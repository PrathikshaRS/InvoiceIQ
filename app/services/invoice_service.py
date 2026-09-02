#Business logic lives here, not in the route. This keeps invoices.py thin and makes this logic independently testable.

import uuid
from pathlib import Path

from fastapi import UploadFile, HTTPException

from app.core.config import settings


def validate_file(file: UploadFile) -> str:
    """
    Validates file extension and returns the lowercase extension.
    Raises HTTPException(400) if the file type isn't allowed.
    """
    extension = Path(file.filename or "").suffix.lower()
    if extension not in settings.allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{extension}'. "
                   f"Allowed types: {', '.join(sorted(settings.allowed_extensions))}",
        )
    return extension


def save_upload(file: UploadFile, extension: str) -> tuple[str, Path]:
    """
    Saves the uploaded file under a generated document_id, never the client's
    original filename, to avoid path traversal and name-collision issues.

    Returns (document_id, saved_path).
    """
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    safe_path = upload_dir / f"{document_id}{extension}"

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    contents = file.file.read()
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"File exceeds {settings.max_upload_size_mb}MB limit.",
        )

    with open(safe_path, "wb") as f:
        f.write(contents)

    return document_id, safe_path