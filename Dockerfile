# Stage 1: Build
FROM python:3.12-slim AS builder

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir --prefix=/install .

# Stage 2: Runtime
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libcairo2 libgdk-pixbuf2.0-dev libffi curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

RUN useradd --create-home --shell /bin/bash deepresearch
USER deepresearch
WORKDIR /home/deepresearch

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8080/api/status || exit 1

ENTRYPOINT ["deepresearch"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
