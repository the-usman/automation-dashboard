import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Send application logs (growth follow/unfollow progress, errors, etc.) to the
# console. Uvicorn only configures its own loggers, so without this our
# module-level `logging.getLogger(__name__)` INFO logs would be swallowed.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

# Silence chatty third-party DEBUG logs (pymongo heartbeats, driver internals,
# HTTP client wire logs) that otherwise drown out our growth progress logs.
for _noisy in ("pymongo", "motor", "urllib3", "httpcore", "httpx", "asyncio"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

from app.src.services.mongodb import close_mongo_connection, connect_to_mongo
from app.src.routes.workflow import router as workflow_router
from app.src.routes.email_outreach import router as outreach_router
from app.src.routes.tracking import router as tracking_router
from app.src.routes.calendar import router as calendar_router
from app.src.routes.growth import router as growth_router
from app.src.routes.reputation import router as reputation_router
from app.src.routes.jobs import router as jobs_router
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

# CORS so a browser frontend can call the API. Set CORS_ALLOW_ORIGINS to a
# comma-separated list of origins in production (e.g. "https://app.example.com").
_origins = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflow_router)
app.include_router(outreach_router)
app.include_router(tracking_router)
app.include_router(calendar_router)
app.include_router(growth_router)
app.include_router(reputation_router)
app.include_router(jobs_router)
