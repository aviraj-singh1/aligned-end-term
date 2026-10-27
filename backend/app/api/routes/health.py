from fastapi import APIRouter
from backend.app.api.schemas import HealthResponse

router = APIRouter()

@router.get("/health", response_model=HealthResponse)
def health_check():
    return HealthResponse(status="ok", service="Aligned GST Reconciliation")
