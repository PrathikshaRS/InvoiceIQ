# tests/test_smoke.py
#
# Not testing InvoiceIQ business logic - testing that our TEST INFRASTRUCTURE
# works: the TestClient can hit a route, and the clean_databases fixture can
# actually reach SQL Server + Mongo (since it's autouse, just running any
# test here forces it to execute).

from fastapi.testclient import TestClient


def test_health_check(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "okiee"


def test_databases_are_reachable_and_empty(client: TestClient) -> None:
    """
    If clean_databases failed to connect to SQL Server or Mongo, this test
    would never even reach its assertions - it'd error out during fixture
    setup instead. Reaching these asserts IS the real proof of connectivity.
    """
    response = client.get("/invoices/search")
    assert response.status_code == 200
    assert response.json() == {"count": 0, "results": []}

    response = client.get("/invoices/analytics")
    assert response.status_code == 200
    body = response.json()
    assert body["total_invoices"] == 0
    assert body["total_invoice_value"] == 0.0