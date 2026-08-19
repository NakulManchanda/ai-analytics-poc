from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ai-app"}
