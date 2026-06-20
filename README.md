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

## Nginx Proxy Manager setup

The app listens on plain HTTP on port 8000. NPM handles TLS termination.

### 1 — Create a Proxy Host

| Field | Value |
|---|---|
| Domain Name | `removebg.nwen.eu` |
| Scheme | `http` |
| Forward Hostname / IP | IP or hostname of the Docker host |
| Forward Port | `8000` |
| Cache Assets | Off |
| Block Common Exploits | On |

Enable **SSL** (Let's Encrypt) and tick **Force SSL**.

### 2 — Raise the upload size limit

Nginx defaults to a 1 MB body limit. Photo uploads will hit 413 without this.

In the Proxy Host → **Advanced** tab, add:

```nginx
client_max_body_size 20m;
```

Keep this consistent with `MAX_UPLOAD_MB` (add a few MB of headroom for form overhead).

### 3 — Optional: soft access gate

The app has no built-in authentication. If you want to restrict access without
adding login screens, create an **Access List** in NPM (Basic Auth or IP allowlist)
and assign it to the Proxy Host.

---

## Portainer deployment

1. In Portainer, go to **Stacks → Add stack**.
2. Choose **Repository** (point at your git repo) or **Upload** / **Web editor**
   and paste the contents of `docker-compose.yml`.
3. Set any environment variables you want to override in the **Environment variables**
   section of the stack form (or edit `docker-compose.yml` directly).
4. Click **Deploy the stack**.
5. Watch the build log — first build pulls Python deps and downloads the model
   (~170 MB); subsequent builds are fast thanks to Docker layer caching.
6. When the stack is running, the health check turns green after ~60–120 s
   (time for the model to load into memory on first request).

To update: pull the new image/code and **Update the stack** in Portainer
(or `docker compose pull && docker compose up -d --build` on the CLI).

---

## Development (no Docker)

```bash
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
export REMBG_MODEL=isnet-general-use
uvicorn app.main:app --reload --port 8000
```

---

## Project layout

```
removebg/
├─ app/
│  ├─ main.py              FastAPI application
│  ├─ templates/
│  │  └─ index.html        Jinja2 HTML template
│  └─ static/
│     ├─ styles.css        All styles (dark-mode ready, no CDN)
│     └─ app.js            Vanilla JS (drag-drop, fetch, download)
├─ requirements.txt        Pinned Python dependencies
├─ Dockerfile              Multi-stage build; model baked in at build time
├─ .dockerignore
├─ docker-compose.yml      Portainer-ready stack definition
└─ README.md
```
