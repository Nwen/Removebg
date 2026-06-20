import io
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps
from rembg import new_session, remove
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

# ── Configuration ─────────────────────────────────────────────────────────────

REMBG_MODEL = os.getenv("REMBG_MODEL", "isnet-general-use")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "15"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
ALLOWED_TYPES = set(os.getenv("ALLOWED_TYPES", "image/png,image/jpeg,image/webp").split(","))
RATE_LIMIT = os.getenv("RATE_LIMIT", "20/minute")
MAX_DIMENSION = int(os.getenv("MAX_DIMENSION", "2500"))

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("removebg")

# ── Rate limiter ──────────────────────────────────────────────────────────────
# Reads the leftmost IP from X-Forwarded-For so it works behind NPM.

def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


limiter = Limiter(key_func=_client_ip)


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded — too many requests. Please slow down."},
    )


# ── Model session (initialised once at startup) ───────────────────────────────

_session = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _session
    logger.info("Loading rembg model: %s", REMBG_MODEL)
    t0 = time.monotonic()
    _session = new_session(REMBG_MODEL)
    logger.info("Model ready in %.1fs", time.monotonic() - t0)
    yield
    logger.info("Shutting down")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="removebg", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# ── Middleware: reject obviously-too-large requests before reading the body ───

@app.middleware("http")
async def _check_content_length(request: Request, call_next):
    if request.method == "POST" and request.url.path == "/api/remove":
        cl = request.headers.get("Content-Length")
        if cl and int(cl) > MAX_UPLOAD_BYTES + 4096:  # 4 KB headroom for form overhead
            return JSONResponse(
                status_code=413,
                content={"detail": f"File too large. Maximum is {MAX_UPLOAD_MB} MB."},
            )
    return await call_next(request)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fix_orientation(img: Image.Image) -> Image.Image:
    try:
        return ImageOps.exif_transpose(img)
    except Exception:
        return img


def _downscale(img: Image.Image) -> Image.Image:
    w, h = img.size
    longest = max(w, h)
    if longest <= MAX_DIMENSION:
        return img
    scale = MAX_DIMENSION / longest
    new_size = (round(w * scale), round(h * scale))
    logger.info("Downscaling %dx%d → %dx%d", w, h, *new_size)
    return img.resize(new_size, Image.LANCZOS)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "max_upload_mb": MAX_UPLOAD_MB,
            "allowed_types": ",".join(sorted(ALLOWED_TYPES)),
        },
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/api/remove")
@limiter.limit(RATE_LIMIT)
async def api_remove(request: Request, file: UploadFile = File(...)):
    # Validate content-type
    ct = (file.content_type or "").split(";")[0].strip().lower()
    if ct not in ALLOWED_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type '{ct}'. "
                f"Allowed: {', '.join(sorted(ALLOWED_TYPES))}."
            ),
        )

    # Read and validate size
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File too large ({len(data) / 1_048_576:.1f} MB). "
                f"Maximum is {MAX_UPLOAD_MB} MB."
            ),
        )

    t0 = time.monotonic()
    try:
        img = Image.open(io.BytesIO(data))
        img = _fix_orientation(img)
        img = _downscale(img)

        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA")

        result: Image.Image = remove(img, session=_session)

        buf = io.BytesIO()
        result.save(buf, format="PNG")
        out_bytes = buf.getvalue()

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Processing failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Background removal failed. Please try a different image.",
        )

    elapsed = time.monotonic() - t0
    logger.info(
        "Processed: model=%s in=%d out=%d elapsed=%.2fs",
        REMBG_MODEL, len(data), len(out_bytes), elapsed,
    )

    return Response(
        content=out_bytes,
        media_type="image/png",
        headers={"X-Processing-Time": f"{elapsed:.2f}"},
    )
