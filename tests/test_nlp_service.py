# tests/test_nlp_service.py
#
# Synthetic-but-realistic OCR text snippets, each crafted to exercise one
# specific extraction path. Using crafted text (not live Tesseract output)
# keeps these tests deterministic and fast - full-pipeline behavior against
# real scanned files is covered separately by the end-to-end tests.

from app.services.nlp_service import (
    extract_fields,
    _extract_vendor_name,
    _extract_total,
    _clean_vendor_candidate,
    detect_multiple_documents,
)


# ---------------------------------------------------------------------------
# Vendor extraction - 3 tiers
# ---------------------------------------------------------------------------

def test_vendor_tier1_explicit_label():
    text = "Seller Name: ABC Traders Pvt Ltd\nGSTIN: 27ABCDE1234F1Z5\n"
    assert _extract_vendor_name(text) == "ABC Traders Pvt Ltd"


def test_vendor_tier1_survives_pipe_before_linebreak():
    """
    Phase 5 bug fix: a stray OCR '|' between vendor name and line break
    used to break the old (?:\\n|$) end-anchor, falling through to the
    unreliable spaCy tier. The lookahead-based terminator must handle it.
    """
    text = "Sold By: Geddit Convenience Private Limited | GSTIN: 29ABCDE1234F1Z5\n"
    assert _extract_vendor_name(text) == "Geddit Convenience Private Limited"


def test_vendor_tier2_same_line_as_invoice_no():
    text = "XYZ Electronics    Invoice No: INV-500\nDate: 01/01/2026\n"
    assert _extract_vendor_name(text) == "XYZ Electronics"


def test_vendor_tier2_gap_never_crosses_newline():
    """
    VENDOR_LINE_PATTERN's gap is [ \\t]+, not \\s+, specifically so it can
    never join two separate lines. A vendor name on its own line, with
    'Invoice No' on the NEXT line, must NOT match tier 2 at all.
    """
    text = "Some Random Header Text\nInvoice No: INV-999\n"
    # Tier 2 should not fire since there's no same-line match; falls
    # through toward the spaCy tier (which may or may not find anything -
    # what matters here is tier 2 doesn't wrongly grab "Some Random Header Text").
    from app.services.nlp_service import VENDOR_LINE_PATTERN
    assert VENDOR_LINE_PATTERN.search(text) is None


def test_vendor_tier3_spacy_fallback_before_bill_to():
    text = (
        "TAX INVOICE\n"
        "Acme Corp International Ltd\n"
        "Bill To: John Smith, 123 Main Street\n"
        "Invoice Number XYZ123\n"
    )
    vendor = _extract_vendor_name(text)
    assert vendor is not None
    assert "John Smith" not in vendor


# ---------------------------------------------------------------------------
# _clean_vendor_candidate - trailing OCR noise stripping
# ---------------------------------------------------------------------------

def test_clean_vendor_strips_short_noise_tokens():
    # "Been", "ane", "hei" are all <=4 chars and not legit suffixes.
    result = _clean_vendor_candidate("Geddit Convenience Private Limited Been ane hei")
    assert result == "Geddit Convenience Private Limited"


def test_clean_vendor_protects_legit_business_suffix():
    result = _clean_vendor_candidate("Acme Traders Pvt Ltd")
    assert result == "Acme Traders Pvt Ltd"


def test_clean_vendor_protects_long_legitimate_words():
    """
    Regression check from Phase 9: raising the noise threshold from <=2 to
    <=4 chars must NOT eat real words. 'LIMITED' is 7 chars - safe either way.
    """
    result = _clean_vendor_candidate("BL2-NYKAA E-RETAIL LIMITED")
    assert result == "BL2-NYKAA E-RETAIL LIMITED"


def test_clean_vendor_always_keeps_the_last_remaining_token():
    """
    The stripping loop is `while len(tokens) > 1`, which structurally
    guarantees at least one token always survives - it stops BEFORE ever
    touching the last remaining token, regardless of how short/noisy that
    token looks. "ab cd ef" -> strip "ef" -> strip "cd" -> stop at ["ab"].
    """
    result = _clean_vendor_candidate("ab cd ef")
    assert result == "ab"


def test_clean_vendor_never_returns_empty_string():
    # Whitespace-only input is the actual path that reaches the `or None`
    # fallback: .split() on "   " yields zero tokens, so the stripping
    # loop never even runs.
    result = _clean_vendor_candidate("   ")
    assert result is None


def test_clean_vendor_handles_none_input():
    assert _clean_vendor_candidate(None) is None


# ---------------------------------------------------------------------------
# Total extraction - 5 tiers, most-reliable-first
# ---------------------------------------------------------------------------

def test_total_tier1_amount_of_inr_sentence():
    text = "Some pricing table too garbled to parse.\nAmount of INR 518.806 settled via UPI.\n"
    assert _extract_total(text) == 518.806


def test_total_tier2_invoice_value_label():
    text = "Invoice Value: 1,250.00\n"
    assert _extract_total(text) == 1250.00


def test_total_tier3_grand_total_label():
    text = "Item Total: 190.00\nGST: 3.00\nGRAND TOTAL: 193.00\n"
    assert _extract_total(text) == 193.00


def test_total_tier4_total_value_row_last_number():
    text = "Total Value 202.00 5.05 5.05 212.10\n"
    assert _extract_total(text) == 212.10


def test_total_tier5_line_anchored_total_label():
    text = "Item(s) Total: 100.00\nSubtotal: 100.00\nTOTAL: 118.00\n"
    # Must match the line-start-anchored "TOTAL", not "Item(s) Total" or
    # "Subtotal" - this is the confirmed real bug from the Phase 4/5 handoff.
    assert _extract_total(text) == 118.00


def test_total_returns_none_when_nothing_matches():
    assert _extract_total("No pricing information anywhere in this text.") is None


# ---------------------------------------------------------------------------
# Multiple-document detection - distinct-value counting
# ---------------------------------------------------------------------------

def test_single_invoice_repeated_across_pages_is_not_flagged():
    """
    A 2-page scan of the SAME invoice repeats its invoice number - this
    must NOT be flagged as multiple documents (raw-count would false-positive).
    """
    text = (
        "Page 1\nInvoice No: INV-100\nGSTIN: 27ABCDE1234F1Z5\n"
        "Page 2 (continued)\nInvoice No: INV-100\nGSTIN: 27ABCDE1234F1Z5\n"
    )
    assert detect_multiple_documents(text) is False


def test_two_distinct_invoice_numbers_is_flagged():
    text = "Invoice No: INV-100\n...\nInvoice No: INV-200\n"
    assert detect_multiple_documents(text) is True


def test_two_distinct_gstins_is_flagged():
    text = "GSTIN: 27ABCDE1234F1Z5\nGSTIN: 29XYZAB5678G2Y6\n"
    assert detect_multiple_documents(text) is True


def test_multi_document_detection_skips_all_field_extraction():
    text = "Invoice No: INV-100\n...\nInvoice No: INV-200\n"
    fields = extract_fields(text)
    assert fields.multiple_documents_detected is True
    assert fields.vendor_name is None
    assert fields.total_amount is None


# ---------------------------------------------------------------------------
# End-to-end (within nlp_service, no OCR) - a full realistic sample
# ---------------------------------------------------------------------------

def test_extract_fields_full_clean_sample():
    text = (
        "ABC Electronics Pvt Ltd\n"
        "Invoice No: INV-1024\n"
        "Invoice Date: 12/08/2026\n"
        "Subtotal: 4500.00\n"
        "GST: 810.00\n"
        "TOTAL: 5310.00\n"
        "GSTIN: 27ABCDE1234F1Z5\n"
    )
    fields = extract_fields(text)
    assert fields.multiple_documents_detected is False
    assert fields.invoice_number == "INV-1024"
    assert fields.invoice_date == "2026-08-12"
    assert fields.subtotal == 4500.00
    assert fields.tax_amount == 810.00
    assert fields.total_amount == 5310.00
    assert fields.currency == "INR"