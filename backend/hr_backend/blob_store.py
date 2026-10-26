"""Immutable document blobs, with a disposable local mirror for private Supabase objects.

REST contract: https://supabase.com/docs/guides/storage/uploads/standard-uploads
and https://supabase.com/docs/guides/storage/serving/downloads .
No environment discovery, public URLs, redirects, or automatic write retries.
"""
from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, urlsplit

import httpx

MAX_BLOB_BYTES = 128 * 1024 * 1024


class BlobStoreError(RuntimeError):
    """Safe to expose: never includes upstream payloads, credentials or URLs."""


class BlobNotFoundError(BlobStoreError, FileNotFoundError):
    pass


class BlobIntegrityError(BlobStoreError):
    pass


def _key(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 1024
            or '\\' in value or '%' in value or ':' in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('Blob key must be a safe relative path')
    return value


def _hash(value: str | None) -> str | None:
    if value is not None and not re.fullmatch(r'[a-fA-F0-9]{64}', value):
        raise ValueError('Expected hash must be a SHA-256 hex digest')
    return value.lower() if value else None


def _limit(value: int | None) -> int:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
        raise ValueError('Byte limit must be a nonnegative integer')
    return MAX_BLOB_BYTES if value is None else min(value, MAX_BLOB_BYTES)


def _read(path: Path, limit: int) -> bytes:
    try:
        with path.open('rb') as stream:
            data = stream.read(limit + 1)
    except FileNotFoundError:
        raise BlobNotFoundError('Blob is unavailable') from None
    except OSError:
        raise BlobStoreError('Unable to read blob') from None
    if len(data) > limit:
        raise BlobStoreError('Blob exceeds the configured size limit')
    return data


class LocalBlobStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, relative_key: str) -> Path:
        key = _key(relative_key)
        path = self.root
        for part in key.split('/'):
            path = path / part
            if path.is_symlink():
                raise ValueError('Blob paths cannot contain symbolic links')
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('Blob key escapes its root')
        return path

    def _cache(self, key: str, data: bytes, *, immutable: bool = False) -> Path:
        path = self._path(key)
        temp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.blob-', delete=False) as stream:
                temp = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            self._path(key)
            if immutable:
                try:
                    # Publish only complete bytes, without overwriting a concurrent writer.
                    os.link(temp, path)
                except FileExistsError:
                    if _read(path, MAX_BLOB_BYTES) != data:
                        raise BlobIntegrityError('Immutable blob key already contains different data') from None
            else:
                os.replace(temp, path)
            return path
        except OSError:
            raise BlobStoreError('Unable to cache blob') from None
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def put_file(self, relative_key: str, source_path: str | Path) -> None:
        self._path(relative_key)
        data = _read(Path(source_path), MAX_BLOB_BYTES)
        self.put_bytes(relative_key, data, mimetypes.guess_type(str(source_path))[0])

    def put_bytes(self, relative_key: str, data: bytes, mime: str | None = None) -> None:
        self._path(relative_key)
        if len(data) > MAX_BLOB_BYTES:
            raise BlobStoreError('Blob exceeds the configured size limit')
        self._cache(relative_key, data, immutable=True)

    def materialize(self, relative_key: str, expected_hash: str | None = None) -> Path:
        expected_hash = _hash(expected_hash)
        path = self._path(relative_key)
        data = _read(path, MAX_BLOB_BYTES)
        if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
            raise BlobIntegrityError('Blob checksum does not match recorded source')
        return path

    def read_bytes(self, relative_key: str, max_bytes: int | None = None) -> bytes:
        return _read(self._path(relative_key), _limit(max_bytes))

    def exists(self, relative_key: str) -> bool:
        return self._path(relative_key).is_file()

    def delete_many(self, keys: list[str]) -> None:
        paths = [self._path(key) for key in keys]
        for path in paths:
            path.unlink(missing_ok=True)


class SupabaseBlobStore(LocalBlobStore):
    """Backend-only private bucket client. Root contains replaceable cache files.

    A successful put means the remote write was acknowledged, or an existing
    remote object was downloaded and verified byte-for-byte. The constructor
    does not create a bucket: provisioning must create it with public=false.
    """
    def __init__(self, root: str | Path, url: str, secret: str, bucket: str,
                 *, transport: httpx.BaseTransport | None = None):
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise ValueError('Supabase URL must be an HTTPS project origin')
        if not secret or '\n' in secret or '\r' in secret:
            raise ValueError('Supabase backend key is required')
        _key(bucket)
        if '/' in bucket:
            raise ValueError('Bucket must be a single path segment')
        super().__init__(root)
        self._base = url.rstrip('/') + '/storage/v1/object/'
        self._bucket = quote(bucket, safe='')
        self._client = httpx.Client(
            headers={'Authorization': f'Bearer {secret}', 'apikey': secret},
            timeout=httpx.Timeout(60.0, connect=10.0, pool=10.0),
            follow_redirects=False, transport=transport, trust_env=False,
        )

    def close(self) -> None:
        self._client.close()

    def validate_bucket(self) -> None:
        """Fail closed at startup unless the configured bucket is explicitly private."""
        url = self._base.removesuffix('object/') + 'bucket/' + self._bucket
        try:
            response = self._client.get(url)
            if response.status_code != 200:
                raise BlobStoreError('Unable to verify private storage bucket')
            metadata = response.json()
            if not isinstance(metadata, dict) or metadata.get('public') is not False:
                raise BlobStoreError('Storage bucket must be private')
        except (httpx.HTTPError, ValueError):
            raise BlobStoreError('Unable to verify private storage bucket') from None

    def _url(self, key: str, *, download: bool = False) -> str:
        return self._base + ('authenticated/' if download else '') + self._bucket + '/' + quote(_key(key), safe='/')

    def _download(self, key: str, limit: int) -> bytes:
        try:
            with self._client.stream('GET', self._url(key, download=True)) as response:
                if response.status_code == 404:
                    raise BlobNotFoundError('Remote blob is unavailable')
                if response.status_code != 200:
                    raise BlobStoreError('Private blob download failed')
                data = bytearray()
                for chunk in response.iter_bytes():
                    if len(data) + len(chunk) > limit:
                        raise BlobStoreError('Blob exceeds the configured size limit')
                    data.extend(chunk)
                return bytes(data)
        except httpx.HTTPError:
            raise BlobStoreError('Private blob download failed; check storage connectivity and configuration') from None

    def put_bytes(self, relative_key: str, data: bytes, mime: str | None = None) -> None:
        self._path(relative_key)
        if len(data) > MAX_BLOB_BYTES:
            raise BlobStoreError('Blob exceeds the configured size limit')
        if mime is not None and (not mime.isascii() or any(ord(c) < 32 for c in mime)):
            raise ValueError('Invalid blob content type')
        try:
            response = self._client.post(self._url(relative_key), content=data, headers={
                'Content-Type': mime or 'application/octet-stream', 'x-upsert': 'false',
            })
        except httpx.HTTPError:
            raise BlobStoreError('Private blob upload failed; check storage connectivity and configuration') from None
        duplicate = response.status_code == 409
        if response.status_code == 400:
            try:
                error = response.json()
                duplicate = isinstance(error, dict) and (
                    error.get('error') in ('Duplicate', 'Asset Already Exists')
                    or error.get('code') in ('Duplicate', 'ResourceAlreadyExists')
                    or error.get('message') in ('The resource already exists', 'Asset Already Exists'))
            except ValueError:
                pass
        if duplicate:
            remote = self._download(relative_key, MAX_BLOB_BYTES)
            if remote != data:
                raise BlobIntegrityError('Immutable remote blob key already contains different data')
        elif response.status_code not in (200, 201):
            raise BlobStoreError('Private blob upload failed')
        # Never claim a cache-only write is remotely durable.
        self._cache(relative_key, data)

    def materialize(self, relative_key: str, expected_hash: str | None = None) -> Path:
        expected_hash = _hash(expected_hash)
        path = self._path(relative_key)
        if path.is_file():
            data = _read(path, MAX_BLOB_BYTES)
            if expected_hash is None or hashlib.sha256(data).hexdigest() == expected_hash:
                return path
        data = self._download(relative_key, MAX_BLOB_BYTES)
        if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
            raise BlobIntegrityError('Remote blob checksum does not match recorded source')
        return self._cache(relative_key, data)

    def read_bytes(self, relative_key: str, max_bytes: int | None = None) -> bytes:
        limit = _limit(max_bytes)
        path = self._path(relative_key)
        if path.is_file():
            return _read(path, limit)
        data = self._download(relative_key, limit)
        self._cache(relative_key, data)
        return data

    def exists(self, relative_key: str) -> bool:
        # Do not let a stale cache establish remote durability.
        self._path(relative_key)
        try:
            self._download(relative_key, MAX_BLOB_BYTES)
            return True
        except BlobNotFoundError:
            return False

    def delete_many(self, keys: list[str]) -> None:
        keys = list(dict.fromkeys(_key(key) for key in keys))
        for key in keys: self._path(key)
        for start in range(0, len(keys), 100):
            try:
                response = self._client.request('DELETE', self._base + self._bucket,
                                                json={'prefixes': keys[start:start+100]})
            except httpx.HTTPError:
                raise BlobStoreError('Private file deletion failed; retry deletion after restoring storage connectivity.') from None
            if response.status_code not in (200, 204):
                raise BlobStoreError('Private file deletion failed; retry deletion after restoring storage connectivity.')
        super().delete_many(keys)
