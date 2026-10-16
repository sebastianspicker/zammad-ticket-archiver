# Build the signed-capable production service separately from its minimal runtime image.
FROM python:3.14.8-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements/ /app/requirements/
RUN python -m venv /opt/build-venv \
  && /opt/build-venv/bin/python -m pip install --no-cache-dir --require-hashes --only-binary=:all: \
    -r requirements/tools.lock

COPY pyproject.toml README.md LICENSE CHANGELOG.md /app/
COPY src/ /app/src/

RUN /opt/build-venv/bin/python -m build --no-isolation --wheel --outdir /tmp/dist \
  && python -m venv /opt/venv \
  && /opt/venv/bin/python -m pip install --no-cache-dir --require-hashes --only-binary=:all: \
    -r requirements/signing.lock \
  && /opt/venv/bin/python -m pip install --no-cache-dir --no-deps /tmp/dist/*.whl


FROM python:3.14.8-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    XDG_CACHE_HOME=/tmp/.cache \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

# System deps (WeasyPrint runtime + basic fonts/mime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    fonts-dejavu-core \
    shared-mime-info \
  && rm -rf /var/lib/apt/lists/*

RUN addgroup --system --gid 10001 app \
  && adduser --system --uid 10001 --ingroup app --home /nonexistent --shell /usr/sbin/nologin app \
  && install -d -m 0700 -o app -g app /var/lib/chronikwerk/admin

COPY --from=builder --chown=app:app /opt/venv /opt/venv

# Only the public template belongs in the image. Local config/config.yaml and
# signing material are supplied at runtime and excluded from the build context.
COPY --chown=app:app config/config.example.yaml /app/config/config.example.yaml

USER app:app

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=10 \
  CMD python -c "import os,urllib.request; p=os.getenv('SERVER__PORT','8080'); urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz', timeout=2).read()"

CMD ["chronikwerk"]
