# MarketBell production image: one immutable image, two workloads.
#   API/website : gunicorn backend.production:application   (default CMD, port 8080)
#   Worker      : python -m backend.worker_main              (probe port 8081, no ingress)
#
# Supply chain: the base image is pinned by digest in CI (see .github/workflows/release.yml, which
# resolves and records it). Dependencies install only from hash-pinned requirements.container.lock
# with --no-deps, so no unpinned transitive package can enter the image.
ARG BASE_IMAGE=python:3.12-slim-bookworm

# Optional offline wheelhouse (docker build --build-context wheels=<dir> --build-arg PIP_OFFLINE=1).
FROM scratch AS wheels

FROM ${BASE_IMAGE} AS build
ARG PIP_OFFLINE=
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /build
COPY requirements.container.lock .
RUN --mount=type=bind,from=wheels,target=/wheels \
    python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --require-hashes --no-deps --only-binary=:all: \
        ${PIP_OFFLINE:+--no-index --find-links /wheels} -r requirements.container.lock && \
    find /opt/venv -name '__pycache__' -prune -exec rm -rf {} +

FROM ${BASE_IMAGE} AS runtime
LABEL org.opencontainers.image.title="MarketBell" \
      org.opencontainers.image.description="News-led research agent; no brokerage or order execution."
RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin marketbell && \
    mkdir -p /data && chown 10001:10001 /data
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=root:root --chmod=0755 backend ./backend
COPY --chown=root:root --chmod=0755 public ./public
COPY --chown=root:root --chmod=0755 recordings ./recordings
COPY --chown=root:root --chmod=0755 scripts ./scripts
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    MARKETBELL_ENV=production MARKETBELL_DATA_DIR=/data \
    MARKETBELL_ALLOW_LIVE=0 MARKETBELL_WORKER=0 MARKETBELL_ROLE=api \
    MARKETBELL_STRUCTURED_LOGS=1
USER 10001:10001
EXPOSE 8080 8081
# Role-aware local health check (Azure Container Apps uses its own HTTP probes, defined in Bicep).
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD ["python","-c","import os,sys,urllib.request; port=os.environ.get('MARKETBELL_HEALTH_PORT','8081') if os.environ.get('MARKETBELL_ROLE')=='worker' else '8080'; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+port+'/livez',timeout=3).status==200 else 1)"]
# One gunicorn worker process keeps the in-memory rate limiter coherent; threads serve concurrency.
# Long work never runs in request threads (it is queued to the worker workload).
CMD ["gunicorn","--bind","0.0.0.0:8080","--workers","1","--threads","8","--timeout","60","--graceful-timeout","25","--keep-alive","5","--worker-tmp-dir","/tmp","backend.production:application"]
