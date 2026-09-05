import re
from datetime import datetime
from typing import Optional

import spacy

from app.models.schemas import ExtractedFields

_nlp = spacy.load("en_core_web_sm")

# ---------------------------------------------------------------------------
# Regex patterns
#
# OCR often garbles currency symbols (₹ -> %, =, ~, X, £, ¥) and mangles
# spacing, so patterns favor small flexible gaps over matching exact
# symbols. Amounts accept 2 or 3 decimal digits (some real invoices show
# 3-decimal paisa figures from tax rounding, e.g. "12.353"), with optional
# whitespace around the dot itself (OCR sometimes inserts a stray space).
# ---------------------------------------------------------------------------

INVOICE_NUMBER_PATTERN = re.compile(r"Invoice\s*(?:No\.?|Number|#)\s*:?\s*([A-Za-z0-9\-/]+)", re.IGNORECASE)

# Invoice date: prefer the specific "Invoice Date" label over a generic
# "Date" label, since invoices often also show due/payment/order dates -
# a generic match risks grabbing the wrong one.
INVOICE_DATE_PATTERN = re.compile(r"Invoice\s*Date\s*:?\s*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})", re.IGNORECASE)
DATE_PATTERN = re.compile(r"\bDate\s*:?\s*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})", re.IGNORECASE)

SUBTOTAL_PATTERN = re.compile(r"Subtotal\s*:?\s*.{0,3}?([\d,]+\s*\.\s*\d{2,3})", re.IGNORECASE)
TAX_PATTERN = re.compile(r"\bGST\b[^:\n]{0,10}:?\s*.{0,3}?([\d,]+\s*\.\s*\d{2,3})", re.IGNORECASE)
TOTAL_PATTERN = re.compile(r"(?:^|\n)\s*TOTAL\b\s*:?\s*.{0,3}?([\d,]+\s*\.\s*\d{2,3})", re.IGNORECASE | re.MULTILINE)
GRAND_TOTAL_PATTERN = re.compile(r"GRAND\s*TOTAL\s*:?\s*.{0,10}?([\d,]+\s*\.\s*\d{2,3})", re.IGNORECASE)
INVOICE_VALUE_PATTERN = re.compile(r"Invoice\s*Value\s*:?\s*.{0,3}?([\d,]+\s*\.\s*\d{2,3})", re.IGNORECASE)

# "Amount of INR 518.806 settled..." - an explicit plain-text restatement
# of the final total. Very reliable when present: even if the pricing
# table itself is too OCR-garbled to parse, this sentence often survives.
AMOUNT_INR_PATTERN = re.compile(r"\bAmount\s+(?:of\s+)?INR\s*([\d,]+\s*\.\s*\d{2,3})", re.IGNORECASE)

# Generic amount finder, used only for scanning a single already-identified
# line (e.g. a "Total Value" summary row) for all the numbers on it.
LINE_AMOUNT_PATTERN = re.compile(r"\b\d[\d,]*\s*\.\s*\d{2,3}\b")

GSTIN_PATTERN = re.compile(r"GSTIN\s*(?:Number)?\s*:?\s*([0-9A-Z]{15})", re.IGNORECASE)
CURRENCY_PATTERN = re.compile(r"\b(INR|USD|EUR|GBP|Rs\.?)\b", re.IGNORECASE)

# Vendor name: same-line-as-"Invoice No" heuristic. Gap uses [ \t] (not \s)
# so it can never cross a newline - \s would match "\n" too and incorrectly
# join two separate lines together.
VENDOR_LINE_PATTERN = re.compile(r"^(.*?)[ \t]+Invoice\s*No", re.IGNORECASE | re.MULTILINE)

# Vendor name: explicit label, the most reliable signal when present.
VENDOR_LABEL_PATTERN = re.compile(
    r"(?:Seller\s*Name|Legal\s*Entity\s*Name|Vendor\s*Name|Sold\s*By)"
    r"\s*:?\s*([A-Za-z0-9&.,'\-\s]{3,80}?)(?=\s*(?:\n|\||GSTIN|CIN|PAN|$))",
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
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None  # couldn't confidently parse - leave null rather than guess


def _extract_amounts_from_line(line: str) -> list[float]:
    """
    Extracts every decimal amount on a single line, in order left to right.
    Used for parsing tabular summary rows like:
        'Total Value 202.00 5.05 5.05 212.10'
    where column position (not a label) tells us what each number means.
    """
    amounts = []
    for match in LINE_AMOUNT_PATTERN.findall(line):
        amount = _parse_amount(match)
        if amount is not None:
            amounts.append(amount)
    return amounts

# Short trailing tokens that are legitimate parts of a business name,
# not OCR noise - protected from the trailing-noise strip below.
_LEGIT_SHORT_SUFFIXES = {"ltd", "llp", "inc", "co", "pvt", "llc", "plc"}


def _clean_vendor_candidate(candidate: Optional[str]) -> Optional[str]:
    """
    Strips trailing OCR noise bled in from background graphics near the
    vendor name - short, meaningless trailing tokens like "Es a ga ae".

    Works token-by-token from the end: a trailing token is stripped if
    it's short (<=2 chars) AND not a recognized business-name suffix
    (case-insensitive), continuing until a real/longer token is hit.
    Never strips down to nothing - an honest null beats an empty string.
    """
    if not candidate:
        return None

    tokens = candidate.split()
    while len(tokens) > 1:
        last = tokens[-1].strip(".,")
        if len(last) <= 2 and last.lower() not in _LEGIT_SHORT_SUFFIXES:
            tokens.pop()
        else:
            break

    cleaned = " ".join(tokens).strip()
    return cleaned or None


def _extract_vendor_name(text: str) -> Optional[str]:
    """
    Three-tier vendor extraction, most reliable first:
    1. Explicit label (e.g. 'Legal Entity Name:', 'Seller Name:') - the
       document is telling us the vendor directly, so trust it over guessing.
    2. Same-line-as-'Invoice No' heuristic, for templates without an
       explicit label but where the name sits next to the invoice number.
    3. spaCy NER fallback, scoped before the bill-to section so we never
       mistake the customer's name/address for the vendor.

    Every path funnels through _clean_vendor_candidate() before returning,
    since OCR background-graphic bleed can affect any of the three tiers.
    """
    label_match = VENDOR_LABEL_PATTERN.search(text)
    if label_match:
        return _clean_vendor_candidate(label_match.group(1).strip())

    line_match = VENDOR_LINE_PATTERN.search(text)
    if line_match:
        candidate = line_match.group(1).strip()
        if candidate and candidate.upper() not in {"INVOICE", "RECEIPT", "TAX INVOICE"}:
            return _clean_vendor_candidate(candidate)

    header = re.split(r"bill\s*to", text, maxsplit=1, flags=re.IGNORECASE)[0][:400]
    doc = _nlp(header)
    orgs = [ent.text.strip() for ent in doc.ents if ent.label_ == "ORG"]
    return _clean_vendor_candidate(orgs[0]) if orgs else None


def _extract_invoice_number(text: str) -> Optional[str]:
    match = INVOICE_NUMBER_PATTERN.search(text)
    if not match:
        return None
    # Strip trailing OCR punctuation noise, e.g. "23ZQUCKA00072158." -> "...158"
    value = match.group(1).strip().rstrip(".,:;")
    return value or None


def _extract_invoice_date(text: str) -> Optional[str]:
    """
    Prefers the specific 'Invoice Date' label. If that label exists but the
    digits it captured don't form a valid date (common OCR digit-dropping),
    we deliberately do NOT fall back to a generic date elsewhere in the
    document - that would risk silently returning the wrong date (e.g. a
    due date) under a false label. Only fall back to a generic 'Date' when
    'Invoice Date' wasn't found at all.
    """
    match = INVOICE_DATE_PATTERN.search(text)
    if match:
        return _parse_date(match.group(1))

    match = DATE_PATTERN.search(text)
    return _parse_date(match.group(1)) if match else None


def _extract_total_value_row(text: str) -> Optional[list[float]]:
    """
    Finds a 'Total Value' summary row (common in restaurant/retail
    invoices) and returns all decimal amounts found on it, in order.
    Expected layout: [net/subtotal, tax component(s)..., final total].
    Returns None if no such row exists or it has too few numbers to
    make sense of positionally.
    """
    for line in text.splitlines():
        if re.search(r"Total\s*Value", line, re.IGNORECASE):
            amounts = _extract_amounts_from_line(line)
            if len(amounts) >= 2:
                return amounts
    return None


def _extract_subtotal(text: str) -> Optional[float]:
    """
    Prefer an explicit 'Subtotal:' label. Fall back to the first number in
    a 'Total Value' row (the net/taxable amount before tax) for invoice
    formats that summarize via a table row instead of a labeled subtotal.
    """
    match = SUBTOTAL_PATTERN.search(text)
    if match:
        return _parse_amount(match.group(1))

    row = _extract_total_value_row(text)
    if row and len(row) >= 4:
        return row[0]
    return None


def _extract_tax(text: str) -> Optional[float]:
    """
    Tries several signals, most reliable first:
    1. Explicit 'GST (18%): X' style label.
    2. A 'Total Value' row: everything between the first (net) and last
       (final total) figure is treated as tax components and summed -
       e.g. [202.00, 5.05, 5.05, 212.10] -> tax = 5.05 + 5.05 = 10.10.
    3. Summing the last number on any line mentioning CGST/SGST.
    4. Last resort: the last number on any line merely mentioning GST.
    """
    match = TAX_PATTERN.search(text)
    if match:
        return _parse_amount(match.group(1))

    row = _extract_total_value_row(text)
    if row and len(row) >= 4:
        return round(sum(row[1:-1]), 2)

    tax_components = []
    for line in text.splitlines():
        if re.search(r"\b(?:CGST|SGST)\b", line, re.IGNORECASE):
            amounts = _extract_amounts_from_line(line)
            if amounts:
                tax_components.append(amounts[-1])
    if tax_components:
        return round(sum(tax_components), 2)

    for line in text.splitlines():
        if re.search(r"\bGST\b", line, re.IGNORECASE):
            amounts = _extract_amounts_from_line(line)
            if amounts:
                return amounts[-1]
    return None


def _extract_total(text: str) -> Optional[float]:
    """
    Tries several signals, most reliable first:
    1. 'Amount of INR X settled...' - explicit plain-text restatement.
    2. 'Invoice Value:' label - retail/e-commerce style invoices.
    3. 'GRAND TOTAL' label - explicit final-settled-amount label used by
       some e-commerce invoices (e.g. Nykaa); trusted at face value since
       it's unambiguous.
    4. Last number in a 'Total Value' summary row.
    5. A 'TOTAL' label anchored to start of line - excludes 'Item(s)
       Total'/'Subtotal' style rows.
    """
    match = AMOUNT_INR_PATTERN.search(text)
    if match:
        return _parse_amount(match.group(1))

    match = INVOICE_VALUE_PATTERN.search(text)
    if match:
        return _parse_amount(match.group(1))

    match = GRAND_TOTAL_PATTERN.search(text)
    if match:
        return _parse_amount(match.group(1))

    row = _extract_total_value_row(text)
    if row:
        return row[-1]

    match = TOTAL_PATTERN.search(text)
    return _parse_amount(match.group(1)) if match else None


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


    gstin_match = GSTIN_PATTERN.search(raw_text)

    return ExtractedFields(
        vendor_name=_extract_vendor_name(raw_text),
        invoice_number=_extract_invoice_number(raw_text),
        invoice_date=_extract_invoice_date(raw_text),
        subtotal=_extract_subtotal(raw_text),
        tax_amount=_extract_tax(raw_text),
        total_amount=_extract_total(raw_text),
        currency=_extract_currency(raw_text, gstin_found=bool(gstin_match)),
        multiple_documents_detected=False,
    )