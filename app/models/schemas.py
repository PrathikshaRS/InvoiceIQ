from typing import Optional
from typing import List
from pydantic import BaseModel


class UploadResponse(BaseModel):
    document_id: str
    original_filename: str
    file_type: str
    status: str
    message: str


class ExtractedFields(BaseModel):
    """
    Structured fields pulled from OCR text. Any field that couldn't be
    reliably extracted is None - we never guess or invent values.

    If multiple_documents_detected is True, this PDF/image appears to
    contain more than one distinct invoice concatenated together (e.g. a
    marketplace order bundling a seller invoice + platform-fee invoice +
    logistics bill of supply into one file). Field extraction is skipped
    entirely in that case, since combining values from different invoices
    would silently produce incorrect data - splitting multi-invoice PDFs
    into separate documents is a planned future enhancement, not handled yet.
    """
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    currency: Optional[str] = None
    multiple_documents_detected: bool = False

class ValidationResult(BaseModel):
    """
    status: 'processed' (no issues), 'needs_review' (one or more warnings,
    but data is still usable), or 'rejected' (document couldn't be
    reliably processed at all, e.g. multiple concatenated invoices).
    """
    status: str
    warnings: List[str] = []