# syntax=docker/dockerfile:1
FROM python:3.15-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.15-slim
RUN useradd --system --uid 10001 --home /data harmony \
 && mkdir -p /data /app/device-db && chown harmony /data
COPY --from=build /opt/venv /opt/venv
COPY --from=build /app/src /app/src
ENV PATH=/opt/venv/bin:$PATH \
    HARMONY_DATA_DIR=/data \
    HARMONY_DB_PATH=/data/harmony.db \
    HARMONY_DEVICE_DB_PATH=/app/device-db \
    HARMONY_HTTP_PORT=80 \
    HARMONY_HTTPS_PORT=443
WORKDIR /data
# Binding :80/:443 as non-root.
RUN apt-get update && apt-get install -y --no-install-recommends libcap2-bin \
 && setcap 'cap_net_bind_service=+ep' "$(readlink -f /usr/local/bin/python3)" \
 && apt-get purge -y libcap2-bin && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*
USER harmony
EXPOSE 80 443
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:80/healthz')"
# CA + server cert are generated in /data/certs on first start.
ENTRYPOINT ["harmony-server"]
