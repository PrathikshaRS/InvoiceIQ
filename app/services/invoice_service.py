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
from app.db.sqlserver import save_invoice_to_sql
from app.models.schemas import ExtractedFields, ValidationResult


SQL_ELIGIBLE_STATUSES = {"processed", "needs_review"}


def maybe_save_to_sql(document_id: str, fields: ExtractedFields, status: str) -> None:
    """
    Writes a structured row to SQL Server if - and only if - the document
    was actually processed (not rejected/failed) AND the three fields SQL
    Server requires as NOT NULL were all successfully extracted. This can
    mean a 'needs_review' document is skipped here even though it exists
    fully in Mongo - e.g. missing invoice_date alone doesn't block the SQL
    write, but a missing total_amount does.

    A SQL write failure is logged but never raised - the Mongo record is
    already the source of truth for this document, so a SQL Server issue
    (e.g. the container being down) shouldn't turn a successful upload
    into a failed one.
    """
    if status not in SQL_ELIGIBLE_STATUSES:
        return

    if not (fields.vendor_name and fields.invoice_number and fields.total_amount):
        return

    try:
        save_invoice_to_sql(
            document_id=document_id,
            vendor_name=fields.vendor_name,
            gstin=None,  # not yet extracted separately from vendor_name - see Known Issues
            invoice_number=fields.invoice_number,
            invoice_date=fields.invoice_date,
            subtotal=fields.subtotal,
            tax_amount=fields.tax_amount,
            total_amount=fields.total_amount,
            currency=fields.currency,
            status=status,
        )
    except Exception:
        logger.exception(
            "Failed to write document %s to SQL Server", document_id
        )

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