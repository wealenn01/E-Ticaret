"""Trendyol iade onay/red mantığı testleri."""
from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.marketplace_api import base as B
from app.marketplace_api.trendyol_api import TrendyolAPI


def _api() -> TrendyolAPI:
    return TrendyolAPI({
        "seller_id": "2738",
        "api_key": "key",
        "api_secret": "secret",
        "env": "stage",
    })


def test_headers_include_storefront():
    h = _api()._headers()
    assert h["storeFrontCode"] == "TR"
    assert h["User-Agent"] == "2738 - SelfIntegration"


def test_reject_requires_waiting_in_action():
    api = _api()
    with patch.object(api, "_claim_items_for", return_value=[("99", "Accepted")]):
        with pytest.raises(B.MarketplaceError, match="itiraz"):
            api.review_claim("c1", False, "test")


def test_reject_sends_multipart_with_storefront():
    api = _api()
    with patch.object(api, "_claim_items_for", return_value=[("42", "WaitingInAction")]):
        with patch.object(api, "_issue_reason_without_file", return_value=451):
            with patch.object(api, "_req") as mock_req:
                api.review_claim("c1", False, "Hasar yok")
                mock_req.assert_called_once()
                args, kwargs = mock_req.call_args
                assert args[0] == "POST"
                assert "/claims/c1/issue" in args[1]
                files = kwargs["files"]
                assert files["claimItemIdList"] == (None, "42")
                assert files["claimIssueReasonId"] == (None, "451")
                assert "data" not in kwargs or kwargs.get("data") is None


def test_approve_only_actionable_items():
    api = _api()
    with patch.object(
        api,
        "_claim_items_for",
        return_value=[("1", "WaitingInAction"), ("2", "Accepted")],
    ):
        with patch.object(api, "_req") as mock_req:
            api.review_claim("c1", True)
            payload = mock_req.call_args.kwargs["json"]
            assert payload["claimLineItemIdList"] == ["1"]
