from fastapi import FastAPI

from app.core.config import settings
from app.routers import invoices, analytics

app = FastAPI(title=settings.app_name)
app.include_router(analytics.router)
app.include_router(invoices.router)

@app.get("/health")
def health_check() -> dict:
    
    return {
        "status": "okiee",
        "app_name": settings.app_name,
        "environment": settings.environment,
    }