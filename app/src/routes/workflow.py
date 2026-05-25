from fastapi import APIRouter


router = APIRouter(prefix="/workflow", tags=["Workflow"], dependencies=[])


@router.get("/health")
async def health_check():
    return {"status": "ok"}


@router.post("/create-knowledge-base")
def knowledge_base():
    return {"message": "Knowledge base created successfully"}