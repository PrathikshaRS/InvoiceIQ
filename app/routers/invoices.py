from fastapi import APIRouter, UploadFile, File

from app.models.schemas import UploadResponse
from app.services.invoice_service import validate_file, save_upload

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.post("/upload", response_model=UploadResponse)
async def upload_invoice(file: UploadFile = File(...)) -> UploadResponse:
    """
    Accepts an invoice/receipt file (PDF, PNG, or JPEG), validates it,
    and saves it to disk under a generated document ID.

    OCR/NLP processing is added in later phases — this step only handles
    safe ingestion of the file.
    """
    extension = validate_file(file)
    document_id, saved_path = save_upload(file, extension)

    return UploadResponse(
        document_id=document_id,
        original_filename=file.filename or "unknown",
        file_type=extension,
        status="uploaded",
        message=f"File saved successfully as {saved_path.name}",
    )