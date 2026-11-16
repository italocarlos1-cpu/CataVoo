# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistent analysis cache (SQLite) — analyse each clip once.

What is stored per clip: metadata, the per-sample timeline (every measurement
the scorer needs), JPEG thumbnails for CLIP, and CLIP embeddings per model.
Segments and scores are *not* stored as final answers: they are recomputed from
the timeline in milliseconds, so changing the style, the weights or the target
duration never forces the expensive decode again.

Invalidation: a clip's key is ``(size, mtime_ns)`` plus a quick content hash
(first and last MiB). A moved or renamed file is recognised by the content
hash and reused; an edited file gets a new hash and is re-analysed. The
analyser version and sampling parameters are part of the key too.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
import zlib
from typing import Optional

from drone import ANALYZER_VERSION


def default_cache_dir() -> str:
    base = os.environ.get("CATAVOO_CACHE")
    if base:
        return base
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(root, "CataVoo")
    return os.path.join(os.path.expanduser("~"), ".cache", "catavoo")


def quick_hash(path: str, chunk: int = 1 << 20) -> str:
    size = os.path.getsize(path)
    h = hashlib.sha1(str(size).encode())
    with open(path, "rb") as fh:
        h.update(fh.read(chunk))
        if size > 2 * chunk:
            fh.seek(-chunk, os.SEEK_END)
            h.update(fh.read(chunk))
    return h.hexdigest()


def _pack(obj) -> bytes:
    return zlib.compress(json.dumps(obj, separators=(",", ":")).encode("utf-8"), 6)


def _unpack(blob: bytes):
    return json.loads(zlib.decompress(blob).decode("utf-8"))


class AnalysisCache:
    def __init__(self, path: str = ""):
        if not path:
            d = default_cache_dir()
            os.makedirs(d, exist_ok=True)
            path = os.path.join(d, "analysis.sqlite")
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS clips (
                content_hash TEXT NOT NULL,
                params TEXT NOT NULL,
                path TEXT, size INTEGER, mtime_ns INTEGER,
                created REAL, meta BLOB, timeline BLOB,
                PRIMARY KEY (content_hash, params));
            CREATE TABLE IF NOT EXISTS thumbs (
                content_hash TEXT, t REAL, jpeg BLOB,
                PRIMARY KEY (content_hash, t));
            CREATE TABLE IF NOT EXISTS embeddings (
                content_hash TEXT, model TEXT, data BLOB,
                PRIMARY KEY (content_hash, model));
            CREATE TABLE IF NOT EXISTS music (
                content_hash TEXT PRIMARY KEY, path TEXT, data BLOB);
            CREATE INDEX IF NOT EXISTS clips_path ON clips(path);
        """)
        self._db.commit()
        self._hash_memo: dict = {}

    # -- identity -------------------------------------------------------
    def content_key(self, path: str) -> str:
        st = os.stat(path)
        memo_key = (os.path.abspath(path), st.st_size, st.st_mtime_ns)
        if memo_key not in self._hash_memo:
            # Fast path: same path, size and mtime as a cached row.
            with self._lock:
                row = self._db.execute(
                    "SELECT content_hash FROM clips WHERE path=? AND size=? AND mtime_ns=?",
                    (memo_key[0], st.st_size, st.st_mtime_ns)).fetchone()
            self._hash_memo[memo_key] = row[0] if row else quick_hash(path)
        return self._hash_memo[memo_key]

    @staticmethod
    def params_key(params: dict) -> str:
        p = dict(params)
        p["analyzer_version"] = ANALYZER_VERSION
        return json.dumps(p, sort_keys=True)

    # -- timelines ------------------------------------------------------
    def get(self, path: str, params: dict) -> Optional[dict]:
        key = self.content_key(path)
        with self._lock:
            row = self._db.execute(
                "SELECT meta, timeline FROM clips WHERE content_hash=? AND params=?",
                (key, self.params_key(params))).fetchone()
        if not row:
            return None
        meta = _unpack(row[0])
        # The row may have been written for a copy or the file's old name: the
        # analysis is reused, the identity is always the file asked about.
        meta["path"] = path
        meta["name"] = os.path.basename(path)
        meta["timeline"] = _unpack(row[1])
        meta["content_hash"] = key
        return meta

    def put(self, path: str, params: dict, meta: dict, timeline: dict) -> None:
        key = self.content_key(path)
        st = os.stat(path)
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO clips VALUES (?,?,?,?,?,?,?,?)",
                (key, self.params_key(params), os.path.abspath(path), st.st_size,
                 st.st_mtime_ns, time.time(), _pack(meta), _pack(timeline)))
            self._db.commit()

    # -- thumbnails / embeddings ---------------------------------------
    def put_thumbs(self, key: str, items: list[tuple[float, bytes]]) -> None:
        with self._lock:
            self._db.executemany("INSERT OR REPLACE INTO thumbs VALUES (?,?,?)",
                                 [(key, float(t), j) for t, j in items])
            self._db.commit()

    def get_thumbs(self, key: str) -> list[tuple[float, bytes]]:
        with self._lock:
            return list(self._db.execute(
                "SELECT t, jpeg FROM thumbs WHERE content_hash=? ORDER BY t", (key,)))

    def put_embeddings(self, key: str, model: str, data: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO embeddings VALUES (?,?,?)",
                             (key, model, _pack(data)))
            self._db.commit()

    def get_embeddings(self, key: str, model: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT data FROM embeddings WHERE content_hash=? AND model=?",
                (key, model)).fetchone()
        return _unpack(row[0]) if row else None

    # -- music ----------------------------------------------------------
    def get_music(self, path: str) -> Optional[dict]:
        key = self.content_key(path)
        with self._lock:
            row = self._db.execute("SELECT data FROM music WHERE content_hash=?",
                                   (key + f":v{ANALYZER_VERSION}",)).fetchone()
        return _unpack(row[0]) if row else None

    def put_music(self, path: str, data: dict) -> None:
        key = self.content_key(path)
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO music VALUES (?,?,?)",
                             (key + f":v{ANALYZER_VERSION}", os.path.abspath(path), _pack(data)))
            self._db.commit()

    def stats(self) -> dict:
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
            m = self._db.execute("SELECT COUNT(*) FROM music").fetchone()[0]
        return {"clips": n, "music": m, "path": self.path}

    def close(self) -> None:
        with self._lock:
            self._db.close()
