"""Bounded Gemini fallback for unfamiliar provider evidence."""

import json
import logging
import re
from dataclasses import replace

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODELS
from scrapers import ComplianceStatus, ResultState, ScreeningResult, Security

logger = logging.getLogger(__name__)


class GeminiEvidenceReviewer:
    """Review parser failures without browsing or inventing a verdict."""

    def __init__(self):
        self.client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

    async def review(
        self, security: Security, result: ScreeningResult
    ) -> ScreeningResult:
        if (
            self.client is None
            or result.state != ResultState.PARSE_ERROR
            or not result.review_text
        ):
            return result

        snippet = result.review_text[:5000]
        prompt = f"""You are a strict evidence parser, not a religious authority.
Read only the supplied text for provider {result.source}.
Expected ticker: {security.symbol}
Expected name: {security.name or "unknown"}

Return JSON only:
{{"status":"HALAL|NOT_HALAL|DOUBTFUL|UNKNOWN","evidence":"verbatim quote",
"ticker_matches":true,"contradiction":false}}

Rules:
- Evidence must be copied verbatim from the supplied text.
- Use UNKNOWN if the verdict, identity, or visible text is uncertain.
- Mark contradiction true if visible verdicts conflict.

TEXT:
{snippet}
"""
        for model in GEMINI_MODELS:
            try:
                response = await self.client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        temperature=0,
                        max_output_tokens=256,
                        response_mime_type="application/json",
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        ),
                    ),
                )
                reviewed = self._validate(response.text or "", snippet, security.symbol)
                if reviewed is None:
                    return result
                status, evidence = reviewed
                return replace(
                    result,
                    status=status,
                    state=ResultState.SUCCESS,
                    evidence=evidence,
                    retrieval_method="gemini_reviewed",
                    error_message=None,
                )
            except Exception as exc:
                if any(
                    marker in str(exc).lower()
                    for marker in ("429", "quota", "rate limit", "resource exhausted")
                ):
                    continue
                logger.warning("Gemini evidence review failed: %s", exc)
                return result
        return result

    @staticmethod
    def _validate(
        response_text: str, supplied_text: str, expected_ticker: str
    ) -> tuple[ComplianceStatus, str] | None:
        try:
            cleaned = re.sub(r"^```(?:json)?|```$", "", response_text.strip()).strip()
            data = json.loads(cleaned)
            evidence = data["evidence"]
            if (
                not data.get("ticker_matches")
                or data.get("contradiction")
                or not isinstance(evidence, str)
                or evidence not in supplied_text
                or not re.search(
                    rf"\b{re.escape(expected_ticker)}\b", evidence, re.IGNORECASE
                )
                or not re.search(
                    r"\b(?:not\s+halal|non[_ -]?compliant|doubtful|questionable|halal|compliant)\b",
                    evidence,
                    re.IGNORECASE,
                )
            ):
                return None
            status = {
                "HALAL": ComplianceStatus.HALAL,
                "NOT_HALAL": ComplianceStatus.NOT_HALAL,
                "DOUBTFUL": ComplianceStatus.DOUBTFUL,
            }.get(str(data.get("status", "")).upper())
            return (status, evidence) if status else None
        except (json.JSONDecodeError, KeyError, TypeError):
            return None
