from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile


router = APIRouter(prefix="/api/anon", tags=["anon"])

DEFAULT_HEADING = "NO NAME. NO NONSENSE."
DEFAULT_BODY = (
    "Put your paragraph here. Keep it sharp, keep it honest, and leave the "
    "polished corporate rubbish somewhere else."
)
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_TYPES = {
    b"\xff\xd8\xff": ("jpg", "image/jpeg"),
    b"\x89PNG\r\n\x1a\n": ("png", "image/png"),
    b"GIF87a": ("gif", "image/gif"),
    b"GIF89a": ("gif", "image/gif"),
}


def _storage_dir() -> Path:
    configured = os.getenv("ANON_CONTENT_DIR", "").strip()
    if configured:
        path = Path(configured)
    elif Path("/data").exists():
        path = Path("/data/anon")
    else:
        path = Path(__file__).resolve().parent / "data" / "anon"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _content_path() -> Path:
    return _storage_dir() / "content.json"


def _default_content() -> dict[str, str | None]:
    return {
        "heading": DEFAULT_HEADING,
        "body": DEFAULT_BODY,
        "imageFilename": None,
        "updatedAt": None,
    }


def _load_content() -> dict[str, str | None]:
    try:
        raw = json.loads(_content_path().read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("content must be an object")
        content = _default_content()
        content.update({key: raw.get(key) for key in content})
        return content
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return _default_content()


def _public_content(content: dict[str, str | None]) -> dict[str, str | None]:
    filename = content.get("imageFilename")
    image_exists = bool(filename and (_storage_dir() / filename).is_file())
    return {
        "heading": content.get("heading") or DEFAULT_HEADING,
        "body": content.get("body") or DEFAULT_BODY,
        "imageUrl": "/api/anon/image" if image_exists else None,
        "updatedAt": content.get("updatedAt"),
    }


def _detect_image(data: bytes) -> tuple[str, str] | None:
    for signature, image_type in IMAGE_TYPES.items():
        if data.startswith(signature):
            return image_type
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _write_json(content: dict[str, str | None]) -> None:
    path = _content_path()
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(content, indent=2), encoding="utf-8")
    os.replace(temporary, path)


@router.get("/content")
def get_anon_content():
    return _public_content(_load_content())


@router.put("/content")
async def save_anon_content(request: Request):
    form = await request.form()
    heading = str(form.get("heading", "")).strip()
    body = str(form.get("body", "")).strip()
    if not heading or len(heading) > 80:
        raise HTTPException(status_code=422, detail="Heading must be 1 to 80 characters.")
    if not body:
        raise HTTPException(status_code=422, detail="Body text is required.")

    current = _load_content()
    image = form.get("image")
    new_filename: str | None = None
    if isinstance(image, UploadFile) and image.filename:
        data = await image.read(MAX_IMAGE_BYTES + 1)
        await image.close()
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=413, detail="Image must be no larger than 8 MB.")
        detected = _detect_image(data)
        if not detected:
            raise HTTPException(status_code=415, detail="Use a JPEG, PNG, GIF, or WebP image.")
        extension, _ = detected
        new_filename = f"image-{uuid.uuid4().hex}.{extension}"
        destination = _storage_dir() / new_filename
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, destination)

    previous_filename = current.get("imageFilename")
    content = {
        "heading": heading,
        "body": body,
        "imageFilename": new_filename or previous_filename,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _write_json(content)
    except OSError as exc:
        if new_filename:
            (_storage_dir() / new_filename).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Could not save the page content.") from exc

    if new_filename and previous_filename and previous_filename != new_filename:
        (_storage_dir() / previous_filename).unlink(missing_ok=True)
    return _public_content(content)


@router.get("/image")
def get_anon_image():
    content = _load_content()
    filename = content.get("imageFilename")
    if not filename:
        raise HTTPException(status_code=404, detail="No image has been published.")
    path = _storage_dir() / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Published image is missing.")
    detected = _detect_image(path.read_bytes()[:16])
    return FileResponse(path, media_type=detected[1] if detected else "application/octet-stream")
