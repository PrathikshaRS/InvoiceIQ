#Pydantic models define the shape of our API responses — FastAPI uses them to validate output and auto-generate docs

from pydantic import BaseModel

class UploadResponse(BaseModel):
    """Returned immediately after a file is accepted and saved."""
    document_id: str
    original_filename: str
    file_type: str
    status: str
    message: str