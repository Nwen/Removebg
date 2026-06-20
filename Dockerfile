# ── Stage 1: builder ──────────────────────────────────────────────────────────
# Install Python deps + pre-download the default rembg model.
# Build tools (gcc, g++) are not carried into the final image.
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Isolated venv keeps the final COPY clean.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
# onnxruntime (CPU) is already a transitive dep of rembg.
# Explicitly listed in requirements.txt to pin the CPU-only wheel.
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download the default model so the image runs fully offline.
# U2NET_HOME is where rembg (via pooch) writes the .onnx file.
ENV U2NET_HOME=/models
RUN python -c "from rembg import new_session; new_session('isnet-general-use')"


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

# libgomp1: OpenMP runtime required by onnxruntime CPU parallelism.
# curl:     lightweight healthcheck alternative to a Python subprocess.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — container security hygiene.
RUN useradd -r -u 1001 -m -d /home/appuser appuser

# Copy the pre-built venv and baked model from builder.
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /models   /models

WORKDIR /app
COPY app/ ./app/

RUN chown -R appuser:appuser /app /models

USER appuser

ENV PATH="/opt/venv/bin:$PATH"
ENV U2NET_HOME=/models
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# --proxy-headers: let Uvicorn consume X-Forwarded-For/X-Forwarded-Proto so
# request.client.host reflects the real client (also used by slowapi key_func).
# --forwarded-allow-ips=*: trust the upstream NPM proxy on the Docker network.
# --timeout-keep-alive: close idle connections sooner to free worker resources.
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -fs http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--timeout-keep-alive", "30", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*"]
