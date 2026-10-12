# syntax=docker/dockerfile:1.7
FROM python:3.11-slim-bookworm

ARG MODEL_URL=https://huggingface.co/litert-community/embeddinggemma-2-740m-litert-lm/resolve/main/embeddinggemma-2-740m.litertlm

RUN apt-get update && apt-get install -y --no-install-recommends \
      recollcmd antiword poppler-utils unrtf ca-certificates curl tini \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir litert-lm-api==0.18.0
RUN apt-get update && apt-get install -y --no-install-recommends python3-recoll \
    && rm -rf /var/lib/apt/lists/*

# Cache the verified public model independently of application source changes.
# Override URL only with an artifact matching the pinned digest.
RUN mkdir -p /model /source /state \
    && curl --fail --location --retry 3 --output /model/embeddinggemma-2-740m.litertlm "$MODEL_URL" \
    && echo 'e7a8a2204b91e0f96e92960e84a09a89212e1633dcb7575a9bf3378b4df77f4c  /model/embeddinggemma-2-740m.litertlm' | sha256sum -c -

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY recoll_eg2 /app/recoll_eg2
COPY tests /app/tests
RUN python -m pip install --no-cache-dir --no-deps . \
    && chown -R 65532:65532 /state

USER 65532:65532
ENV SOURCE_ROOT=/source STATE_ROOT=/state \
    EG2_MODEL_PATH=/model/embeddinggemma-2-740m.litertlm \
    PYTHONPATH=/usr/lib/python3/dist-packages \
    PYTHONUNBUFFERED=1 TMPDIR=/tmp
EXPOSE 8787
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["recoll-eg2","serve"]
