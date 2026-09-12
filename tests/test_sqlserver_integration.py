# tests/test_sqlserver_integration.py
#
# These tests hit the REAL invoiceiq_test SQL Server database (see
# conftest.py: clean_databases wipes Vendors/Invoices/Payments before each
# test). We deliberately don't mock pyodbc - Phase 7/8 found real bugs
# (the gstin NULL-uniqueness gotcha, route-ordering issues) that only a
# real database would surface.

from app.db.sqlserver import (
    get_connection,
    get_or_create_vendor,
    find_duplicate_invoice,
    save_invoice_to_sql,
    search_invoices,
    get_analytics,
)
from app.models.schemas import ExtractedFields
from app.services.invoice_service import maybe_save_to_sql, check_for_duplicate


def _fields(**overrides) -> ExtractedFields:
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


# ---------------------------------------------------------------------------
# get_or_create_vendor - dedup on vendor_name
# ---------------------------------------------------------------------------

def test_get_or_create_vendor_reuses_existing_row():
    with get_connection() as conn:
        cursor = conn.cursor()
        first_id = get_or_create_vendor(cursor, "Zepto Marketplace Pvt Ltd", gstin=None)
        second_id = get_or_create_vendor(cursor, "Zepto Marketplace Pvt Ltd", gstin=None)
        conn.commit()

    assert first_id == second_id


def test_get_or_create_vendor_different_names_get_different_ids():
    with get_connection() as conn:
        cursor = conn.cursor()
        id_a = get_or_create_vendor(cursor, "Vendor A", gstin=None)
        id_b = get_or_create_vendor(cursor, "Vendor B", gstin=None)
        conn.commit()

    assert id_a != id_b


# ---------------------------------------------------------------------------
# maybe_save_to_sql - the eligibility gate
# ---------------------------------------------------------------------------

def test_maybe_save_to_sql_writes_row_when_eligible():
    maybe_save_to_sql("doc_test001", _fields(), status="processed")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM Invoices WHERE document_id = ?", "doc_test001")
        assert cursor.fetchone()[0] == 1


def test_maybe_save_to_sql_skips_rejected_status():
    maybe_save_to_sql("doc_test002", _fields(), status="rejected")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM Invoices WHERE document_id = ?", "doc_test002")
        assert cursor.fetchone()[0] == 0


def test_maybe_save_to_sql_skips_when_total_amount_missing():
    """
    needs_review status alone doesn't block a SQL write - only a missing
    one of the three required fields does. This test exercises that
    specific distinction, called out explicitly in the Phase 7 handoff.
    """
    maybe_save_to_sql("doc_test003", _fields(total_amount=None), status="needs_review")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM Invoices WHERE document_id = ?", "doc_test003")
        assert cursor.fetchone()[0] == 0


def test_maybe_save_to_sql_writes_needs_review_when_fields_present():
    maybe_save_to_sql("doc_test004", _fields(), status="needs_review")

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM Invoices WHERE document_id = ?", "doc_test004")
        assert cursor.fetchone()[0] == "needs_review"


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def test_find_duplicate_invoice_returns_none_when_no_prior_match():
    result = find_duplicate_invoice("Nobody Yet Ltd", "INV-0001", 100.0)
    assert result is None


def test_check_for_duplicate_finds_prior_matching_invoice():
    # First upload: goes through cleanly, nothing to match against yet.
    fields = _fields(vendor_name="Repeat Vendor Ltd", invoice_number="INV-777", total_amount=999.0)
    assert check_for_duplicate(fields) is None
    maybe_save_to_sql("doc_original", fields, status="processed")

    # Second "upload" of the same vendor+invoice_number+total_amount combo.
    duplicate = check_for_duplicate(fields)
    assert duplicate is not None
    assert duplicate["document_id"] == "doc_original"


def test_check_for_duplicate_does_not_match_on_partial_field_overlap():
    fields_a = _fields(vendor_name="Vendor X", invoice_number="INV-1", total_amount=100.0)
    maybe_save_to_sql("doc_a", fields_a, status="processed")

    # Same vendor + invoice number, but DIFFERENT total_amount - must not match.
    fields_b = _fields(vendor_name="Vendor X", invoice_number="INV-1", total_amount=200.0)
    assert check_for_duplicate(fields_b) is None


def test_check_for_duplicate_returns_none_when_required_field_missing():
    # invoice_number is None - eligibility gate should skip the SQL query entirely.
    fields = _fields(invoice_number=None)
    assert check_for_duplicate(fields) is None


# ---------------------------------------------------------------------------
# search_invoices - AND-combined filters
# ---------------------------------------------------------------------------

def test_search_with_no_filters_returns_all_rows():
    maybe_save_to_sql("doc_s1", _fields(vendor_name="Vendor One", total_amount=100.0), "processed")
    maybe_save_to_sql("doc_s2", _fields(vendor_name="Vendor Two", total_amount=200.0), "processed")

    results = search_invoices()
    assert len(results) == 2


def test_search_by_vendor_partial_match():
    maybe_save_to_sql("doc_s3", _fields(vendor_name="Geddit Convenience Ltd", invoice_number="A1", total_amount=100.0), "processed")
    maybe_save_to_sql("doc_s4", _fields(vendor_name="Nykaa Retail Ltd", invoice_number="A2", total_amount=200.0), "processed")

    results = search_invoices(vendor_name="Geddit")
    assert len(results) == 1
    assert results[0]["vendor_name"] == "Geddit Convenience Ltd"


def test_search_amount_range_and_vendor_combine_with_and():
    """
    Mirrors the Phase 9 handoff's manually-confirmed case: vendor filter
    + min_amount together must narrow results (AND), not widen them (OR).
    """
    maybe_save_to_sql("doc_s5", _fields(vendor_name="Geddit Ltd", invoice_number="B1", total_amount=184.0), "processed")
    maybe_save_to_sql("doc_s6", _fields(vendor_name="Geddit Ltd", invoice_number="B2", total_amount=193.0), "processed")
    maybe_save_to_sql("doc_s7", _fields(vendor_name="Other Vendor", invoice_number="B3", total_amount=300.0), "processed")

    results = search_invoices(vendor_name="Geddit", min_amount=190.0)
    assert len(results) == 1
    assert results[0]["total_amount"] == 193.0


def test_search_by_status():
    maybe_save_to_sql("doc_s8", _fields(vendor_name="V1", invoice_number="C1", total_amount=50.0), "processed")
    maybe_save_to_sql("doc_s9", _fields(vendor_name="V2", invoice_number="C2", total_amount=60.0), "needs_review")

    results = search_invoices(status="needs_review")
    assert len(results) == 1
    assert results[0]["status"] == "needs_review"


# ---------------------------------------------------------------------------
# get_analytics - totals, top vendors by VALUE not count, status breakdown
# ---------------------------------------------------------------------------

def test_analytics_totals_and_status_breakdown():
    maybe_save_to_sql("doc_a1", _fields(vendor_name="V1", invoice_number="D1", total_amount=100.0), "processed")
    maybe_save_to_sql("doc_a2", _fields(vendor_name="V2", invoice_number="D2", total_amount=200.0), "needs_review")

    result = get_analytics()
    assert result["total_invoices"] == 2
    assert result["total_invoice_value"] == 300.0
    assert result["invoices_by_status"] == {"processed": 1, "needs_review": 1}


def test_analytics_top_vendors_ranked_by_value_not_count():
    """
    Vendor A: 1 large invoice (1000). Vendor B: 3 small invoices (100 each
    = 300 total). Vendor A must rank first despite fewer invoices - ranking
    is by summed total_amount, per the Phase 9 design decision.
    """
    maybe_save_to_sql("doc_v1", _fields(vendor_name="Vendor A", invoice_number="E1", total_amount=1000.0), "processed")
    maybe_save_to_sql("doc_v2", _fields(vendor_name="Vendor B", invoice_number="E2", total_amount=100.0), "processed")
    maybe_save_to_sql("doc_v3", _fields(vendor_name="Vendor B", invoice_number="E3", total_amount=100.0), "processed")
    maybe_save_to_sql("doc_v4", _fields(vendor_name="Vendor B", invoice_number="E4", total_amount=100.0), "processed")

    result = get_analytics()
    top_vendors = result["top_vendors"]
    assert top_vendors[0]["vendor_name"] == "Vendor A"
    assert top_vendors[0]["total_value"] == 1000.0
    assert top_vendors[1]["vendor_name"] == "Vendor B"
    assert top_vendors[1]["total_value"] == 300.0


def test_analytics_with_no_invoices_returns_zeroed_totals():
    result = get_analytics()
    assert result["total_invoices"] == 0
    assert result["total_invoice_value"] == 0.0
    assert result["top_vendors"] == []
    assert result["invoices_by_status"] == {}