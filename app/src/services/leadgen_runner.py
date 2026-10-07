"""Background runner for lead-generation runs.

A run executes the selected Apify actor(s) (Google Maps and/or Leads Finder) in
the background, normalizes each result, and stores the leads in MongoDB. Mirrors
the structure of ``instagram_campaign_runner.py`` — in-memory ``dict[str, Task]``
plus an ``asyncio.Event`` for cooperative stop, blocking Apify calls dispatched
through ``asyncio.to_thread``, and MongoDB (``lead_runs`` / ``leads``)
as the single source of truth for progress. Each run row carries the
``campaign_id`` foreign key; each lead row carries the ``run_id`` foreign key.

Soft-error per actor: one actor failing is recorded on the run and the other
actor still runs and stores its leads (graceful-degradation rule).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.src.models.leadgen_models import RunRequest
from app.src.services.mongodb import get_db
from social_trend_analyzer.config import DEFAULT_CONFIG
from leadgen.actors import NORMALIZERS, RUNNERS

logger = logging.getLogger(__name__)

_runs: dict[str, asyncio.Task] = {}
_stop_events: dict[str, asyncio.Event] = {}


def launch_run(run_id: str, req: RunRequest) -> None:
    stop_event = asyncio.Event()
    _stop_events[run_id] = stop_event
    task = asyncio.create_task(_run_leadgen(run_id, req, stop_event))
    _runs[run_id] = task
    task.add_done_callback(
        lambda _: (_runs.pop(run_id, None), _stop_events.pop(run_id, None))
    )


def stop_run(run_id: str) -> bool:
    """Signal a running lead-gen run to stop. False if already finished/unknown."""
    stop_event = _stop_events.get(run_id)
    task = _runs.get(run_id)
    if not stop_event or not task:
        return False
    stop_event.set()
    task.cancel()
    return True


def _actor_filters(req: RunRequest, actor: str) -> dict:
    """The (possibly edited) filters for one actor as a plain Apify-input dict."""
    model = getattr(req, actor, None)
    return model.model_dump() if model is not None else {}


async def _run_leadgen(run_id: str, req: RunRequest, stop_event: asyncio.Event) -> None:
    db = get_db()
    config = dict(DEFAULT_CONFIG)

    async def _set(**fields) -> None:
        await db.lead_runs.update_one({"run_id": run_id}, {"$set": fields})

    async def _finish(status: str, **extra) -> None:
        await _set(status=status, completed_at=datetime.now(timezone.utc).isoformat(), **extra)

    logger.info("Leadgen %s: START — actors=%s", run_id, req.actors)

    try:
        for actor in req.actors:
            if stop_event.is_set():
                logger.info("Leadgen %s: stop requested — halting before %s", run_id, actor)
                await _finish("stopped", stage=actor)
                return

            await _set(stage=actor, **{f"progress.{actor}.status": "running"})
            filters = _actor_filters(req, actor)
            runner = RUNNERS[actor]
            normalize = NORMALIZERS[actor]
            try:
                raw_items = await asyncio.to_thread(runner, filters, config)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.exception("Leadgen %s: actor %s failed", run_id, actor)
                await _set(**{f"progress.{actor}.status": "failed", f"progress.{actor}.error": str(exc)})
                await db.lead_runs.update_one(
                    {"run_id": run_id},
                    {"$push": {"errors": {"actor": actor, "error_message": str(exc)}}},
                )
                continue

            now = datetime.now(timezone.utc).isoformat()
            docs = [
                {
                    "run_id": run_id,                 # foreign key → the lead_runs doc
                    "campaign_id": req.campaign_id,   # denormalized for easy per-campaign queries
                    "source": actor,
                    "lead": normalize(raw),
                    "raw": raw,
                    "created_at": now,
                }
                for raw in raw_items
            ]
            if docs:
                await db.leads.insert_many(docs)
            await _set(**{f"progress.{actor}.status": "completed", f"progress.{actor}.count": len(docs)})
            await db.lead_runs.update_one({"run_id": run_id}, {"$inc": {"total_leads": len(docs)}})
            logger.info("Leadgen %s: actor %s stored %d leads", run_id, actor, len(docs))

            if not raw_items:
                await db.lead_runs.update_one(
                    {"run_id": run_id},
                    {"$push": {"errors": {
                        "actor": actor,
                        "error_message": "0 leads returned — check TREND_APIFY_TOKEN, "
                                         "actor availability, and that the filters match something",
                    }}},
                )
    except asyncio.CancelledError:
        logger.info("Leadgen %s: cancelled", run_id)
        await _finish("stopped")
        raise

    logger.info("Leadgen %s: DONE", run_id)
    await _finish("completed", stage="done")
