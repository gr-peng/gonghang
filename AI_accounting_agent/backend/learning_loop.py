"""Opt-in structured corrections. No raw prompt, account, name or precise amount.

Approval is an explicit owner review. Export/training/release are offline tools;
neither a conversation nor this API can deploy a model or change bank policy.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import time
from uuid import uuid4

from fastapi import HTTPException
from finance_schema import intent_slots


class LearningLoop:
    def __init__(self, store, root: Path):
        self.store, self.root = store, root
        with store._atomic():
            for statement in (
                'CREATE TABLE IF NOT EXISTS app_learning (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL)',
                'INSERT OR IGNORE INTO app_learning VALUES (1,0)',
                'CREATE TABLE IF NOT EXISTS app_draft_feedback (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, slots TEXT NOT NULL, model_version TEXT NOT NULL, expires REAL NOT NULL)',
                'CREATE TABLE IF NOT EXISTS app_feedback (id TEXT PRIMARY KEY, draft_ref TEXT NOT NULL UNIQUE, before_slots TEXT NOT NULL, after_slots TEXT NOT NULL, model_version TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, reviewed REAL)',
            ):
                store._db.execute(statement)

    def enabled(self) -> bool:
        with self.store._lock:
            return bool(self.store._db.execute('SELECT enabled FROM app_learning WHERE id=1').fetchone()[0])

    def consent(self, enabled: bool):
        with self.store._atomic():
            self.store._db.execute('UPDATE app_learning SET enabled=? WHERE id=1', (int(enabled),))
            if not enabled:
                self.store._db.execute('DELETE FROM app_draft_feedback')
                self.store._db.execute("DELETE FROM app_feedback WHERE status IN ('candidate','approved','rejected')")
        return self.status()

    def remember_draft(self, sid: str, draft: dict) -> str | None:
        if not self.enabled():
            return None
        handle = secrets.token_urlsafe(24)
        with self.store._atomic():
            self.store._db.execute('DELETE FROM app_draft_feedback WHERE expires<?', (time.time(),))
            self.store._db.execute('INSERT INTO app_draft_feedback VALUES (?,?,?,?,?)',
                (handle, sid, json.dumps(intent_slots(draft), sort_keys=True), os.getenv('LLM_MODEL','local'), time.time()+1800))
        return handle

    def capture(self, sid: str, ref: str | None, corrected: dict):
        if not ref or not self.enabled():
            return
        with self.store._atomic():
            row = self.store._db.execute('SELECT * FROM app_draft_feedback WHERE id=? AND session_id=? AND expires>?',
                                        (ref, sid, time.time())).fetchone()
            if row is None:
                return  # absent/forged/expired feedback can never affect a bank operation
            before, after = json.loads(row['slots']), intent_slots(corrected)
            if before == after:
                return
            self.store._db.execute('INSERT OR IGNORE INTO app_feedback VALUES (?,?,?,?,?,?,?,NULL)',
                (str(uuid4()), ref, json.dumps(before, sort_keys=True), json.dumps(after, sort_keys=True),
                 row['model_version'], 'candidate', time.time()))

    def review(self, handle: str, approve: bool):
        with self.store._atomic():
            row=self.store._db.execute('SELECT * FROM app_feedback WHERE id=?', (handle,)).fetchone()
            if row is None:
                raise HTTPException(404, '反馈不存在')
            if row['status'] not in {'candidate','approved','rejected'}:
                raise HTTPException(409, '反馈已进入训练记录，不能修改既有模型')
            if approve and not self.enabled():
                raise HTTPException(409, '请先选择加入改进计划')
            self.store._db.execute('UPDATE app_feedback SET status=?,reviewed=? WHERE id=?',
                ('approved' if approve else 'rejected', time.time(), handle))
        return self.status()

    def delete(self, handle: str):
        with self.store._atomic():
            row=self.store._db.execute('SELECT status FROM app_feedback WHERE id=?', (handle,)).fetchone()
            if row and row['status'] == 'consumed':
                raise HTTPException(409, '这条反馈已用于训练；移除其影响需要重训并发布新版本')
            self.store._db.execute('DELETE FROM app_feedback WHERE id=?', (handle,))
        return self.status()

    def status(self) -> dict:
        with self.store._lock:
            counts={row[0]:row[1] for row in self.store._db.execute('SELECT status,COUNT(*) FROM app_feedback GROUP BY status')}
            rows=self.store._db.execute('SELECT * FROM app_feedback ORDER BY created DESC LIMIT 30').fetchall()
        # Only a signed-off offline release report is shown; no fabricated metrics.
        release_path=self.root/'.runtime/model-releases/current.json'
        release=json.loads(release_path.read_text()) if release_path.exists() else {'version':os.getenv('LLM_MODEL','local'),'evaluation':None}
        return {'enabled':self.enabled(), 'counts':counts,
                'samples':[{'id':r['id'],'before':json.loads(r['before_slots']),'after':json.loads(r['after_slots']),
                            'status':r['status'],'model_version':r['model_version']} for r in rows],
                'model':{k:release[k] for k in ('version','released_at','evaluation','training_source','feedback_rows','feedback_provenance') if k in release}}
