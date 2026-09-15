# app/db/sqlserver.py

import pyodbc
from typing import Optional
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
    return pyodbc.connect(conn_str, timeout=60)


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

def find_duplicate_invoice(
    vendor_name: str,
    invoice_number: str,
    total_amount: float,
) -> Optional[dict]:
    """
    Looks for an existing invoice matching on vendor name + invoice number +
    total amount. This runs BEFORE the current document's own row is
    inserted, so a genuine match means a real prior upload, not a
    self-match.

    Returns {"document_id": ..., "invoice_id": ...} if found, else None.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT TOP 1 i.document_id, i.invoice_id
            FROM Invoices i
            JOIN Vendors v ON v.vendor_id = i.vendor_id
            WHERE v.vendor_name = ?
              AND i.invoice_number = ?
              AND i.total_amount = ?
            """,
            vendor_name, invoice_number, total_amount,
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {"document_id": row.document_id, "invoice_id": row.invoice_id}


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

def search_invoices(
    vendor_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    status: str | None = None,
) -> list[dict]:
    """
    Searches Invoices (joined to Vendors) using any combination of filters,
    combined with AND - e.g. vendor + min_amount together means BOTH must
    match, not either. Filters that are None are simply omitted from the
    WHERE clause rather than matched against.

    Every value is passed as a parameter (?), never string-formatted into
    the query, to avoid SQL injection.
    """
    query = """
        SELECT i.invoice_id, i.document_id, v.vendor_name, i.invoice_number,
               i.invoice_date, i.subtotal, i.tax_amount, i.total_amount,
               i.currency, i.status
        FROM Invoices i
        JOIN Vendors v ON v.vendor_id = i.vendor_id
        WHERE 1=1
    """
    params: list = []

    if vendor_name:
        query += " AND v.vendor_name LIKE ?"
        params.append(f"%{vendor_name}%")
    if date_from:
        query += " AND i.invoice_date >= ?"
        params.append(date_from)
    if date_to:
        query += " AND i.invoice_date <= ?"
        params.append(date_to)
    if min_amount is not None:
        query += " AND i.total_amount >= ?"
        params.append(min_amount)
    if max_amount is not None:
        query += " AND i.total_amount <= ?"
        params.append(max_amount)
    if status:
        query += " AND i.status = ?"
        params.append(status)

    query += " ORDER BY i.invoice_date DESC"

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        columns = [col[0] for col in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

def get_analytics(top_n: int = 5) -> dict:
    """
    Computes four aggregate stats in a single connection:
    - total_invoices: count of all rows in Invoices
    - total_invoice_value: sum of total_amount across all invoices
    - top_vendors: top N vendors ranked by summed total_amount (not count)
    - invoices_by_status: count of invoices grouped by status

    Only SQL-eligible invoices (see maybe_save_to_sql's guard clause) are
    ever in this table, so these numbers reflect that structured slice,
    not every document ever uploaded.
    """
    with get_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*), SUM(total_amount) FROM Invoices")
        count_row = cursor.fetchone()
        total_invoices = count_row[0]
        total_value = float(count_row[1]) if count_row[1] is not None else 0.0

        cursor.execute(
            """
            SELECT TOP (?) v.vendor_name, SUM(i.total_amount) AS vendor_total
            FROM Invoices i
            JOIN Vendors v ON v.vendor_id = i.vendor_id
            GROUP BY v.vendor_name
            ORDER BY vendor_total DESC
            """,
            top_n,
        )
        top_vendors = [
            {"vendor_name": row.vendor_name, "total_value": float(row.vendor_total)}
            for row in cursor.fetchall()
        ]

        cursor.execute("SELECT status, COUNT(*) FROM Invoices GROUP BY status")
        by_status = {row[0]: row[1] for row in cursor.fetchall()}

    return {
        "total_invoices": total_invoices,
        "total_invoice_value": round(total_value, 2),
        "top_vendors": top_vendors,
        "invoices_by_status": by_status,
    }