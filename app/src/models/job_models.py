"""Request/response models for the hh.kz Vacancy Scrape Job API.

Mirrors the style of `app/src/models/campaign_models.py`: a create response with
just an id, a status response for polling, a results response for the payload.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ScrapeHHRequest(BaseModel):
    """Start a scrape of hh.kz internship/junior/entry-level vacancies posted in
    the last 24 hours. Runs in the background — multiple paginated page loads."""

    upload_to_sheets: bool = Field(
        True, description="upload results to the configured Google Sheet on completion"
    )
    search_url: str = Field(
        "", description="override the default hh.ru search URL; leave blank to use the built-in filters"
    )


class ScrapeHHCreatedResponse(BaseModel):
    job_id: str
    status: str


class VacancyModel(BaseModel):
    name: str
    type: str  # "internship" | "junior" | "full-time"
    salary: str = ""
    employer: str = ""
    address: str = ""
    url: str = ""


class ScrapeHHStatusResponse(BaseModel):
    """Progress view — how far the scrape has got."""

    job_id: str
    status: str  # "running" | "completed" | "failed"
    current_page: int = 0
    total_pages: int = 0
    found_so_far: int = 0
    total_found: int = 0
    internships: int = 0
    juniors: int = 0
    fulltime: int = 0
    started_at: str
    completed_at: str | None = None
    error: str | None = None
    sheet_error: str | None = None


class ScrapeHHResultsResponse(BaseModel):
    """Full results — every vacancy found."""

    job_id: str
    status: str
    total_found: int
    results: list[VacancyModel]
