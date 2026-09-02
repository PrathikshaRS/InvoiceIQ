from fastapi import FastAPI

from app.core.config import settings

app = FastAPI(title=settings.app_name)


@app.get("/health")
def health_check() -> dict:
    """
    Basic liveness check. Returns 200 if the app is up and able to read its own config.
    Used by load balancers, uptime monitors, or just you confirming the server is alive.
    """
    return {
        "status": "ok",
        "app_name": settings.app_name,
        "environment": settings.environment,
    }