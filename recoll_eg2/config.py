from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class Config:
    source: Path = Path("/source")
    state: Path = Path("/state")
    bind: str = "127.0.0.1"
    port: int = 8787
    scan_seconds: int = 900
    max_file_bytes: int = 16 * 1024 * 1024
    max_text_chars: int = 2_000_000
    max_files: int = 100_000
    max_depth: int = 64
    max_chunks_per_file: int = 2048
    chunk_chars: int = 1600
    chunk_overlap: int = 160
    max_results: int = 50
    max_query_chars: int = 1000
    min_free_bytes: int = 512 * 1024 * 1024
    recoll_timeout: int = 600
    encode_timeout: int = 120
    encoder_command: str = ""
    model_path: str = "/model/embeddinggemma-2-740m-litert-lm"
    allow_empty_purge: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            source=Path(os.environ.get("SOURCE_ROOT", "/source")),
            state=Path(os.environ.get("STATE_ROOT", "/state")),
            bind=os.environ.get("BIND_HOST", "127.0.0.1"),
            port=_int("PORT", 8787, 1, 65535),
            scan_seconds=_int("SCAN_SECONDS", 900, 30, 86400),
            max_file_bytes=_int("MAX_FILE_BYTES", 16*1024*1024, 1024, 1024**3),
            max_text_chars=_int("MAX_TEXT_CHARS", 2_000_000, 1000, 20_000_000),
            max_files=_int("MAX_FILES", 100_000, 1, 1_000_000),
            max_depth=_int("MAX_DEPTH", 64, 1, 256),
            max_chunks_per_file=_int("MAX_CHUNKS_PER_FILE", 2048, 1, 10000),
            chunk_chars=_int("CHUNK_CHARS", 1600, 128, 16000),
            chunk_overlap=_int("CHUNK_OVERLAP", 160, 0, 4000),
            max_results=_int("MAX_RESULTS", 50, 1, 200),
            max_query_chars=_int("MAX_QUERY_CHARS", 1000, 1, 10000),
            min_free_bytes=_int("MIN_FREE_BYTES", 512*1024*1024, 1024*1024, 1024**4),
            recoll_timeout=_int("RECOLL_TIMEOUT", 600, 10, 3600),
            encode_timeout=_int("ENCODE_TIMEOUT", 120, 5, 1800),
            encoder_command=os.environ.get("EG2_ENCODER_COMMAND", ""),
            model_path=os.environ.get("EG2_MODEL_PATH", "/model/embeddinggemma-2-740m-litert-lm"),
            allow_empty_purge=os.environ.get("ALLOW_EMPTY_PURGE", "0") == "1",
        )
