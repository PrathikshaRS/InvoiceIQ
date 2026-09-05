import io
from pathlib import Path

import pymupdf as fitz
import pytesseract
from PIL import Image, ImageOps, ImageFilter

from app.core.config import settings


# ---------------------------------------------------------------------------
# Tesseract configuration
# ---------------------------------------------------------------------------

if settings.tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd


# ---------------------------------------------------------------------------
# Image preprocessing
# ---------------------------------------------------------------------------

def _preprocess_image(image: Image.Image) -> Image.Image:
    """
    Preprocess an image before sending it to Tesseract.

    Steps:
    1. Convert to grayscale
    2. Upscale 3x
    3. Improve contrast
    4. Light sharpening

    Upscaling is particularly useful for invoices where the text/table
    is relatively small.
    """

    # Convert to grayscale
    image = image.convert("L")

    # Upscale 3x
    image = image.resize(
        (image.width * 3, image.height * 3),
        Image.Resampling.LANCZOS,
    )

    # Improve contrast automatically
    image = ImageOps.autocontrast(image)

    # Light sharpening
    image = image.filter(ImageFilter.SHARPEN)

    return image


# ---------------------------------------------------------------------------
# Tesseract OCR
# ---------------------------------------------------------------------------

def _run_tesseract(image: Image.Image) -> str:
    """
    Runs Tesseract on a preprocessed image.

    PSM 6 works well for invoices because the page usually contains
    multiple blocks of text arranged in a consistent layout.
    """

    return pytesseract.image_to_string(
        image,
        config="--oem 3 --psm 6",
    )


# ---------------------------------------------------------------------------
# Image OCR
# ---------------------------------------------------------------------------

def _extract_text_from_image(image_path: Path) -> str:
    """
    Opens an image, preprocesses it, and extracts text using Tesseract.
    """

    image = Image.open(image_path)

    image = _preprocess_image(image)

    return _run_tesseract(image)


# ---------------------------------------------------------------------------
# PDF OCR
# ---------------------------------------------------------------------------

def _extract_text_from_pdf(pdf_path: Path) -> str:
    """
    Rasterizes each PDF page into an image, preprocesses it,
    and runs Tesseract OCR.

    Each page's OCR text is joined together.
    """

    text_per_page = []

    doc = fitz.open(pdf_path)

    try:
        for page in doc:

            # Render PDF page at configured OCR DPI
            pixmap = page.get_pixmap(
                dpi=settings.ocr_dpi
            )

            # Convert Pixmap → PIL Image
            image = Image.open(
                io.BytesIO(
                    pixmap.tobytes("png")
                )
            )

            # Same preprocessing used for normal images
            image = _preprocess_image(image)

            # OCR
            text = _run_tesseract(image)

            text_per_page.append(text)

    finally:
        doc.close()

    return "\n".join(text_per_page)


# ---------------------------------------------------------------------------
# Public extraction function
# ---------------------------------------------------------------------------

def extract_text(file_path: Path, extension: str) -> str:
    """
    Extracts raw OCR text from a supported file.

    Supported:
    - PDF
    - PNG
    - JPG
    - JPEG

    This function only performs OCR.
    Field extraction is handled separately by the NLP service.
    """

    extension = extension.lower()

    if extension == ".pdf":
        return _extract_text_from_pdf(file_path)

    if extension in {".png", ".jpg", ".jpeg"}:
        return _extract_text_from_image(file_path)

    raise ValueError(
        f"Unsupported extension for OCR: {extension}"
    )