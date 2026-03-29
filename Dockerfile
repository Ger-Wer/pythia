FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    BUN_INSTALL=/root/.bun \
    PATH=/root/.bun/bin:/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       bash \
       build-essential \
       ca-certificates \
       curl \
       git \
       jq \
       pkg-config \
       procps \
       sqlite3 \
       unzip \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://bun.sh/install | bash -s "bun-v1.3.11"

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

COPY package.json tsconfig.json bunfig.toml /app/
RUN bun install

COPY python/requirements.txt /app/python/requirements.txt
RUN python -m venv /app/python/venv \
    && /app/python/venv/bin/pip install --no-cache-dir -r /app/python/requirements.txt

RUN git clone https://github.com/virattt/dexter.git /app/dexter \
    && cd /app/dexter \
    && git checkout --detach e126374a0fd28f50144fe068d8a8c4b4541ed38c \
    && bun install

COPY src /app/src
COPY python /app/python
COPY scripts /app/scripts
COPY supercronic.crontab /app/supercronic.crontab
COPY .env.example /app/.env.example
COPY README.md LICENSE /app/

RUN chmod +x /app/scripts/*.sh \
    && mkdir -p /app/data /app/logs

ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["scheduler"]
