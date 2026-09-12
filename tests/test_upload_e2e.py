# tests/test_upload_e2e.py
#
# Full-pipeline tests through the real route: real Tesseract OCR, real
# nlp_service extraction, real validation, real Mongo + SQL Server writes.
# Slower than the other test files (real OCR takes a few seconds per
# file) - that's expected and correct for what these are checking.

from pathlib import Path

from fastapi.testclient import TestClient

from app.services.invoice_service import get_processed_document
from app.db.sqlserver import get_connection

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _upload(client: TestClient, path: Path, content_type: str = "application/pdf"):
    with open(path, "rb") as f:
        return client.post(
            "/invoices/upload",
            files={"file": (path.name, f, content_type)},
        )


# ---------------------------------------------------------------------------
# processed / needs_review - a real, clean single invoice
# ---------------------------------------------------------------------------

def test_upload_real_invoice_is_processed_or_needs_review(client: TestClient):
    """
    We don't pin the exact status here - real OCR output can vary slightly
    run to run (e.g. if a field like invoice_date is borderline), and
    that's a genuine "needs_review" case, not a test failure. What we DO
    pin down: it must NOT be rejected or failed, and the three
    SQL-eligibility fields must have actually been extracted.
    """
    response = _upload(client, FIXTURES_DIR / "zepto_invoice.pdf")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] in {"processed", "needs_review"}
    assert body["extracted_fields"]["multiple_documents_detected"] is False
    assert body["extracted_fields"]["vendor_name"] is not None
    assert body["extracted_fields"]["invoice_number"] is not None
    assert body["extracted_fields"]["total_amount"] is not None

    # Confirm it actually persisted to Mongo, not just returned in the response.
    mongo_doc = get_processed_document(body["document_id"])
    assert mongo_doc is not None
    assert mongo_doc["status"] == body["status"]
    assert mongo_doc["raw_ocr_text"]  # non-empty OCR text was captured

    # Confirm the SQL write happened (eligibility fields were all present).
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM Invoices WHERE document_id = ?", body["document_id"]
        )
        assert cursor.fetchone()[0] == 1


def test_uploading_same_real_invoice_twice_flags_second_as_duplicate(client: TestClient):
    first = _upload(client, FIXTURES_DIR / "zepto_invoice.pdf")
    assert first.json()["is_duplicate"] is False

    second = _upload(client, FIXTURES_DIR / "zepto_invoice.pdf")
    second_body = second.json()
    assert second_body["is_duplicate"] is True
    assert second_body["status"] == "needs_review"
    assert any("duplicate" in w.lower() for w in second_body["warnings"])

    # Both uploads still get their own SQL row (audit-trail design, Phase 8).
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM Invoices WHERE invoice_number = ?",
            get_processed_document(second_body["document_id"])["extracted_fields"]["invoice_number"],
        )
        assert cursor.fetchone()[0] == 2


# ---------------------------------------------------------------------------
# rejected - real multi-invoice PDF
# ---------------------------------------------------------------------------

def test_upload_multi_invoice_pdf_is_rejected(client: TestClient):
    response = _upload(client, FIXTURES_DIR / "multiple_invoice.pdf")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "rejected"
    assert body["extracted_fields"]["multiple_documents_detected"] is True
    assert body["extracted_fields"]["vendor_name"] is None
    assert any("multiple invoices" in w.lower() for w in body["warnings"])

    # Rejected documents must never reach SQL Server (Phase 7/8 design rule).
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM Invoices WHERE document_id = ?", body["document_id"]
        )
        assert cursor.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# failed - a corrupted / non-PDF file wearing a .pdf extension
# ---------------------------------------------------------------------------

def test_upload_corrupted_pdf_returns_failed(client: TestClient, tmp_path):
    fake_pdf = tmp_path / "corrupted.pdf"
    fake_pdf.write_bytes(b"This is plain text, not a real PDF structure at all.")

    response = _upload(client, fake_pdf)
    assert response.status_code == 200  # per Phase 6: failures are 200 + status="failed"

    body = response.json()
    assert body["status"] == "failed"
    assert body["extracted_fields"] is None
    assert len(body["warnings"]) == 1

    mongo_doc = get_processed_document(body["document_id"])
    assert mongo_doc is not None
    assert mongo_doc["status"] == "failed"
    assert mongo_doc["raw_ocr_text"] is None
    assert mongo_doc["extracted_fields"] is None

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) FROM Invoices WHERE document_id = ?", body["document_id"]
        )
        assert cursor.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# GET /invoices/{document_id} - 404 for unknown ids
# ---------------------------------------------------------------------------

def test_get_unknown_document_id_returns_404(client: TestClient):
    response = client.get("/invoices/doc_doesnotexist")
    assert response.status_code == 404