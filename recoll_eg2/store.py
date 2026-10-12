from __future__ import annotations

import json
import math
import sqlite3
import time
import uuid
import threading
from pathlib import Path


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS files(
 path TEXT PRIMARY KEY, content_hash TEXT NOT NULL, generation TEXT,
 visible INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
 retry_count INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
 error TEXT, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS generations(
 id TEXT PRIMARY KEY, path TEXT NOT NULL, content_hash TEXT NOT NULL,
 state TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks(
 generation TEXT NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
 ordinal INTEGER NOT NULL, text TEXT NOT NULL, vector TEXT NOT NULL,
 PRIMARY KEY(generation, ordinal)
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS chunks_generation ON chunks(generation);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=20, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript(SCHEMA)

    def close(self):
        with self.lock: self.db.close()

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value)); self.db.commit()

    def meta(self, key: str, default: str = "") -> str:
        with self.lock: row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def known(self) -> dict[str, sqlite3.Row]:
        with self.lock: return {r["path"]: r for r in self.db.execute("SELECT * FROM files")}

    def invalidate(self, path: str, content_hash: str, reason: str = "pending") -> None:
        """Make stale lexical and semantic hits invisible before any new extraction."""
        now = time.time()
        with self.lock, self.db:
            self.db.execute("UPDATE generations SET state='retired' WHERE path=?", (path,))
            self.db.execute("""INSERT INTO files(path,content_hash,generation,visible,status,updated_at)
              VALUES(?,?,NULL,0,?,?) ON CONFLICT(path) DO UPDATE SET content_hash=excluded.content_hash,
              generation=NULL,visible=0,status=excluded.status,error=NULL,updated_at=excluded.updated_at""",
              (path, content_hash, reason, now))

    def fail(self, path: str, error: str) -> None:
        now = time.time()
        with self.lock, self.db:
            row = self.db.execute("SELECT retry_count FROM files WHERE path=?", (path,)).fetchone()
            retries = min((row[0] if row else 0) + 1, 8)
            delay = min(3600, 5 * (2 ** (retries - 1)))
            status = "quarantine" if retries >= 8 else "retry"
            self.db.execute("UPDATE files SET visible=0,status=?,retry_count=?,retry_at=?,error=?,updated_at=? WHERE path=?",
                            (status, retries, now + delay, error[:500], now, path))

    def publish(self, path: str, content_hash: str, chunks: list[tuple[str, list[float]]], current_hash) -> bool:
        """Atomically publish a complete generation iff source revision is still current."""
        generation = uuid.uuid4().hex
        now = time.time()
        with self.lock, self.db:
            row = self.db.execute("SELECT content_hash,status FROM files WHERE path=?", (path,)).fetchone()
            if not row or row[0] != content_hash or current_hash() != content_hash:
                return False
            self.db.execute("INSERT INTO generations VALUES(?,?,?,?,?)", (generation, path, content_hash, "staging", now))
            self.db.executemany("INSERT INTO chunks VALUES(?,?,?,?)",
                ((generation, i, text, json.dumps(vector, separators=(",", ":"))) for i, (text, vector) in enumerate(chunks)))
            self.db.execute("UPDATE generations SET state='retired' WHERE path=? AND id<>?", (path, generation))
            self.db.execute("UPDATE generations SET state='visible' WHERE id=?", (generation,))
            self.db.execute("UPDATE files SET generation=?,visible=1,status='ready',retry_count=0,retry_at=0,error=NULL,updated_at=? WHERE path=? AND content_hash=?",
                            (generation, now, path, content_hash))
            changed = self.db.execute("SELECT changes()").fetchone()[0]
            if not changed:
                raise RuntimeError("source revision changed while publishing")
            self.db.execute("DELETE FROM generations WHERE path=? AND id<>?", (path, generation))
        return True

    def purge(self, path: str) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM files WHERE path=?", (path,))
            self.db.execute("DELETE FROM generations WHERE path=?", (path,))

    def recover(self) -> None:
        with self.lock, self.db:
            self.db.execute("DELETE FROM generations WHERE state='staging'")
            self.db.execute("DELETE FROM chunks WHERE generation NOT IN (SELECT id FROM generations)")

    def visible_path(self, path: str) -> bool:
        with self.lock: row = self.db.execute("SELECT visible FROM files WHERE path=?", (path,)).fetchone()
        return bool(row and row[0])

    def visible_hash(self, path: str) -> str | None:
        with self.lock:
            row=self.db.execute("SELECT content_hash FROM files WHERE path=? AND visible=1 AND status='ready'",(path,)).fetchone()
        return row[0] if row else None

    def semantic(self, query: list[float], limit: int) -> list[dict]:
        qnorm = math.sqrt(sum(x*x for x in query)) or 1.0
        found = []
        sql = """SELECT f.path,c.ordinal,c.text,c.vector,g.id generation FROM files f
          JOIN generations g ON g.id=f.generation JOIN chunks c ON c.generation=g.id
          WHERE f.visible=1 AND f.status='ready' AND g.state='visible'"""
        with self.lock: rows=list(self.db.execute(sql))
        for row in rows:
            vec = json.loads(row["vector"])
            if len(vec) != len(query):
                continue
            norm = math.sqrt(sum(x*x for x in vec)) or 1.0
            score = sum(a*b for a,b in zip(query,vec))/(qnorm*norm)
            found.append({"path":row["path"],"ordinal":row["ordinal"],"snippet":row["text"][:500],"generation":row["generation"],"semantic_score":score})
        return sorted(found, key=lambda x:x["semantic_score"], reverse=True)[:limit]

    def status(self) -> dict:
        with self.lock: counts = {r[0]: r[1] for r in self.db.execute("SELECT status,count(*) FROM files GROUP BY status")}
        return {"files": counts, "scope_status": self.meta("scope_status", "never_scanned"),
                "last_scan": self.meta("last_scan", ""), "last_error": self.meta("last_error", "")}
