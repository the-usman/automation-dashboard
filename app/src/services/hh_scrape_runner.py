"""Background runner for the hh.kz vacancy scrape job.

Wraps ``jobs.scrape_hh.scrape_vacancies`` (Playwright, already async) plus the
optional Google Sheets upload (``gspread``, blocking -> ``asyncio.to_thread``).
MongoDB (``hh_scrape_jobs``) is the single source of truth for progress,
mirroring ``instagram_task_runner.py``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.src.services.mongodb import get_db
from jobs.scrape_hh import scrape_vacancies, upload_to_google_sheets

logger = logging.getLogger(__name__)

_jobs: dict[str, asyncio.Task] = {}


def launch_scrape_job(job_id: str, search_url: str, upload_to_sheets: bool) -> None:
    task = asyncio.create_task(_run_job(job_id, search_url, upload_to_sheets))
    _jobs[job_id] = task
    task.add_done_callback(lambda _: _jobs.pop(job_id, None))


async def _run_job(job_id: str, search_url: str, upload_to_sheets: bool) -> None:
    db = get_db()

    async def _set(**fields) -> None:
        await db.hh_scrape_jobs.update_one({"job_id": job_id}, {"$set": fields})

    async def _progress(info: dict) -> None:
        await _set(
            current_page=info["page"],
            total_pages=info["total_pages"] or 0,
            found_so_far=info["found_so_far"],
        )

    logger.info("HH scrape job %s: START", job_id)

    try:
        results = await scrape_vacancies(search_url, on_progress=_progress)
    except Exception as exc:  # noqa: BLE001
        logger.exception("HH scrape job %s: scrape failed", job_id)
        await _set(
            status="failed",
            error=f"scrape failed: {exc}",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return

    internships = sum(1 for v in results if v["type"] == "internship")
    juniors = sum(1 for v in results if v["type"] == "junior")
    fulltime = sum(1 for v in results if v["type"] == "full-time")

    sheet_error = None
    if upload_to_sheets:
        try:
            await asyncio.to_thread(upload_to_google_sheets, results)
        except Exception as exc:  # noqa: BLE001
            logger.warning("HH scrape job %s: Google Sheets upload failed: %s", job_id, exc)
            sheet_error = str(exc)

    logger.info("HH scrape job %s: DONE — %d vacancies", job_id, len(results))
    await _set(
        status="completed",
        completed_at=datetime.now(timezone.utc).isoformat(),
        total_found=len(results),
        internships=internships,
        juniors=juniors,
        fulltime=fulltime,
        results=results,
        sheet_error=sheet_error,
    )
