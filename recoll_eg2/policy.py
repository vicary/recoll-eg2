"""Fail-closed names and content types admitted to the private Recoll stage."""
from __future__ import annotations

import fnmatch
from pathlib import PurePath

SQLITE_MAGIC = b"SQLite format 3\x00"
MARKER = ".recoll-noindex"

DENY_PATTERNS = (
    ".*", ".env", "*.env", "*.env.*", "*.pem", "*.key", "*.p12", "*.pfx",
    "id_rsa*", "id_ed25519*", "credentials*", "secrets*", "tokens*",
    "*.db", "*.db-*", "*.sqlite*", "*.wal", "*.shm", "*.journal",
    "*.zip", "*.tar", "*.tgz", "*.tbz", "*.tbz2", "*.txz", "*.7z", "*.rar",
    "*.gz", "*.bz2", "*.xz", "*.zst", "*.iso",
)

# Deliberately narrow: no archive/container or media formats are handed to parsers.
ALLOWED_SUFFIXES = {".txt", ".text", ".md", ".rst", ".csv", ".tsv", ".html", ".htm",
                    ".xml", ".pdf", ".rtf", ".odt", ".ods", ".odp", ".docx", ".xlsx", ".pptx"}
ARCHIVE_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"7z\xbc\xaf\x27\x1c", b"Rar!", b"\x1f\x8b")
# OOXML/ODF are ZIP containers and are intentionally unsupported for now: distinguishing
# them safely requires container inspection, which this boundary does not perform.
CONTAINER_SUFFIXES = {".odt", ".ods", ".odp", ".docx", ".xlsx", ".pptx"}

def denied_name(name: str) -> bool:
    low = name.casefold()
    return any(fnmatch.fnmatchcase(low, pattern.casefold()) for pattern in DENY_PATTERNS)

def eligible_relative(rel: str) -> bool:
    parts = PurePath(rel).parts
    return bool(parts) and all(p not in ("", ".", "..") and not denied_name(p) for p in parts)

def supported_name(name: str) -> bool:
    suffix = PurePath(name).suffix.casefold()
    return suffix in ALLOWED_SUFFIXES and suffix not in CONTAINER_SUFFIXES

def forbidden_magic(head: bytes) -> bool:
    return head.startswith(SQLITE_MAGIC) or any(head.startswith(m) for m in ARCHIVE_MAGIC)
