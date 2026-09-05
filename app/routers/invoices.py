import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, UploadFile, File, HTTPException
from typing import Optional
from app.models.schemas import UploadResponse
from app.services.invoice_service import (
    validate_file, save_upload, save_processed_document, get_processed_document,
)
from app.services.ocr_service import extract_text
from app.services.nlp_service import extract_fields
from app.services.validation_service import validate_fields
from app.services.invoice_service import (
    validate_file, save_upload, save_processed_document,
    save_failed_document, get_processed_document,
)


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

    save_processed_document(
        document_id=document_id,
        original_filename=file.filename or "unknown",
        extension=extension,
        raw_text=raw_text,
        fields=fields,
        validation=validation,
    )

    return UploadResponse(
        document_id=document_id,
        original_filename=file.filename or "unknown",
        file_type=extension,
        status=validation.status,
        message=f"File processed with status '{validation.status}'.",
        extracted_fields=fields,
        warnings=validation.warnings,
    )

    return UploadResponse(
        document_id=document_id,
        original_filename=file.filename or "unknown",
        file_type=extension,
        status=validation.status,
        message=f"File processed with status '{validation.status}'.",
        extracted_fields=fields,
        warnings=validation.warnings,
    )


@router.get("/{document_id}")
def get_invoice(document_id: str) -> dict:
    document = get_processed_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return document