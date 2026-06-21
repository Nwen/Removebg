# removebg — Self-hosted background remover

A lightweight, self-hosted web app that removes image backgrounds using AI.
Think [remove.bg](https://remove.bg) but entirely on your own hardware.

**Stack:** Python 3.12 · FastAPI · Uvicorn · rembg · ONNX Runtime (CPU) · Pillow · slowapi · Jinja2 + vanilla JS

**Privacy guarantee:** the container makes no outbound network calls at runtime.
The AI model is baked into the image at build time. Uploaded images are processed
in memory and never written to disk.

---

## Quick start

```bash
# Build and start (first build downloads the ~170 MB model — takes a few minutes)
docker compose up -d --build

# Open the UI
open http://localhost:8000
```

The `/health` endpoint returns `{"status":"ok"}` — useful for monitoring.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `REMBG_MODEL` | `isnet-general-use` | Which background-removal model to use (see table below). |
| `MAX_UPLOAD_MB` | `15` | Maximum upload size in megabytes (server-side). |
| `ALLOWED_TYPES` | `image/png,image/jpeg,image/webp` | Comma-separated accepted MIME types. |
| `MAX_DIMENSION` | `2500` | Images with a longest edge above this are downscaled before inference to bound memory usage. The aspect ratio is preserved. |
| `RATE_LIMIT` | `20/minute` | slowapi rate-limit expression applied per client IP on `POST /api/remove`. |

### Model tradeoffs

| Model | File size | Quality | Speed on CPU | Notes |
|---|---|---|---|---|
| `isnet-general-use` ★ | ~170 MB | Excellent | Fast (~2–5 s on modern CPU) | Best default choice. Baked into the image. |
| `u2net` | ~176 MB | Good | Moderate | Classic model, reliable on most subjects. |
| `u2netp` | ~4 MB | Acceptable | Fastest | Use when speed matters more than edge quality. |
| `birefnet-general` | ~374 MB | Best | Very slow (~30–120 s on CPU) | Suitable only if you can tolerate long waits. |

★ Default. The `isnet-general-use` model is pre-downloaded during `docker build`
so the container runs fully offline. Other models are downloaded by rembg on first
use (requires internet access from the host, or mount a named volume with the
`.onnx` file pre-placed at `$U2NET_HOME`).

---

## Development (no Docker)

```bash
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
export REMBG_MODEL=isnet-general-use
uvicorn app.main:app --reload --port 8000
```
