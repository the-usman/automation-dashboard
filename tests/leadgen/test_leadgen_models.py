"""RunRequest validation: each selected actor needs filters or a draft_id."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.src.models.leadgen_models import (
    GoogleMapsFiltersModel,
    RunRequest,
)


def test_run_request_rejects_selected_actor_without_filters_or_draft():
    with pytest.raises(ValidationError):
        RunRequest(campaign_id="c1", actors=["google_maps"])  # no inline filters, no draft_id


def test_run_request_requires_campaign_id():
    with pytest.raises(ValidationError):
        RunRequest(actors=["google_maps"], draft_id="d1")  # missing campaign_id


def test_run_request_ok_with_inline_filters():
    req = RunRequest(
        campaign_id="c1", actors=["google_maps"],
        google_maps=GoogleMapsFiltersModel(searchStringsArray=["coffee"]),
    )
    assert req.actors == ["google_maps"]
    assert req.campaign_id == "c1"


def test_run_request_ok_with_draft_id():
    req = RunRequest(campaign_id="c1", actors=["google_maps", "leads_finder"], draft_id="d1")
    assert req.draft_id == "d1"


def test_run_request_normalizes_and_dedupes_actors():
    req = RunRequest(campaign_id="c1", actors=["Google_Maps", "google_maps", "bogus"], draft_id="d1")
    assert req.actors == ["google_maps"]


def test_run_request_rejects_empty_actor_selection():
    with pytest.raises(ValidationError):
        RunRequest(campaign_id="c1", actors=["bogus"], draft_id="d1")
