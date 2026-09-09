from fastapi import APIRouter

from app.services.invoice_service import get_analytics

router = APIRouter(prefix="/invoices", tags=["analytics"])


@router.get("/analytics")
def get_analytics_route() -> dict:
    return get_analytics()