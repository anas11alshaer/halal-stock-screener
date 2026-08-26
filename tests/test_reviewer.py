"""Evidence validation tests for the optional Gemini reviewer."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from reviewer import GeminiEvidenceReviewer
from scrapers import ComplianceStatus


def test_accepts_only_verbatim_matching_evidence():
    text = "AAPL Shariah Compliance changed layout HALAL"
    response = json.dumps(
        {
            "status": "HALAL",
            "evidence": "AAPL Shariah Compliance changed layout HALAL",
            "ticker_matches": True,
            "contradiction": False,
        }
    )
    validated = GeminiEvidenceReviewer._validate(response, text, "AAPL")
    assert validated == (ComplianceStatus.HALAL, text)


def test_rejects_invented_evidence():
    response = json.dumps(
        {
            "status": "HALAL",
            "evidence": "AAPL is definitely halal",
            "ticker_matches": True,
            "contradiction": False,
        }
    )
    assert GeminiEvidenceReviewer._validate(response, "AAPL profile", "AAPL") is None


def test_rejects_contradiction_or_identity_mismatch():
    for ticker_matches, contradiction in ((False, False), (True, True)):
        response = json.dumps(
            {
                "status": "HALAL",
                "evidence": "HALAL",
                "ticker_matches": ticker_matches,
                "contradiction": contradiction,
            }
        )
        assert GeminiEvidenceReviewer._validate(response, "HALAL", "AAPL") is None


def test_rejects_generic_verdict_not_bound_to_ticker():
    response = json.dumps(
        {
            "status": "HALAL",
            "evidence": "HALAL",
            "ticker_matches": True,
            "contradiction": False,
        }
    )
    assert (
        GeminiEvidenceReviewer._validate(response, "AAPL profile HALAL", "AAPL") is None
    )
