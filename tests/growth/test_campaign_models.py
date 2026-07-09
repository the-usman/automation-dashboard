"""Validation tests for the growth Campaign API request models.

Covers the manual-competitor option on CampaignRequest: handle normalization,
deduplication, and the relaxed apify-mode requirement (description AND/OR
manual competitors).
"""
import pytest
from pydantic import ValidationError

from app.src.models.campaign_models import CampaignRequest


def _req(**overrides) -> CampaignRequest:
    base = {"sessionid": "s3ss10n", "product_description": "AI fitness app"}
    base.update(overrides)
    return CampaignRequest(**base)


def test_manual_competitors_are_normalized_and_deduped():
    req = _req(competitors=["@NikeTraining", "  niketraining ", "GymShark", "", "@"])
    assert req.competitors == ["niketraining", "gymshark"]


def test_apify_mode_valid_with_competitors_and_no_description():
    req = _req(product_description="", competitors=["@gymshark"])
    assert req.candidate_source == "apify"
    assert req.competitors == ["gymshark"]


def test_apify_mode_requires_description_or_competitors():
    with pytest.raises(ValidationError, match="competitors"):
        _req(product_description="", competitors=[])


def test_apify_mode_description_only_still_valid():
    req = _req()
    assert req.competitors == []


def test_suggested_mode_needs_neither_description_nor_competitors():
    req = _req(product_description="", candidate_source="instagram_suggested")
    assert req.competitors == []
