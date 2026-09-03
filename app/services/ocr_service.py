import io
from pathlib import Path

import pymupdf as fitz  # PyMuPDF's new import name; 'as fitz' keeps the rest of the file unchanged
import pytesseract
from PIL import Image

from app.core.config import settings

if settings.tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


def _extract_text_from_image(image_path: Path) -> str:
    """Runs Tesseract directly on an image file."""
    image = Image.open(image_path)
    return pytesseract.image_to_string(image)


def _extract_text_from_pdf(pdf_path: Path) -> str:
    """
    Rasterizes each page of the PDF into an image (Tesseract can't read
    PDFs directly), then OCRs each page and joins the results.
    """
    text_per_page = []
    doc = fitz.open(pdf_path)
    try:
        for page in doc:
            pixmap = page.get_pixmap(dpi=settings.ocr_dpi)
            image = Image.open(io.BytesIO(pixmap.tobytes("png")))
            text_per_page.append(pytesseract.image_to_string(image))
    finally:
        doc.close()
    return "\n".join(text_per_page)


def extract_text(file_path: Path, extension: str) -> str:
    """
    Dispatches to the right extraction method based on file extension.
    Returns raw OCR text — no cleanup or field parsing happens here,
    that's the NLP service's job in Phase 4.
    """
    if extension == ".pdf":
        return _extract_text_from_pdf(file_path)
    if extension in {".png", ".jpg", ".jpeg"}:
        return _extract_text_from_image(file_path)
    raise ValueError(f"Unsupported extension for OCR: {extension}")