import io
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageOps
from rembg import new_session, remove
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

# ── Configuration ─────────────────────────────────────────────────────────────

REMBG_MODEL = os.getenv("REMBG_MODEL", "isnet-general-use")
ALLOWED_MODELS = frozenset({"isnet-general-use", "u2net", "u2netp", "birefnet-general"})
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


# ── Model session cache (lazy-loaded per model, kept for the process lifetime) ─

_sessions: dict[str, object] = {}


def _get_session(model: str) -> object:
    if model not in _sessions:
        logger.info("Loading model: %s", model)
        t0 = time.monotonic()
        _sessions[model] = new_session(model)
        logger.info("Model %s ready in %.1fs", model, time.monotonic() - t0)
    return _sessions[model]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-warm the default model so the first request isn't slow.
    _get_session(REMBG_MODEL)
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


def _apply_tolerance(result: Image.Image, tolerance: int) -> Image.Image:
    """Harden the alpha-channel mask produced by rembg.

    tolerance=0  → unchanged soft edges (default behaviour)
    tolerance=100 → near-binary mask; semi-transparent edge pixels are cut away

    Works by stretching the alpha channel so that values below `cutoff` become 0
    and the rest are scaled back to the 0-255 range.
    """
    if result.mode != "RGBA" or tolerance <= 0:
        return result
    r, g, b, a = result.split()
    a_arr = np.array(a, dtype=np.float32)
    cutoff = tolerance * 2.0              # 0→0, 100→200
    scale = 255.0 / max(255.0 - cutoff, 1.0)
    a_arr = np.clip((a_arr - cutoff) * scale, 0.0, 255.0)
    return Image.merge("RGBA", (r, g, b, Image.fromarray(a_arr.astype(np.uint8), mode="L")))


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
async def api_remove(
    request: Request,
    file: Annotated[UploadFile, File()],
    model: Annotated[str, Form()] = REMBG_MODEL,
    tolerance: Annotated[int, Form()] = 0,
    alpha_matting: Annotated[bool, Form()] = False,
    alpha_matting_foreground_threshold: Annotated[int, Form()] = 240,
    alpha_matting_background_threshold: Annotated[int, Form()] = 10,
):
    # Validate model
    if model not in ALLOWED_MODELS:
        raise HTTPException(status_code=422, detail=f"Unknown model '{model}'.")

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

        session = _get_session(model)
        result: Image.Image = remove(
            img,
            session=session,
            alpha_matting=alpha_matting,
            alpha_matting_foreground_threshold=max(0, min(255, alpha_matting_foreground_threshold)),
            alpha_matting_background_threshold=max(0, min(255, alpha_matting_background_threshold)),
        )
        tolerance = max(0, min(100, tolerance))
        result = _apply_tolerance(result, tolerance)

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
        "Processed: model=%s matting=%s tolerance=%d in=%d out=%d elapsed=%.2fs",
        model, alpha_matting, tolerance, len(data), len(out_bytes), elapsed,
    )

    return Response(
        content=out_bytes,
        media_type="image/png",
        headers={"X-Processing-Time": f"{elapsed:.2f}"},
    )
