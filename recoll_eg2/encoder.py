"""Production encoder boundary and explicit test-only fake."""
from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import threading
from pathlib import Path
from typing import Protocol


def checked_vector(values, expected_dimension: int | None = None) -> list[float]:
    vector = [float(value) for value in values]
    if not vector or any(not math.isfinite(value) for value in vector):
        raise RuntimeError("encoder returned empty or non-finite vector")
    if expected_dimension is not None and len(vector) != expected_dimension:
        raise RuntimeError("encoder returned unexpected vector dimension")
    if not any(vector):
        raise RuntimeError("encoder returned a zero vector")
    return vector


class Encoder(Protocol):
    def encode(self, texts: list[str]) -> list[list[float]]: ...


class LiteRTEncoder:
    """Full-bundle LiteRT-LM 0.18 embedding engine (CPU by default).

    This service currently sends text to the full multimodal model. Image/audio/video
    preprocessing is intentionally reported unsupported rather than silently using a
    text-only model.
    """
    def __init__(self, model: str):
        try:
            from litert_lm import Backend, EmbeddingEngine
        except ImportError as exc:
            raise RuntimeError("litert-lm-api 0.18.0 is required") from exc
        self._lock = threading.RLock()
        cache = Path(os.environ.get("STATE_ROOT", "/state")) / "model-cache"
        cache.mkdir(parents=True, exist_ok=True)
        self._engine = EmbeddingEngine(model, backend=Backend.CPU(thread_count=1),
                                       vision_backend=Backend.CPU(thread_count=1),
                                       audio_backend=Backend.CPU(thread_count=1),
                                       cache_dir=str(cache))

    def encode(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            return [checked_vector(self._engine.compute_embedding(
                "task: search result | text: " + text.strip()).embedding, 768) for text in texts]

    def encode_query(self, text: str) -> list[float]:
        with self._lock:
            return checked_vector(self._engine.compute_embedding(
                "task: search query | text: " + text.strip()).embedding, 768)

    def close(self):
        with self._lock:
            self._engine.close()


class CommandEncoder:
    """JSON-lines bridge to a verified LiteRT-LM EmbeddingGemma 2 runner.

    Request: {"model":..., "task":"embed", "texts":[...]}
    Response: {"vectors":[[...], ...]}. No shell is involved. This boundary avoids
    guessing an unstable litert-lm-api Python symbol; container smoke tests must
    validate the configured runner and full quantized bundle before deployment.
    """
    def __init__(self, command: str, model: str, timeout: int = 120):
        if not command.strip():
            raise RuntimeError("EG2_ENCODER_COMMAND is required; no production fake encoder exists")
        self.argv = shlex.split(command)
        if not self.argv:
            raise RuntimeError("empty encoder command")
        self.model, self.timeout = model, timeout

    def encode(self, texts: list[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model, "task": "embed", "texts": texts}) + "\n"
        result = subprocess.run(self.argv, input=payload, text=True, capture_output=True,
                                timeout=self.timeout, check=False)
        if result.returncode:
            raise RuntimeError(f"encoder exited {result.returncode}: {result.stderr[-300:]}")
        try:
            data = json.loads(result.stdout)
            vectors = data["vectors"]
            if len(vectors) != len(texts) or not all(v and all(isinstance(x,(int,float)) for x in v) for v in vectors):
                raise ValueError("invalid vector count or values")
            dims = {len(v) for v in vectors}
            if len(dims) != 1:
                raise ValueError("inconsistent dimensions")
            return [checked_vector(v) for v in vectors]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("encoder returned invalid JSON response") from exc

    def encode_query(self, text: str) -> list[float]:
        # Bridge implementations receive the already prefixed query as a document batch.
        return self.encode(["task: search query | text: " + text.strip()])[0]


class DeterministicTestEncoder:
    """Unit-test dependency only. Never selected from production configuration."""
    def encode(self, texts: list[str]) -> list[list[float]]:
        import hashlib
        out = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            out.append([(digest[i] - 127.5) / 127.5 for i in range(16)])
        return out

    def encode_query(self, text: str) -> list[float]:
        return self.encode([text])[0]
