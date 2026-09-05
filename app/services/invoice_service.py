#Business logic lives here, not in the route. This keeps invoices.py thin and makes this logic independently testable.
import logging

logger = logging.getLogger(__name__)

import uuid
from pathlib import Path

from fastapi import UploadFile, HTTPException

from app.core.config import settings

from datetime import datetime, timezone
from typing import Optional

from app.db.mongodb import get_invoices_collection
from app.models.schemas import ExtractedFields, ValidationResult


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

def save_processed_document(
    document_id: str,
    original_filename: str,
    extension: str,
    raw_text: str,
    fields: ExtractedFields,
    validation: ValidationResult,
) -> None:
    """Persists the full result of processing one document to MongoDB."""
    collection = get_invoices_collection()
    collection.insert_one({
        "document_id": document_id,
        "original_filename": original_filename,
        "file_type": extension,
        "raw_ocr_text": raw_text,
        "extracted_fields": fields.model_dump(),
        "status": validation.status,
        "warnings": validation.warnings,
        "upload_timestamp": datetime.now(timezone.utc).isoformat(),
    })

def save_failed_document(
    document_id: str,
    original_filename: str,
    extension: str,
    error_message: str,
) -> None:
    """
    Persists a record of a document that failed during OCR/NLP/validation,
    so a failure is never silent - the file exists on disk under
    document_id, and this ensures there's a matching trace of what
    happened to it, even though no fields could be extracted.
    """
    collection = get_invoices_collection()
    collection.insert_one({
        "document_id": document_id,
        "original_filename": original_filename,
        "file_type": extension,
        "raw_ocr_text": None,
        "extracted_fields": None,
        "status": "failed",
        "warnings": [error_message],
        "upload_timestamp": datetime.now(timezone.utc).isoformat(),
    })


def get_processed_document(document_id: str) -> Optional[dict]:
    """Fetches a stored document by its document_id, or None if not found."""
    collection = get_invoices_collection()
    return collection.find_one({"document_id": document_id}, {"_id": 0})