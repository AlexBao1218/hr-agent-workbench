"""Explicit permanent deletion of one candidate's app-owned data.

A failed storage cleanup remains blocked and retryable by the same DELETE request.
No background inference or automatic deletion of previously removed candidates.
"""
from pathlib import Path
import shutil
from .store import now


def delete_candidate(store, blobs, cid):
    with store.lock:
        state = store.read()
        if cid not in state['candidates']:
            return cid in state.get('_deletedCandidates', {})
        with store.transaction() as state:
            candidate = state['candidates'][cid]
            if not candidate.get('_deletePending'):
                candidate['generation'] += 1
                candidate.update(removedAt=now(), _deletePending=True)
        state = store.read()
        candidate = state['candidates'][cid]
        did = candidate['documentId']; document = state['documents'][did]
        shared = any(c['id'] != cid and c['documentId'] == did for c in state['candidates'].values())
        owned_runs = []
        for rid, run in state['runs'].items():
            ids = set(run.get('_candidateIds', [])) | set(run.get('_inputs', {}).get('candidateIds', []))
            ids.update(step['candidateId'] for step in run.get('steps', []) if step.get('candidateId'))
            ids.update(a['candidateId'] for a in state['assessments'].values() if a.get('runId') == rid)
            if cid in ids and not (ids - {cid}): owned_runs.append(rid)
        if not shared:
            if blobs is not None:
                keys = [document['blobKey']] + [p['imageBlobKey'] for p in document.get('pages', []) if p.get('imageBlobKey')]
                blobs.delete_many(keys)
            else:
                paths = [document['path']] + [p['imagePath'] for p in document.get('pages', []) if p.get('imagePath')]
                resolved = [Path(p).resolve() for p in paths]
                if any(not p.is_relative_to(store.root) for p in resolved):
                    raise ValueError('Refusing to delete a file outside app storage')
                for path in resolved: path.unlink(missing_ok=True)
            preview = (store.root/'previews'/did).resolve()
            if not preview.is_relative_to(store.root/'previews'):
                raise ValueError('Invalid preview directory')
            if preview.exists(): shutil.rmtree(preview)
        references = store.delete_candidate_diagnostics(cid, owned_runs)
        with store.transaction() as state:
            aids = {aid for aid,a in state['assessments'].items() if a['candidateId'] == cid}
            for aid in aids: state['assessments'].pop(aid)
            for field in ('candidates','chats','feedback'): state[field].pop(cid, None)
            if not shared: state['documents'].pop(did, None)
            for rid,run in list(state['runs'].items()):
                if rid in owned_runs:
                    state['runs'].pop(rid)
                    if state['activeRunId'] == rid: state['activeRunId'] = None
                    continue
                run['steps'] = [s for s in run.get('steps', []) if s.get('candidateId') != cid]
                for key in ('_candidateIds',):
                    if key in run: run[key] = [v for v in run[key] if v != cid]
                if '_generations' in run: run['_generations'].pop(cid, None)
                inputs = run.get('_inputs', {})
                if 'candidateIds' in inputs: inputs['candidateIds'] = [v for v in inputs['candidateIds'] if v != cid]
                inputs.get('generations', {}).pop(cid, None)
                if '_outputAssessmentIds' in run: run['_outputAssessmentIds'] = [v for v in run['_outputAssessmentIds'] if v not in aids]
                if '_diagnosticFiles' in run: run['_diagnosticFiles'] = [v for v in run['_diagnosticFiles'] if v not in references]
            state.setdefault('_deletedCandidates', {})[cid] = now()
        return True
