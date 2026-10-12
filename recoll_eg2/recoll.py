from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse


class Recoll:
    """Real Recoll crawler/bindings, isolated from the API and original sources."""
    def __init__(self, state: Path, source: Path, timeout: int):
        self.conf = state / "recoll"
        self.timeout = timeout
        self.stage = None
        self.names = {}
        self.lock = threading.RLock()

    def configure(self, stage: Path, names: dict[str, str]):
        with self.lock:
            self.conf.mkdir(parents=True, exist_ok=True)
            (self.conf / "tmp").mkdir(exist_ok=True)
            self.stage, self.names = stage, dict(names)
            if any(c in str(stage) for c in '\r\n"\\'):
                raise ValueError("unsupported state path characters")
            content = (f'topdirs = "{stage}"\nfollowLinks = 0\nprocesswebqueue = 0\n'
                       'compressedfilemaxkbs = 0\nthrQSizes = -1 -1 -1\n'
                       'indexedmimetypes = text/plain text/html application/pdf application/rtf text/rtf text/xml application/xml\n'
                       'idxflushmb = 32\nloglevel = 2\n')
            (self.conf / "recoll.conf").write_text(content, encoding="utf-8")

    def _worker(self, request, max_output, timeout):
        request.update(conf=str(self.conf), max_output=max_output, cpu_seconds=max(1, timeout))
        env = {**os.environ, "RECOLL_CONFDIR": str(self.conf),
               "RECOLL_TMPDIR": str(self.conf / "tmp"), "TMPDIR": str(self.conf / "tmp")}
        with tempfile.TemporaryFile(dir=self.conf / "tmp") as output:
            proc = subprocess.Popen([sys.executable, "-m", "recoll_eg2.recoll_worker"],
                                    stdin=subprocess.PIPE, stdout=output, stderr=subprocess.DEVNULL,
                                    env=env, start_new_session=True)
            try:
                proc.communicate(json.dumps(request).encode(), timeout=timeout)
            except BaseException:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=5)
                raise
            if proc.returncode:
                raise RuntimeError(f"Recoll {request['action']} worker exited {proc.returncode}")
            if request["action"] == "index":
                return None
            output.seek(0)
            data = output.read(max_output + 1)
            if len(data) > max_output:
                raise RuntimeError("Recoll worker exceeded output bound")
            return json.loads(data)

    def index(self):
        with self.lock:
            self._worker({"action": "index"}, 512 * 1024**2, self.timeout)

    def extract_text(self, staged_name, max_chars):
        with self.lock:
            result = self._worker({"action": "extract", "name": staged_name,
                                   "query": f'filename:"{staged_name}"', "max_chars": max_chars},
                                  max_chars * 6 + 1024, min(self.timeout, 120))
            return result["text"]

    def search(self, query_text, limit):
        with self.lock:
            records = self._worker({"action": "search", "query": query_text, "limit": limit},
                                   limit * 10000 + 4096, min(self.timeout, 30))
            hits = []
            for doc in records:
                parsed = urlparse(doc["url"])
                safe = Path(unquote(parsed.path)).name if parsed.scheme == "file" else ""
                original = self.names.get(safe)
                if original:
                    hits.append({"path": original, "title": doc["title"], "snippet": doc["snippet"]})
            return hits

    def release_stage(self, stage):
        shutil.rmtree(stage, ignore_errors=True)
