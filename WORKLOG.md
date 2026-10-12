# Work log

## Implementation

- Standard-library Python HTTP service with bounded configuration.
- Recoll crawler and Python extraction/query bindings operate only on sanitized
  immutable snapshots, not on the original source tree.
- fd-relative no-follow/nonblocking source access, early SQLite-header rejection,
  filename/marker/type exclusions, and same-fd content hashes.
- SQLite WAL generation storage, stale-hit suppression, atomic publication,
  persistent retries, deletion cleanup, and source-identity checks.
- Full quantized LiteRT-LM EmbeddingGemma 2 integration with serialized one-thread
  CPU inference, explicit query/document prefixes, and finite-vector validation.
- Recoll worker subprocesses have time, address-space, CPU and output-file bounds;
  timeout handling kills their process group.
- Unprivileged read-only container and one-CPU/two-GiB Compose example.

## Verification

- 19 synthetic unit tests passed, including SQLite header-only rejection, FIFO
  replacement, source revisions, shrink, deletion, exclusion, interrupted state,
  missing/replaced roots and failed-publication status.
- Independent static review passed after two correction passes. This is not a
  claim of comprehensive security certification.
- Linux ARM64 image built with Recoll 1.34.3 and litert-lm-api 0.18.0.
- Real full-model text, query and synthetic PNG inference produced 768 finite
  values under a one-CPU/two-GiB container limit.
- Real Recoll lexical plus LiteRT semantic round-trip passed with synthetic
  fixtures, including excluded-content canary absence, same-size preserved-mtime
  edits, shortened content, deletion, and missing-source state preservation.
- Model SHA-256 verified:
  `e7a8a2204b91e0f96e92960e84a09a89212e1633dcb7575a9bf3378b4df77f4c`.

The verification build used checksum-verified local copies of the public wheel
and model to avoid repeated downloads. No private source material or model
weights are committed to this repository.

## Limits

Only synthetic data has been tested. Whole-home indexing, AMD64, general PDF/RTF
parser behavior, sustained load, reboot recovery, and independent LAN client
reachability are not verified. The service ingests text-like documents only;
standalone image inference does not implement image/audio/video ingestion.
Snapshot staging currently causes a fresh lexical crawl each scan; this is not
an optimized incremental Recoll mirror. The default Compose volume is not a
hard storage quota: deployments must provide a genuinely bounded filesystem.
