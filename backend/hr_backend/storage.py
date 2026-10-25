"""Explicit storage selection; remote failures never fall back to local state."""
import os
from pathlib import Path
from .store import Store


def create_storage(root, environ=None):
    env = os.environ if environ is None else environ
    mode = env.get('HR_STORAGE_BACKEND', 'local')
    if mode == 'local':
        return Store(root), None
    if mode != 'supabase':
        raise RuntimeError('HR_STORAGE_BACKEND must be local or supabase')
    names = ('HR_DATABASE_URL', 'HR_SUPABASE_URL', 'HR_SUPABASE_SECRET_KEY', 'HR_STORAGE_BUCKET')
    if any(not env.get(name, '').strip() for name in names):
        raise RuntimeError('Supabase storage requires HR_DATABASE_URL, HR_SUPABASE_URL, HR_SUPABASE_SECRET_KEY and HR_STORAGE_BUCKET')
    from .postgres_store import PostgresStore
    from .blob_store import SupabaseBlobStore
    blobs = SupabaseBlobStore(root, env['HR_SUPABASE_URL'], env['HR_SUPABASE_SECRET_KEY'], env['HR_STORAGE_BUCKET'])
    try:
        blobs.validate_bucket()
        store = PostgresStore(root, env['HR_DATABASE_URL'])
    except BaseException:
        blobs.close()
        raise
    return store, blobs
