import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

def uid(): return uuid4().hex
def now(): return datetime.now(timezone.utc).isoformat()
def version(number=0, actor=None):
    return {'id':uid(), 'number':number, 'confirmedAt':now() if actor else None, 'confirmedBy':actor}

RULES = [
 ('confirmed', '仅使用确认标准', 'Only HR-confirmed criteria; no added standards or thresholds.'),
 ('evidence', '结论有据可查', 'Claims trace to sources; missing evidence is unknown.'),
 ('advice', '仅提供建议', 'Advice only; never contact, reject, offer or decide employment.'),
 ('scope', '连接要求与证据', 'Connect every JD requirement with CV evidence and its supported scope.'),
 ('latest', '保持版本一致', 'Use one latest confirmed HAT and standards snapshot; invalidate stale conclusions.')]

class Store:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(root/'workspace.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)')
        hat = {'version':version(1, 'initial configuration'), 'rules':[{'id':i,'title':t,'text':s,'enabled':True} for i,t,s in RULES]}
        state = dict(standards=None, draftStandards=None, hat=hat, draftHat=None, standardsHistory=[], hats=[hat], candidates={}, documents={}, assessments={}, runs={}, chats={}, feedback={}, activeRunId=None, standardsGeneration=0)
        self.db.execute('INSERT OR IGNORE INTO state VALUES (1,?)', (json.dumps(state),))
        self.db.commit()
    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            state = json.loads(self.db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
            try:
                yield state
                self.db.execute('UPDATE state SET body=? WHERE id=1', (json.dumps(state, ensure_ascii=False),))
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
    def read(self):
        with self.lock:
            return json.loads(self.db.execute('SELECT body FROM state WHERE id=1').fetchone()[0])
