"""Content-free PostgreSQL call accounting; reservations commit before inference.

Uses the workspace store's connection and transaction lock. No reconnection,
local fallback, or retry is performed after an uncertain database outcome.
"""
from collections.abc import Mapping
from uuid import uuid4

from .provider import ProviderError


FIELDS = ('inputTokens', 'outputTokens', 'cacheHitTokens', 'cacheMissTokens')


def _count(value):
    # PostgreSQL bigint range; malformed provider accounting remains unknown.
    return value if type(value) is int and 0 <= value <= 9223372036854775807 else None


def normalize_usage(usage):
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get('input_tokens_details')
    details = details if isinstance(details, dict) else {}
    prompt = _count(usage.get('input_tokens'))
    output = _count(usage.get('output_tokens'))
    hit = _count(details.get('cached_tokens'))
    miss = max(0, prompt - hit) if prompt is not None and hit is not None else None
    return dict(zip(FIELDS, (prompt, output, hit, miss)))


class PostgresUsageLedger:
    def __init__(self, store):
        self.store = store

    def reserve(self, role, run_id, max_calls):
        call_id = uuid4().hex
        with self.store.sql_transaction(require_top_level=True) as cursor:
            # A shared database row serializes budget checks across app instances.
            cursor.execute('SELECT id FROM hr_private.model_budget_guard WHERE id=1 FOR UPDATE')
            if cursor.fetchone() is None:
                raise ProviderError('usage_ledger_unavailable', 'Model budget guard is missing. No call sent.')
            cursor.execute('SELECT count(*) AS attempts FROM hr_private.model_calls')
            row = cursor.fetchone()
            attempts = row['attempts'] if isinstance(row, Mapping) else row[0]
            if attempts >= max_calls:
                raise ProviderError('budget_exhausted', 'Model call limit reached. Review the usage ledger before explicitly increasing HR_MODEL_MAX_CALLS.')
            cursor.execute('INSERT INTO hr_private.model_calls (id,role,run_id,status,started_at) VALUES (%s,%s,%s,%s,CURRENT_TIMESTAMP)',
                           (call_id, role, run_id, 'attempted'))
        # Returning only outside the transaction guarantees commit acknowledgement.
        return call_id

    def record(self, call_id, status, usage):
        if status not in ('failed', 'incomplete', 'invalid_output', 'succeeded'):
            raise ValueError('Unsupported model call status.')
        values = tuple(_count(usage.get(name)) for name in FIELDS)
        with self.store.sql_transaction() as cursor:
            cursor.execute('UPDATE hr_private.model_calls SET status=%s,completed_at=CURRENT_TIMESTAMP,input_tokens=%s,output_tokens=%s,cache_hit_tokens=%s,cache_miss_tokens=%s WHERE id=%s',
                           (status, *values, call_id))
            if cursor.rowcount != 1:
                raise ProviderError('usage_ledger_unavailable', 'Model reservation is missing; no automatic retry.')

    def summary(self):
        with self.store.sql_transaction() as cursor:
            cursor.execute('SELECT count(*) AS attempts,coalesce(sum(input_tokens),0) AS input_tokens,coalesce(sum(output_tokens),0) AS output_tokens,coalesce(sum(cache_hit_tokens),0) AS cache_hit_tokens,coalesce(sum(cache_miss_tokens),0) AS cache_miss_tokens,coalesce(sum(CASE WHEN input_tokens IS NULL OR output_tokens IS NULL THEN 1 ELSE 0 END),0) AS unknown_usage_calls FROM hr_private.model_calls')
            row = cursor.fetchone()
        columns = ('attempts', 'input_tokens', 'output_tokens', 'cache_hit_tokens', 'cache_miss_tokens', 'unknown_usage_calls')
        values = [row[name] for name in columns] if isinstance(row, Mapping) else row
        return dict(zip(('attempts', *FIELDS, 'unknownUsageCalls'), map(int, values)))
