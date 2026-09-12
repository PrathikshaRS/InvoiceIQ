# tests/test_validation_service.py
#
# Pure-function tests - no database, no fixtures needed beyond what
# conftest.py already provides automatically (which is harmless here
# since these tests never touch a DB).

from app.models.schemas import ExtractedFields
from app.services.validation_service import validate_fields


def _fields(**overrides) -> ExtractedFields:
    """All four required fields present and consistent by default."""
    base = dict(
        vendor_name="ABC Traders Pvt Ltd",
        invoice_number="INV-1024",
        invoice_date="2026-08-12",
        subtotal=4500.0,
        tax_amount=810.0,
        total_amount=5310.0,
        currency="INR",
        multiple_documents_detected=False,
    )
    base.update(overrides)
    return ExtractedFields(**base)


def test_all_required_fields_present_and_consistent_is_processed():
    result = validate_fields(_fields())
    assert result.status == "processed"
    assert result.warnings == []


def test_missing_vendor_name_gives_needs_review_with_warning():
    result = validate_fields(_fields(vendor_name=None))
    assert result.status == "needs_review"
    assert any("Vendor name" in w for w in result.warnings)


def test_missing_multiple_required_fields_gives_one_warning_each():
    result = validate_fields(_fields(vendor_name=None, invoice_number=None))
    assert result.status == "needs_review"
    assert len(result.warnings) == 2


def test_subtotal_and_tax_null_is_still_processed():
    """
    subtotal/tax are deliberately excluded from REQUIRED_FIELDS (Phase 5
    decision) - many legitimate formats never state them cleanly.
    """
    result = validate_fields(_fields(subtotal=None, tax_amount=None))
    assert result.status == "processed"
    assert result.warnings == []


def test_total_mismatch_beyond_tolerance_gives_warning():
    # subtotal + tax = 5310, but total claims 6000 - way outside tolerance
    result = validate_fields(_fields(total_amount=6000.0))
    assert result.status == "needs_review"
    assert any("does not match" in w for w in result.warnings)


def test_total_mismatch_within_flat_tolerance_is_processed():
    # subtotal + tax = 5310.00; total = 5310.50 -> diff 0.50, under the
    # ₹1 flat tolerance floor.
    result = validate_fields(_fields(total_amount=5310.50))
    assert result.status == "processed"
    assert result.warnings == []


def test_total_mismatch_within_percent_tolerance_is_processed():
    # total = 10000 -> 1% tolerance = 100. subtotal+tax must be within
    # 100 of 10000 to pass.
    result = validate_fields(
        _fields(subtotal=9950.0, tax_amount=0.0, total_amount=10000.0)
    )
    assert result.status == "processed"
    assert result.warnings == []


def test_multiple_documents_detected_short_circuits_to_rejected():
    """
    The multi-document short-circuit must win even if other fields would
    otherwise look fine - and it should produce exactly one warning, not
    also run the required-field/consistency checks.
    """
    fields = ExtractedFields(multiple_documents_detected=True)
    result = validate_fields(fields)
    assert result.status == "rejected"
    assert len(result.warnings) == 1
    assert "Multiple invoices" in result.warnings[0]