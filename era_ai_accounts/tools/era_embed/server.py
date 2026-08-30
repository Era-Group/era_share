"""OpenAI-compatible embeddings endpoint backed by a local model.

Exists because the AI accounts on this server sign in through the Claude/codex
CLIs, and those are text-only: they have no embeddings endpoint, so knowledge
sources can never be indexed through them. Rather than buy a second, key-based
subscription just to index documents, embed locally.

Two properties make this a drop-in for `text-embedding-3-small`:

* It speaks the OpenAI ``POST /v1/embeddings`` shape, so Odoo's existing
  ``custom_llm`` provider reaches it with configuration alone.
* ``ai.embedding.embedding_vector`` is a fixed ``Vector(size=1536)`` while
  multilingual-e5-large returns 1024. Zero-padding to the requested width
  leaves cosine similarity *exactly* unchanged — the dot product and both
  norms are untouched by trailing zeros — so the ivfflat cosine index keeps
  working with no schema change. Verified numerically: 0.922363884 before and
  after padding.

The e5 family expects "query: " / "passage: " prefixes; Odoo tags each call
through ``_format_for_embedding``, and anything arriving unprefixed is treated
as a passage, which is the indexing path.
"""
import hmac
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from fastembed import TextEmbedding

HOST = os.environ.get("ERA_EMBED_HOST", "127.0.0.1")
PORT = int(os.environ.get("ERA_EMBED_PORT", "8091"))
MODEL = os.environ.get("ERA_EMBED_MODEL", "intfloat/multilingual-e5-large")
CACHE = os.environ.get("ERA_EMBED_CACHE", "/var/lib/odoo/era_embed/models")
# ONNX Runtime otherwise opens a thread — and a memory arena — per core, which
# on a 20-core host cost ~6.9GB resident for a 2.2GB model. Indexing is a
# one-off; what runs continuously is a single short query per chat turn, and
# that does not need twenty threads.
THREADS = int(os.environ.get("ERA_EMBED_THREADS", "4"))
# Required as soon as HOST is anything but loopback: a shared instance is
# reachable by whatever else is on that network, and an open inference endpoint
# is both a free compute donation and a way to probe what a firm is indexing.
TOKEN = os.environ.get("ERA_EMBED_TOKEN", "")
MAX_BODY = 64 * 1024 * 1024

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
_log = logging.getLogger("era_embed")

_model = None
_lock = threading.Lock()


def model():
    """Load once, on first use — startup should not block on a 2GB read."""
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                _log.info("loading %s (threads=%s)", MODEL, THREADS)
                _model = TextEmbedding(MODEL, cache_dir=CACHE, threads=THREADS)
                _log.info("model ready")
    return _model


def encode(texts, dimensions):
    prepared = [
        t if t.startswith(("query:", "passage:")) else f"passage: {t}"
        for t in texts
    ]
    vectors = list(model().embed(prepared))
    out = []
    for v in vectors:
        v = np.asarray(v, dtype=np.float32)
        if dimensions and dimensions > v.shape[0]:
            v = np.concatenate([v, np.zeros(dimensions - v.shape[0], dtype=np.float32)])
        elif dimensions and dimensions < v.shape[0]:
            # Truncating changes the vector; refuse rather than silently degrade.
            raise ValueError(
                f"model returns {v.shape[0]} dimensions, cannot serve {dimensions}")
        out.append([float(x) for x in v])
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        _log.info("%s - %s", self.address_string(), fmt % args)

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self):
        if not TOKEN:
            return True
        header = self.headers.get("Authorization") or ""
        presented = header[7:] if header.startswith("Bearer ") else header
        # compare_digest, not ==, so a wrong token cannot be found byte by byte
        return hmac.compare_digest(presented, TOKEN)

    def do_GET(self):
        # /health stays open: it carries no data and monitoring needs it.
        if self.path.rstrip("/") in ("/health", "/v1/health"):
            return self._send(200, {"status": "ok", "model": MODEL,
                                    "loaded": _model is not None})
        if self.path.rstrip("/") == "/v1/models":
            if not self._authorised():
                return self._send(401, {"error": {"message": "invalid or missing token"}})
            return self._send(200, {"object": "list", "data": [
                {"id": MODEL, "object": "model", "owned_by": "local"}]})
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if not self._authorised():
            return self._send(401, {"error": {"message": "invalid or missing token"}})
        if self.path.rstrip("/") != "/v1/embeddings":
            return self._send(404, {"error": {"message": "not found"}})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                raise ValueError("missing or oversized body")
            payload = json.loads(self.rfile.read(length))
            texts = payload.get("input")
            if isinstance(texts, str):
                texts = [texts]
            if not texts or not all(isinstance(t, str) for t in texts):
                raise ValueError("input must be a string or a list of strings")
            vectors = encode(texts, payload.get("dimensions"))
        except Exception as ex:
            _log.exception("embedding request failed")
            return self._send(400, {"error": {"message": f"{type(ex).__name__}: {ex}"}})

        self._send(200, {
            "object": "list",
            "model": payload.get("model") or MODEL,
            "data": [{"object": "embedding", "index": i, "embedding": v}
                     for i, v in enumerate(vectors)],
            "usage": {"prompt_tokens": sum(len(t.split()) for t in texts),
                      "total_tokens": sum(len(t.split()) for t in texts)},
        })


def already_serving():
    """True if a healthy instance already holds the port.

    The container entrypoint starts this unconditionally, so a manual start
    or a re-run must not crash with a bind traceback in the shared log.
    """
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=3) as r:
            return json.loads(r.read()).get("status") == "ok"
    except Exception:
        return False


if __name__ == "__main__":
    if HOST not in ("127.0.0.1", "localhost", "::1") and not TOKEN:
        _log.error(
            "refusing to listen on %s without ERA_EMBED_TOKEN — an open "
            "embeddings endpoint on a shared network is not acceptable", HOST)
        sys.exit(2)
    if already_serving():
        # Distinct code so a supervisor can tell "someone else owns the port"
        # from "I crashed" and exit instead of restart-looping.
        _log.info("era_embed already running on %s:%s — nothing to do", HOST, PORT)
        sys.exit(3)
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as ex:
        # Port taken by something that is not us: say so plainly and stop.
        _log.error("cannot bind %s:%s — %s", HOST, PORT, ex)
        sys.exit(1)
    _log.info("era_embed listening on %s:%s (model=%s)", HOST, PORT, MODEL)
    server.serve_forever()
