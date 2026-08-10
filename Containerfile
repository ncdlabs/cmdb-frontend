# syntax=docker/dockerfile:1.7

# Frontend builds on the build host arch (avoids qemu segfaults under npm).
# Prefer a prebuilt dist/ when present (faster / offline-friendly cluster builds).
FROM --platform=$BUILDPLATFORM node:22-bookworm AS frontend
WORKDIR /src/frontend
COPY app/frontend/ ./
RUN if [ -f dist/index.html ]; then \
      echo "Using prebuilt frontend dist"; \
    else \
      npm ci && npm run build; \
    fi

FROM python:3.10-slim-bookworm AS runtime
ARG TARGETARCH
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CMDB_ROOT=/data/cmdb \
    CMDB_STATIC=/app/static \
    PORT=8080
WORKDIR /app

COPY app/requirements.txt ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-client iputils-ping iproute2 openssl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

COPY app/cmdb_api ./cmdb_api
COPY --from=frontend /src/frontend/dist ./static

# Seed inventory for StatefulSet PVC init, plus baked /data/cmdb for non-PVC runs
COPY inventory/servers /data/cmdb-seed/servers
COPY inventory/services /data/cmdb-seed/services
COPY inventory/applications /data/cmdb-seed/applications
COPY inventory/environments /data/cmdb-seed/environments
COPY inventory/index.yaml /data/cmdb-seed/index.yaml
COPY inventory/docs /data/cmdb-seed/docs
COPY inventory/servers /data/cmdb/servers
COPY inventory/services /data/cmdb/services
COPY inventory/applications /data/cmdb/applications
COPY inventory/environments /data/cmdb/environments
COPY inventory/index.yaml /data/cmdb/index.yaml
COPY inventory/docs /data/cmdb/docs

EXPOSE 8080
USER nobody
CMD ["python", "-m", "uvicorn", "cmdb_api.main:app", "--host", "0.0.0.0", "--port", "8080"]
