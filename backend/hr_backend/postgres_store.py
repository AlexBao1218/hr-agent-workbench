"""Explicitly bootstrapped PostgreSQL persistence, with one session writer.

Use a direct or session-pooled TLS connection: transaction pooling cannot preserve
our lifetime advisory lock. No connection retry or local persistence fallback.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import threading
from contextlib import contextmanager
from uuid import uuid4

from .store import RULES, version


class RemoteStoreError(RuntimeError):
    pass


class WriterBusyError(RemoteStoreError):
    """The session lock is held by a prior instance; no state was changed."""


class PostgresStore:
    is_remote = True
    WRITER_LOCK = 0x485241495753
    MAX_DIAGNOSTIC_BYTES = 4 * 1024 * 1024

    def __init__(self, root, database_url):
        self.root = Path(root)
        self.lock = threading.RLock()
        self.db = None
        self._state = None
        self._sql_depth = 0
        self._rollback_only = False
        try:
            import psycopg
            from psycopg.conninfo import conninfo_to_dict
        except ImportError:
            raise RemoteStoreError('Remote storage requires psycopg; install the backend remote dependencies.') from None
        self._database_error = psycopg.Error
        try:
            options = conninfo_to_dict(database_url)
            if options.get('sslmode') not in ('require', 'verify-ca', 'verify-full'):
                raise RemoteStoreError('Remote database DSN must explicitly require TLS (sslmode=require or verify-full).')
            self.db = psycopg.connect(database_url, autocommit=True, connect_timeout=10,
                                      options='-c statement_timeout=30000 -c lock_timeout=10000',
                                      tcp_user_timeout=30000)
            if not self.db.execute('SELECT pg_try_advisory_lock(%s)', (self.WRITER_LOCK,)).fetchone()[0]:
                raise WriterBusyError('Remote workspace already has an active writer. Waiting for graceful handoff is required.')
            # Deliberately never creates schema. Bootstrap is a separate explicit step.
            with self.sql_transaction() as cursor:
                cursor.execute('SELECT body FROM hr_private.workspace WHERE id=1 FOR UPDATE')
                if cursor.fetchone() is None:
                    hat = {'version': version(1, 'initial configuration'), 'rules': [
                        {'id': i, 'title': t, 'text': s, 'enabled': True} for i, t, s in RULES]}
                    state = dict(standards=None, draftStandards=None, hat=hat, draftHat=None,
                                 standardsHistory=[], hats=[hat], candidates={}, documents={},
                                 assessments={}, runs={}, chats={}, feedback={}, activeRunId=None,
                                 standardsGeneration=0)
                    cursor.execute('INSERT INTO hr_private.workspace(id,body,revision) VALUES (1,%s::jsonb,0)',
                                   (json.dumps(state, ensure_ascii=False),))
        except RemoteStoreError:
            self.close()
            raise
        except Exception:
            self.close()
            raise RemoteStoreError('Remote database initialization failed. Check TLS, session connection access and the explicit bootstrap schema; no local fallback was used.') from None

    def _available(self):
        if self.db is None or self.db.closed:
            raise RemoteStoreError('Remote storage connection is unavailable. Restart after restoring database access; no reconnect or local fallback is permitted.')

    @contextmanager
    def sql_transaction(self, *, require_top_level=False):
        """Serialized cursor; nested scopes are savepoints on the same connection."""
        with self.lock:
            self._available()
            if require_top_level and self._sql_depth:
                raise RemoteStoreError('A durable model reservation requires a separate top-level transaction.')
            self._sql_depth += 1
            try:
                with self.db.transaction():
                    with self.db.cursor() as cursor:
                        yield cursor
            except self._database_error:
                self.close()
                raise RemoteStoreError('Remote storage operation failed. Restart after checking database access; no local fallback was used.') from None
            finally:
                self._sql_depth -= 1

    def read(self):
        with self.lock:
            self._available()
            if self._state is not None:
                return copy.deepcopy(self._state)
            with self.sql_transaction() as cursor:
                cursor.execute('SELECT body FROM hr_private.workspace WHERE id=1')
                row = cursor.fetchone()
                if row is None:
                    raise RemoteStoreError('Remote workspace row is missing; restore it before restarting.')
                return copy.deepcopy(row[0])

    @contextmanager
    def transaction(self):
        with self.lock:
            self._available()
            if self._state is not None:
                try:
                    yield self._state
                except BaseException:
                    self._rollback_only = True
                    raise
                return
            with self.sql_transaction() as cursor:
                cursor.execute('SELECT body FROM hr_private.workspace WHERE id=1 FOR UPDATE')
                row = cursor.fetchone()
                if row is None:
                    raise RemoteStoreError('Remote workspace row is missing; restore it before restarting.')
                self._state = copy.deepcopy(row[0])
                self._rollback_only = False
                try:
                    yield self._state
                    if self._rollback_only:
                        raise RemoteStoreError('Nested workspace transaction failed; all changes rolled back.')
                    cursor.execute('UPDATE hr_private.workspace SET body=%s::jsonb, revision=revision+1 WHERE id=1',
                                   (json.dumps(self._state, ensure_ascii=False),))
                finally:
                    self._state = None
                    self._rollback_only = False

    @staticmethod
    def _validate_reference(reference):
        if not isinstance(reference, str) or not re.fullmatch(r'diagnostics/[A-Za-z0-9_-]{1,160}\.json', reference):
            raise ValueError('Invalid diagnostic reference.')

    def write_diagnostic(self, artifact):
        run_id = artifact.get('runId')
        if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
            raise ValueError('Invalid diagnostic run ID.')
        reference = 'diagnostics/' + run_id + '-' + uuid4().hex + '.json'
        content = json.dumps(artifact, ensure_ascii=False).encode('utf-8')
        if len(content) > self.MAX_DIAGNOSTIC_BYTES:
            raise ValueError('Diagnostic exceeds the 4 MiB storage limit.')
        with self.sql_transaction() as cursor:
            cursor.execute('INSERT INTO hr_private.diagnostics(reference,content,sha256,run_id) VALUES (%s,%s,%s,%s)',
                           (reference, content, hashlib.sha256(content).hexdigest(), run_id))
        return reference

    def read_diagnostic(self, reference):
        self._validate_reference(reference)
        with self.sql_transaction() as cursor:
            cursor.execute('SELECT content,sha256 FROM hr_private.diagnostics WHERE reference=%s AND octet_length(content)<=%s',
                           (reference, self.MAX_DIAGNOSTIC_BYTES))
            row = cursor.fetchone()
            if row is None:
                raise FileNotFoundError('Indexed diagnostic is unavailable.')
            content = bytes(row[0])
            if hashlib.sha256(content).hexdigest() != row[1]:
                raise RemoteStoreError('Diagnostic integrity verification failed.')
            return content

    def close(self):
        with self.lock:
            if self.db is not None:
                try:
                    self.db.close()
                except Exception:
                    pass
                finally:
                    self.db = None

    def delete_candidate_diagnostics(self, candidate_id, owned_run_ids):
        with self.sql_transaction() as cursor:
            cursor.execute("DELETE FROM hr_private.diagnostics WHERE run_id = ANY(%s) OR convert_from(content,'UTF8')::jsonb->>'candidateId' = %s RETURNING reference",
                           (owned_run_ids, candidate_id))
            return {row[0] for row in cursor.fetchall()}
