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
from app.db.sqlserver import save_invoice_to_sql, find_duplicate_invoice, search_invoices as sql_search_invoices, get_analytics as sql_get_analytics

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

def check_for_duplicate(fields: ExtractedFields) -> Optional[dict]:
    """
    Checks SQL Server for a prior invoice with the same vendor name,
    invoice number, and total amount. Uses the same three-field
    eligibility as maybe_save_to_sql, since those are the only fields
    reliable enough to compare on.

    A SQL Server failure here is logged but never raised - duplicate
    detection is a nice-to-have warning, not something that should turn
    a successful upload into a failure.
    """
    if not (fields.vendor_name and fields.invoice_number and fields.total_amount):
        return None

    try:
        return find_duplicate_invoice(
            vendor_name=fields.vendor_name,
            invoice_number=fields.invoice_number,
            total_amount=fields.total_amount,
        )
    except Exception:
        logger.exception("Duplicate check against SQL Server failed")
        return None

def search_invoices(
    vendor: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    status: str | None = None,
) -> list[dict]:
    """
    Thin pass-through to the SQL Server search query. Unlike duplicate
    detection or maybe_save_to_sql, a search failure IS surfaced to the
    client as an error - there's no fallback data source to silently
    succeed with if SQL Server is unreachable.
    """
    try:
        return sql_search_invoices(
            vendor_name=vendor, date_from=date_from, date_to=date_to,
            min_amount=min_amount, max_amount=max_amount, status=status,
        )
    except Exception:
        logger.exception("Invoice search failed")
        raise HTTPException(status_code=503, detail="Search is temporarily unavailable.")

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
    is_duplicate: bool = False,
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
        "is_duplicate": is_duplicate,
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

def get_analytics() -> dict:
    """
    Thin pass-through to the SQL Server analytics query. Same reasoning
    as search_invoices: a failure here is surfaced as a 503, not swallowed,
    since there's no meaningful fallback data source for a dashboard stat.
    """
    try:
        return sql_get_analytics()
    except Exception:
        logger.exception("Analytics query failed")
        raise HTTPException(status_code=503, detail="Analytics is temporarily unavailable.")

def get_processed_document(document_id: str) -> Optional[dict]:
    """Fetches a stored document by its document_id, or None if not found."""
    collection = get_invoices_collection()
    return collection.find_one({"document_id": document_id}, {"_id": 0})