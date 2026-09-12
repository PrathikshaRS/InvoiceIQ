# tests/conftest.py
#
# Pytest auto-discovers this file and runs it before collecting any test
# module in this directory. The load_dotenv() call MUST stay at the very
# top, before any `app.*` import - Settings() is a singleton built once at
# import time (see app/core/config.py: `settings = Settings()`), so if a
# test file imported an app module before this ran, it would already be
# pointed at production databases.

import os
from dotenv import load_dotenv

load_dotenv(".env.test", override=True)

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.db.sqlserver import get_connection
from app.db.mongodb import get_invoices_collection


def _assert_using_test_databases() -> None:
    """
    Safety net: if .env.test failed to load for any reason (wrong working
    directory, typo, etc.), every other fixture in this file would silently
    operate on production data. Fail loudly instead.
    """
    assert settings.sqlserver_database == "invoiceiq_test", (
        f"Refusing to run tests against '{settings.sqlserver_database}' - "
        "expected 'invoiceiq_test'. Is .env.test present and loading?"
    )
    assert settings.mongodb_db_name == "invoiceiq_test", (
        f"Refusing to run tests against '{settings.mongodb_db_name}' - "
        "expected 'invoiceiq_test'. Is .env.test present and loading?"
    )


@pytest.fixture(scope="session", autouse=True)
def verify_test_databases() -> None:
    """Runs once per test session, before any test, as a guardrail."""
    _assert_using_test_databases()


@pytest.fixture(autouse=True)
def clean_databases():
    """
    Runs before EVERY test function. Wipes Invoices/Vendors (in FK-safe
    order - Invoices references Vendors) and the Mongo test collection, so
    tests never see leftover data from a previous test and don't depend on
    run order.

    DELETE, not TRUNCATE: TRUNCATE is blocked by SQL Server on a table
    that's the target of a foreign key, even with zero matching rows.
    """
    _assert_using_test_databases()

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM Payments")
        cursor.execute("DELETE FROM Invoices")
        cursor.execute("DELETE FROM Vendors")
        conn.commit()

    get_invoices_collection().delete_many({})

    yield  # test runs here

    # No teardown needed after - the next test's setup wipes again anyway.


@pytest.fixture()
def client() -> TestClient:
    """FastAPI TestClient - calls routes in-process, no uvicorn server needed."""
    return TestClient(app)