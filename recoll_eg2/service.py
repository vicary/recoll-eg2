from __future__ import annotations

import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from .indexer import hash_file


class SearchService:
    def __init__(self, config, store, recoll, encoder, indexer, token: str = ""):
        self.config, self.store, self.recoll, self.encoder, self.indexer = config, store, recoll, encoder, indexer
        self.token = token
        self.worker_alive = True
        self.worker_failures = 0
        self.operation_lock = threading.RLock()
        self.stop = threading.Event()

    def scan_loop(self):
        try:
            while not self.stop.is_set():
                try:
                    with self.operation_lock: self.indexer.scan()
                    self.worker_failures = 0
                except Exception as exc:
                    self.worker_failures += 1
                    self.store.set_meta("last_error", f"{type(exc).__name__}: {exc}")
                    if self.worker_failures >= 3:
                        self.worker_alive = False
                        self.stop.set()
                        if hasattr(self,"server"): self.server.shutdown()
                        return
                self.stop.wait(self.config.scan_seconds)
        finally:
            self.worker_alive = False

    def search(self, query: str, limit: int) -> dict:
      with self.operation_lock:
        if self.store.meta("scope_status") == "unavailable":
            return {"query":query,"results":[],"scope_status":"unavailable"}
        semantic = self.store.semantic(self.encoder.encode_query(query), limit * 3)
        lexical = self.recoll.search(query, limit * 3)
        def current(hit):
            expected=self.store.visible_hash(hit["path"])
            if not expected: return False
            try: return hash_file(self.config.source,hit["path"],self.config.max_file_bytes)==expected
            except (OSError,ValueError): return False
        semantic=[hit for hit in semantic if current(hit)]
        lexical=[hit for hit in lexical if current(hit)]
        merged = {}
        for rank, hit in enumerate(semantic):
            hit["score"] = .6 * (1/(rank+1))
            merged[(hit["path"], hit["ordinal"])] = hit
        for rank, hit in enumerate(lexical):
            if not self.store.visible_path(hit["path"]): continue
            key = (hit["path"], 0)
            if key in merged:
                merged[key]["score"] += .4 * (1/(rank+1)); merged[key]["lexical"] = True
            else:
                hit.update(score=.4*(1/(rank+1)), lexical=True); merged[key] = hit
        return {"query":query,"results":sorted(merged.values(),key=lambda h:h["score"],reverse=True)[:limit],
                "scope_status":self.store.meta("scope_status")}

    def handler(self):
        app = self
        class Handler(BaseHTTPRequestHandler):
            server_version = "recoll-eg2/0.1"
            def send(self, status, obj):
                body=json.dumps(obj,separators=(",",":"),ensure_ascii=False).encode()
                self.send_response(status); self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(body))); self.send_header("Cache-Control","no-store")
                self.send_header("X-Content-Type-Options","nosniff"); self.end_headers(); self.wfile.write(body)
            def authorized(self):
                if not app.token: return True
                value=self.headers.get("Authorization","")
                return hmac.compare_digest(value, "Bearer "+app.token)
            def do_GET(self):
                parsed=urlparse(self.path)
                if parsed.path == "/health":
                    healthy=app.worker_alive and app.store.meta("scope_status") == "ready"
                    return self.send(200 if healthy else 503,{"healthy":healthy})
                if not self.authorized(): return self.send(401,{"error":"unauthorized"})
                if parsed.path == "/status": return self.send(200,app.store.status())
                if parsed.path != "/search": return self.send(404,{"error":"not found"})
                args=parse_qs(parsed.query,keep_blank_values=True)
                q=args.get("q",[""])[0]
                if not q or len(q)>app.config.max_query_chars: return self.send(400,{"error":"invalid query length"})
                try: limit=int(args.get("limit",["10"])[0])
                except ValueError: return self.send(400,{"error":"invalid limit"})
                if not 1<=limit<=app.config.max_results: return self.send(400,{"error":"limit out of range"})
                try: return self.send(200,app.search(q,limit))
                except Exception: return self.send(503,{"error":"search temporarily unavailable"})
            def do_POST(self): self.send(405,{"error":"read only"})
            def log_message(self, format, *args): pass
        return Handler

    def serve(self):
        worker=threading.Thread(target=self.scan_loop,name="indexer",daemon=False); worker.start()
        # A single request worker is intentional: inference and response concurrency
        # remain bounded on small systems instead of spawning unlimited threads.
        server=HTTPServer((self.config.bind,self.config.port),self.handler()); self.server=server
        try: server.serve_forever(poll_interval=.5)
        finally: self.stop.set(); server.server_close(); worker.join(timeout=10)
