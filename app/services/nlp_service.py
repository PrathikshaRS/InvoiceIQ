import re
from datetime import datetime
from typing import Optional

import spacy

from app.models.schemas import ExtractedFields

_nlp = spacy.load("en_core_web_sm")

# ---------------------------------------------------------------------------
# Regex patterns
#
# OCR often garbles currency symbols (₹ -> %, =, ~, X, £) and mangles spacing,
# so patterns favor small flexible gaps over matching exact symbols. Amounts
# require a decimal (X.XX), with optional whitespace around the dot itself
# (OCR sometimes inserts a stray space, e.g. "20,768 .00") - this avoids
# accidentally matching unrelated bare numbers (years, codes, page numbers)
# while still tolerating common OCR spacing noise.
# ---------------------------------------------------------------------------

INVOICE_NUMBER_PATTERN = re.compile(r"Invoice\s*(?:No\.?|Number)\s*:?\s*([A-Za-z0-9\-/]+)", re.IGNORECASE)
DATE_PATTERN = re.compile(r"\bDate\s*:?\s*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})", re.IGNORECASE)
SUBTOTAL_PATTERN = re.compile(r"Subtotal\s*:?\s*.{0,3}?([\d,]+\s*\.\s*\d{2})", re.IGNORECASE)
TAX_PATTERN = re.compile(r"\bGST\b[^:\n]{0,10}:?\s*.{0,3}?([\d,]+\s*\.\s*\d{2})", re.IGNORECASE)
TOTAL_PATTERN = re.compile(r"(?<!sub)\bTOTAL\b\s*:?\s*.{0,3}?([\d,]+\s*\.\s*\d{2})", re.IGNORECASE)
INVOICE_VALUE_PATTERN = re.compile(r"Invoice\s*Value\s*:?\s*.{0,3}?([\d,]+\s*\.\s*\d{2})", re.IGNORECASE)
GSTIN_PATTERN = re.compile(r"GSTIN\s*(?:Number)?\s*:?\s*([0-9A-Z]{15})", re.IGNORECASE)
CURRENCY_PATTERN = re.compile(r"\b(INR|USD|EUR|GBP|Rs\.?)\b", re.IGNORECASE)

# Vendor name: same-line-as-"Invoice No" heuristic. Gap uses [ \t] (not \s)
# so it can never cross a newline - \s would match "\n" too and incorrectly
# join two separate lines together.
VENDOR_LINE_PATTERN = re.compile(r"^(.*?)[ \t]+Invoice\s*No", re.IGNORECASE | re.MULTILINE)

# Vendor name: explicit label, the most reliable signal when present.
VENDOR_LABEL_PATTERN = re.compile(
    r"(?:Seller\s*Name|Legal\s*Entity\s*Name|Restaurant\s*Name|Vendor\s*Name|Sold\s*By)"
    r"\s*:?\s*([A-Za-z0-9&.,'\-\s]{3,60}?)(?:\n|$)",
    re.IGNORECASE,
)


def _parse_amount(raw: Optional[str]) -> Optional[float]:
    """Converts '17,600 . 00' or '17,600.00' -> 17600.0. Returns None if unparseable."""
    if not raw:
        return None
    cleaned = raw.replace(",", "").replace(" ", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(raw: Optional[str]) -> Optional[str]:
    """Converts common dd/mm/yyyy style OCR dates into ISO format."""
    if not raw:
        return None
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None  # couldn't confidently parse - leave null rather than guess


def _extract_vendor_name(text: str) -> Optional[str]:
    """
    Three-tier vendor extraction, most reliable first:
    1. Explicit label (e.g. 'Legal Entity Name:', 'Seller Name:') - the
       document is telling us the vendor directly, so trust it over guessing.
    2. Same-line-as-'Invoice No' heuristic, for templates without an
       explicit label but where the name sits next to the invoice number.
    3. spaCy NER fallback, scoped before the bill-to section, for anything
       that doesn't match a known pattern.
    """
    label_match = VENDOR_LABEL_PATTERN.search(text)
    if label_match:
        return label_match.group(1).strip()

    line_match = VENDOR_LINE_PATTERN.search(text)
    if line_match:
        candidate = line_match.group(1).strip()
        if candidate and candidate.upper() not in {"INVOICE", "RECEIPT", "TAX INVOICE"}:
            return candidate

    header = re.split(r"bill\s*to", text, maxsplit=1, flags=re.IGNORECASE)[0][:400]
    doc = _nlp(header)
    orgs = [ent.text.strip() for ent in doc.ents if ent.label_ == "ORG"]
    return orgs[0] if orgs else None


def _extract_total(text: str) -> Optional[float]:
    """
    Prefer 'Invoice Value' when present - it's typically the final, rounded,
    authoritative total on retail/e-commerce style invoices. Falls back to
    a 'TOTAL' label for the more common business-invoice format.
    """
    invoice_value_match = INVOICE_VALUE_PATTERN.search(text)
    if invoice_value_match:
        return _parse_amount(invoice_value_match.group(1))
    total_match = TOTAL_PATTERN.search(text)
    return _parse_amount(total_match.group(1)) if total_match else None


def _extract_currency(text: str, gstin_found: bool) -> Optional[str]:
    """
    Prefer an explicit currency mention in the text (INR/USD/Rs./etc).
    Only fall back to inferring INR from a GSTIN if no currency was stated
    outright - a GSTIN is India-specific, so its presence is a reasonable
    (but weaker) signal, not a guess out of thin air.
    """
    match = CURRENCY_PATTERN.search(text)
    if match:
        token = match.group(1).upper().rstrip(".")
        return "INR" if token == "RS" else token
    return "INR" if gstin_found else None


def detect_multiple_documents(text: str) -> bool:
    """
    Heuristic check for multiple invoices concatenated into a single
    PDF/image. A single invoice's identifying markers (invoice number,
    GSTIN) may legitimately repeat if the same invoice spans multiple
    pages - so we count DISTINCT values, not raw occurrences. More than
    one distinct invoice number or GSTIN means genuinely separate
    invoices are present, not just a duplicated page.
    """
    invoice_numbers = {m.strip() for m in INVOICE_NUMBER_PATTERN.findall(text)}
    gstins = {m.strip() for m in GSTIN_PATTERN.findall(text)}
    return len(invoice_numbers) >= 2 or len(gstins) >= 2


def extract_fields(raw_text: str) -> ExtractedFields:
    """
    Runs all regex patterns + spaCy NER against raw OCR text and returns a
    structured ExtractedFields object. Any field that can't be reliably
    extracted is left as None rather than guessed - a wrong extraction is
    worse than an honest 'unknown'.

    If the document appears to contain multiple concatenated invoices,
    field extraction is skipped entirely and multiple_documents_detected
    is set instead - combining fields from different invoices would
    silently produce wrong data.
    """
    if detect_multiple_documents(raw_text):
        return ExtractedFields(multiple_documents_detected=True)

    invoice_number_match = INVOICE_NUMBER_PATTERN.search(raw_text)
    date_match = DATE_PATTERN.search(raw_text)
    subtotal_match = SUBTOTAL_PATTERN.search(raw_text)
    tax_match = TAX_PATTERN.search(raw_text)
    gstin_match = GSTIN_PATTERN.search(raw_text)

    return ExtractedFields(
        vendor_name=_extract_vendor_name(raw_text),
        invoice_number=invoice_number_match.group(1) if invoice_number_match else None,
        invoice_date=_parse_date(date_match.group(1) if date_match else None),
        subtotal=_parse_amount(subtotal_match.group(1) if subtotal_match else None),
        tax_amount=_parse_amount(tax_match.group(1) if tax_match else None),
        total_amount=_extract_total(raw_text),
        currency=_extract_currency(raw_text, gstin_found=bool(gstin_match)),
        multiple_documents_detected=False,
    )