# Pythia v2 — minimal single-container image.
# Only Python + requests + supercronic. No node/bun/dashboard/db.
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    DB_PATH=/app/data/pythia.db \
    LOG_DIR=/app/logs

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       bash ca-certificates curl jq procps sqlite3 \
    && rm -rf /var/lib/apt/lists/*

ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SUPERCRONIC_ARCH="amd64" ;; \
      arm64) SUPERCRONIC_ARCH="arm64" ;; \
      *) echo "Unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-linux-${SUPERCRONIC_ARCH}" -o /usr/local/bin/supercronic; \
    chmod +x /usr/local/bin/supercronic

WORKDIR /app

COPY python/requirements.txt /app/python/requirements.txt
RUN pip install --no-cache-dir -r /app/python/requirements.txt

COPY python /app/python
COPY scripts/entrypoint.sh /app/scripts/entrypoint.sh
COPY supercronic.crontab /app/supercronic.crontab

RUN chmod +x /app/scripts/entrypoint.sh \
    && mkdir -p /app/data /app/logs \
              /app/python/evaluation/snapshots \
              /app/python/evaluation/reports \
    && groupadd -g 1002 pythia \
    && useradd -u 1002 -g 1002 -m -s /bin/bash pythia \
    && chown -R pythia:pythia /app/data /app/logs \
              /app/python/evaluation/snapshots \
              /app/python/evaluation/reports

USER pythia

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["scheduler"]
