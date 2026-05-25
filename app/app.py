import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.src.services.mongodb import close_mongo_connection, connect_to_mongo
from app.src.routes.workflow import router as workflow_router
from app.src.routes.email_outreach import router as outreach_router
from app.src.routes.tracking import router as tracking_router
from app.src.routes.calendar import router as calendar_router
from app.src.services.reply_scheduler import reply_check_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_to_mongo()
    _scheduler = asyncio.create_task(reply_check_loop())
    yield
    _scheduler.cancel()
    try:
        await _scheduler
    except asyncio.CancelledError:
        pass
    await close_mongo_connection()


app = FastAPI(
    title="Automation API",
    description="Multi-client email outreach and workflow automation",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(workflow_router)
app.include_router(outreach_router)
app.include_router(tracking_router)
app.include_router(calendar_router)
