import logging

from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import Optional
from fastapi import Query
from app.services.invoice_service import search_invoices  # add to existing import block
from app.models.schemas import UploadResponse
from app.services.ocr_service import extract_text
from app.services.nlp_service import extract_fields
from app.services.validation_service import validate_fields
from app.services.invoice_service import (
    validate_file, save_upload, save_processed_document,
    save_failed_document, get_processed_document, maybe_save_to_sql,
    check_for_duplicate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.post("/upload", response_model=UploadResponse)
async def upload_invoice(file: UploadFile = File(...)) -> UploadResponse:

    extension = validate_file(file)
    document_id, saved_path = save_upload(file, extension)

    try:
        raw_text = extract_text(saved_path, extension)
        fields = extract_fields(raw_text)
        validation = validate_fields(fields)

    except Exception:
        logger.exception(
            "Processing failed for document_id=%s (%s)", document_id, file.filename
        )
        save_failed_document(
            document_id=document_id,
            original_filename=file.filename or "unknown",
            extension=extension,
            error_message="Document could not be processed (OCR/NLP/validation error).",
        )
        return UploadResponse(
            document_id=document_id,
            original_filename=file.filename or "unknown",
            file_type=extension,
            status="failed",
            message="File was saved but could not be processed.",
            extracted_fields=None,
            warnings=["Document could not be processed (OCR/NLP/validation error)."],
        )

    # Duplicate check runs BEFORE this document's own SQL insert, and
    # only for documents that weren't already rejected (a rejected
    # multi-document PDF has no reliable fields to compare on anyway).
    duplicate = None
    if validation.status != "rejected":
        duplicate = check_for_duplicate(fields)

    if duplicate:
        validation.warnings.append(
            f"Possible duplicate invoice detected "
            f"(matches existing document_id={duplicate['document_id']})."
        )
        if validation.status == "processed":
            validation.status = "needs_review"

    save_processed_document(
        document_id=document_id,
        original_filename=file.filename or "unknown",
        extension=extension,
        raw_text=raw_text,
        fields=fields,
        validation=validation,
        is_duplicate=bool(duplicate),
    )

    maybe_save_to_sql(document_id, fields, validation.status)

    return UploadResponse(
        document_id=document_id,
        original_filename=file.filename or "unknown",
        file_type=extension,
        status=validation.status,
        message=f"File processed with status '{validation.status}'.",
        extracted_fields=fields,
        warnings=validation.warnings,
        is_duplicate=bool(duplicate),
    )

@router.get("/search")
def search_invoices_route(
    vendor: Optional[str] = Query(None, description="Vendor name, partial match"),
    date_from: Optional[str] = Query(None, description="Invoice date on/after (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Invoice date on/before (YYYY-MM-DD)"),
    min_amount: Optional[float] = Query(None, description="Minimum total_amount"),
    max_amount: Optional[float] = Query(None, description="Maximum total_amount"),
    status: Optional[str] = Query(None, description="processed or needs_review"),
) -> dict:
    results = search_invoices(vendor, date_from, date_to, min_amount, max_amount, status)
    return {"count": len(results), "results": results}

@router.get("/{document_id}")
def get_invoice(document_id: str) -> dict:
    document = get_processed_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return document