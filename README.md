# recoll-eg2

A local, read-only filesystem search service built on Recoll's real crawler and
extractors plus the full quantized EmbeddingGemma 2 740M LiteRT-LM bundle. It
copies approved bytes into a private sanitized snapshot under `/state`, lets
Recoll crawl only that snapshot, persists lexical/vector state, and
serves bounded hybrid search over HTTP.

This project targets a resource-constrained Linux ARM64 system such as a Pi 5 and
portable AMD64 images. The exact image must still be built and smoked on each
target architecture; this repository does not claim compatibility with every
Linux distribution or accelerator.

## Quick start

Create `example-source`, then build and run:

```sh
mkdir -p example-source
docker compose -f compose.example.yml build
docker compose -f compose.example.yml up -d
curl 'http://127.0.0.1:8787/health'
curl --get --data-urlencode 'q=meeting notes' --data 'limit=10' \
  'http://127.0.0.1:8787/search'
curl 'http://127.0.0.1:8787/status'
```

The source bind is read-only. `/state` contains Recoll's index, `vectors.sqlite3`
(WAL mode), generations, retry state, and checkpoints. Do not publish or back up
that state as source code: it contains extracted snippets. Restore it only to a
trusted machine with the same source data, or delete the volume and rebuild.

The example publishes to loopback only. For LAN access, deliberately change only
the host side of `ports` (for example, to a selected private interface address),
then add a trusted reverse proxy with TLS and authentication. Do not expose this
plaintext service directly to an untrusted network. Optional bearer auth reads a
token from a mounted file via `AUTH_TOKEN_FILE`; never put tokens in URLs, command
arguments, Compose environment values, or logs.

## Verification status

Experimental: ARM64 real Recoll/LiteRT tests pass on synthetic data; whole-home
indexing, independent LAN access, AMD64, and sustained load are not yet verified.
See `WORKLOG.md` for tested boundaries. Image inference has been exercised on the
full model, but this service does not yet ingest image/audio/video documents.

## Index and update semantics

Recoll performs lexical crawling/extraction, but never walks `/source`. A successful, complete filesystem
enumeration is hashed with SHA-256, so same-size edits are detected. When a
revision changes, its prior generation becomes invisible before Recoll extraction
or embedding begins. New chunks are staged and atomically published only after a
second hash proves the revision is still current. All chunks belong to one UUID
generation; publishing or deletion removes every older chunk, including tails
left after a file shrinks. Interrupted staging is removed idempotently at restart.

Embedding/extraction failure leaves the path invisible and persists exponential
retry metadata. A missing, replaced, marked, failed, or (by default) empty root marks the scope unavailable,
does not infer deletions, and suppresses results. A successful enumeration is the
only basis for deletion/now-excluded cleanup. There is no mandatory inotify. Snapshot staging currently causes a fresh lexical
crawl each scan; vectors are reused for unchanged source hashes, but this is not
an optimized incremental Recoll mirror.
An empty source authorizes purge only when the operator explicitly sets
`ALLOW_EMPTY_PURGE=1`.
Deletion visibility lag is at most the scan interval unless a scan is running;
changes detected by a scan are hidden immediately.

Recoll output is never trusted by itself: lexical hits are joined against the
currently visible generation table before return. Semantic hits use the same
filter. The API has no browsing, file-content, mutation, or arbitrary-path route.

## Privacy boundary

Before Recoll extraction, fd-relative no-follow reads copy approved bytes to
generated hashed names. One shared policy excludes hidden names and
directories, `.env` and `*.env` variants, private-key formats, common key names
(`id_rsa*`, `id_ed25519*`), `credentials*`, `secrets*`, and `tokens*`. It excludes
all `*.sqlite*` names and database/WAL/SHM/journal companions, and checks SQLite
and archive magic on that same descriptor. Symlinks are not followed,
archive suffixes are excluded, and a directory containing `.recoll-noindex` is
skipped, including at the root. Raw source names never enter Recoll configuration.

This is a filename/type denylist, not a data-loss-prevention engine. A secret in
an arbitrary `notes.txt` cannot be reliably detected. Keep sensitive trees out of
the `/source` mount. Recoll parsers are untrusted-input risk: the container drops
capabilities, runs unprivileged/read-only, has no Docker socket, bounds temporary
space, and disables archive suffix ingestion. Keep images current.

## File and model capabilities

The boundary currently permits plain text, Markdown, CSV/TSV, HTML/XML, PDF, and
RTF. Office ZIP containers are deliberately unsupported. Archives,
databases, hidden content, symlinks, oversized files, and unsupported parser types
are excluded. FFmpeg is not installed and media decoding is not run.

The image contains the full quantized `litert-community/embeddinggemma-2-740m-litert-lm`
bundle, not a PyTorch or text-only substitute. The current ingestion implementation
uses its text input path only. Image, audio, video, and interleaved multimodal
preprocessing are **unsupported by this service today**, despite being capabilities
of the model bundle. LiteRT-LM 0.18 applies the documented CPU embedding API and
search-document/search-query prefixes.

## Bounds and operations

Compose limits CPU, memory and swap, PIDs, a 128 MiB tmpfs, and response counts.
Configuration also limits file bytes, extracted characters, chunks, queries,
directory depth, total files, results, subprocess timeouts, and scan frequency. Work is intentionally serial to
bound peak model/parser use. Bad items persist a bounded exponential retry delay.
Logs contain no source content and Docker should be configured with a rotating log
driver appropriate to the deployment.

`MIN_FREE_BYTES` stops ingestion before reserve space is consumed while the query
process stays up and reports `low_space`. This byte check is not a hard disk cap.
For a true cap, mount `/state` from a bounded filesystem/LVM dataset/project quota
or a size-enforced volume. Verify the cap on the actual host. Docker healthchecks
only report health; `restart: unless-stopped` restarts a process that exits, not a
merely unhealthy process. The service health becomes red if the scan worker stops
or scope is unavailable, so external supervision should restart persistently
unhealthy containers if that policy is desired.

Ordinary non-secret settings are listed in `compose.example.yml`. `BIND_HOST`
defaults to `127.0.0.1` outside Compose. Other useful settings are `PORT`,
`SCAN_SECONDS`, `CHUNK_CHARS`, `CHUNK_OVERLAP`, `RECOLL_TIMEOUT`, and
`ENCODE_TIMEOUT`.

## Tests

Fast synthetic tests use an explicitly injected deterministic encoder; they do not
prove LiteRT, the model, or Recoll:

```sh
make test
```

After building the image, run the real integration smoke inside it with writable
temporary space (it invokes actual Recoll and the shipped model):

```sh
docker run --rm --entrypoint python recoll-eg2:local /app/tests/integration_smoke.py
```

If tests are not copied into your production image, bind-mount this public checkout
read-only at `/work` and run `/work/tests/integration_smoke.py`. Validate both
`linux/arm64` on the intended Pi and `linux/amd64`; record peak RSS, first model-load
time, scan duration, and parser behavior before choosing production limits.
