# app/db/sqlserver.py

import pyodbc

from app.core.config import settings


def get_connection() -> pyodbc.Connection:
    """
    Opens a new connection to the invoiceiq database, scoped to a single
    operation. Callers are expected to use this in a `with` block so the
    connection closes automatically, even if an error occurs mid-write.
    """
    conn_str = (
        f"DRIVER={{{settings.sqlserver_driver}}};"
        f"SERVER={settings.sqlserver_host},{settings.sqlserver_port};"
        f"DATABASE={settings.sqlserver_database};"
        f"UID={settings.sqlserver_username};"
        f"PWD={settings.sqlserver_password};"
        f"TrustServerCertificate=yes;"
    )
    return pyodbc.connect(conn_str)


def get_or_create_vendor(
    cursor: pyodbc.Cursor,
    vendor_name: str,
    gstin: str | None,
) -> int:
    """
    Looks up a vendor by name. Returns the existing vendor_id if found,
    otherwise inserts a new Vendors row and returns its generated id.

    Matching on vendor_name (not gstin) because gstin is frequently null
    in our extracted data - see KNOWN ISSUES on subtotal/tax gaps, the
    same OCR limitations affect gstin extraction on several formats.
    """
    cursor.execute(
        "SELECT vendor_id FROM Vendors WHERE vendor_name = ?",
        vendor_name,
    )
    row = cursor.fetchone()
    if row:
        return row.vendor_id

    cursor.execute(
        "INSERT INTO Vendors (vendor_name, gstin) OUTPUT INSERTED.vendor_id "
        "VALUES (?, ?)",
        vendor_name,
        gstin,
    )
    return cursor.fetchone().vendor_id


def insert_invoice(
    cursor: pyodbc.Cursor,
    vendor_id: int,
    document_id: str,
    invoice_number: str,
    invoice_date: str | None,
    subtotal: float | None,
    tax_amount: float | None,
    total_amount: float,
    currency: str | None,
    status: str,
) -> int:
    """
    Inserts a new Invoices row linked to the given vendor. document_id is
    the bridge back to the full Mongo record (raw OCR text, warnings,
    every extracted field) - SQL Server only holds the structured slice.
    """
    cursor.execute(
        """
        INSERT INTO Invoices (
            vendor_id, document_id, invoice_number, invoice_date,
            subtotal, tax_amount, total_amount, currency, status
        )
        OUTPUT INSERTED.invoice_id
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        vendor_id, document_id, invoice_number, invoice_date,
        subtotal, tax_amount, total_amount, currency, status,
    )
    return cursor.fetchone().invoice_id


def save_invoice_to_sql(
    document_id: str,
    vendor_name: str,
    gstin: str | None,
    invoice_number: str,
    invoice_date: str | None,
    subtotal: float | None,
    tax_amount: float | None,
    total_amount: float,
    currency: str | None,
    status: str,
) -> int:
    """
    Orchestrates the vendor lookup/create + invoice insert as a single
    transaction. If the invoice insert fails, the vendor lookup/creation
    is rolled back too - we never want an orphan vendor row from a failed
    invoice write.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        vendor_id = get_or_create_vendor(cursor, vendor_name, gstin)
        invoice_id = insert_invoice(
            cursor, vendor_id, document_id, invoice_number, invoice_date,
            subtotal, tax_amount, total_amount, currency, status,
        )
        conn.commit()
        return invoice_id