from app.models.schemas import ExtractedFields, ValidationResult

REQUIRED_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "total_amount")

FIELD_WARNING_MESSAGES = {
    "vendor_name": "Vendor name could not be reliably extracted",
    "invoice_number": "Invoice number is missing or could not be extracted",
    "invoice_date": "Invoice date could not be confidently extracted",
    "total_amount": "Total amount could not be extracted",
}

# Allow rounding differences up to whichever is larger: a flat amount or a
# small percentage of the total. Real invoices round tax lines individually,
# so subtotal + tax rarely equals total exactly down to the last paisa/cent.
FLAT_TOLERANCE = 1.0
PERCENT_TOLERANCE = 0.01  # 1%


def validate_fields(fields: ExtractedFields) -> ValidationResult:
    """
    Runs required-field checks and a subtotal+tax-vs-total consistency
    check against extracted invoice fields, and returns an overall status
    plus a list of human-readable warnings.
    """
    if fields.multiple_documents_detected:
        return ValidationResult(
            status="rejected",
            warnings=[
                "Multiple invoices appear to be concatenated in a single "
                "document. Automatic field extraction was skipped rather "
                "than risk merging data from different invoices."
            ],
        )

    warnings: list[str] = []

    for field_name in REQUIRED_FIELDS:
        if getattr(fields, field_name) is None:
            warnings.append(FIELD_WARNING_MESSAGES[field_name])

    if (
        fields.subtotal is not None
        and fields.tax_amount is not None
        and fields.total_amount is not None
    ):
        expected_total = fields.subtotal + fields.tax_amount
        difference = abs(expected_total - fields.total_amount)
        tolerance = max(FLAT_TOLERANCE, fields.total_amount * PERCENT_TOLERANCE)

        if difference > tolerance:
            warnings.append(
                f"Total amount ({fields.total_amount}) does not match "
                f"subtotal + tax ({expected_total:.2f}); difference of "
                f"{difference:.2f} exceeds expected rounding tolerance"
            )

    status = "needs_review" if warnings else "processed"
    return ValidationResult(status=status, warnings=warnings)