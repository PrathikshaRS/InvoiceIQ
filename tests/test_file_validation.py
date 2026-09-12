# tests/test_file_validation.py

import pytest
from fastapi import HTTPException

from app.services.invoice_service import validate_file


class _FakeUploadFile:
    """
    validate_file() only reads `.filename` - a full UploadFile (which
    wraps a real file-like object) would be needless overhead here.
    """
    def __init__(self, filename: str):
        self.filename = filename


@pytest.mark.parametrize(
    "filename,expected_extension",
    [
        ("invoice.pdf", ".pdf"),
        ("receipt.PNG", ".png"),  # case-insensitivity
        ("photo.jpg", ".jpg"),
        ("photo.jpeg", ".jpeg"),
    ],
)
def test_allowed_extensions_pass(filename, expected_extension):
    assert validate_file(_FakeUploadFile(filename)) == expected_extension


@pytest.mark.parametrize("filename", ["malware.exe", "doc.docx", "archive.zip", "noext"])
def test_disallowed_extensions_raise_400(filename):
    with pytest.raises(HTTPException) as exc_info:
        validate_file(_FakeUploadFile(filename))
    assert exc_info.value.status_code == 400