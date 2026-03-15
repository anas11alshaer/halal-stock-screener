"""Gemini-based image parser for extracting stock tickers."""

import asyncio
import hashlib
import json
import logging
import re
import time
from typing import Optional

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODELS

logger = logging.getLogger(__name__)

# Retry configuration
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds

# Common false positives that look like tickers but aren't
FALSE_POSITIVE_TICKERS = {
    "CEO",
    "CFO",
    "CTO",
    "COO",
    "IPO",
    "ETF",
    "USD",
    "EUR",
    "GBP",
    "NYSE",
    "NASDAQ",
    "OTC",
    "SEC",
    "FDA",
    "USA",
    "API",
    "PDF",
    "THE",
    "AND",
    "FOR",
    "ARE",
    "NOT",
    "YOU",
    "ALL",
    "CAN",
    "HAD",
    "HER",
    "WAS",
    "ONE",
    "OUR",
    "OUT",
    "HAS",
    "HIS",
    "HOW",
    "MAN",
    "NEW",
    "NOW",
    "OLD",
    "SEE",
    "WAY",
    "WHO",
    "BOY",
    "DID",
    "GET",
    "LET",
    "PUT",
    "SAY",
    "SHE",
    "TOO",
    "USE",
    "INC",
    "LLC",
    "LTD",
    "PLC",
    "EST",
    "YTD",
    "QTR",
    "AVG",
    "MAX",
    "MIN",
    "TOP",
    "BUY",
    "SELL",
    "HOLD",
    "CALL",
    "LONG",
    "SHORT",
    "CASH",
    "DEBT",
}


def is_valid_ticker(ticker: str) -> bool:
    """Check if a string looks like a valid stock ticker."""
    if not ticker:
        return False

    if not re.match(r"^[A-Z]{1,5}(\.[A-Z])?$", ticker):
        return False

    return ticker not in FALSE_POSITIVE_TICKERS


class QuotaExceededError(Exception):
    """Raised when Gemini API quota is exceeded."""

    pass


class ImageParser:
    """Parse images to extract stock tickers using Gemini with daily model rotation."""

    def __init__(self, image_cache=None):
        if not GEMINI_API_KEY:
            raise ValueError("No Gemini API key configured. Set GEMINI_API_KEY.")

        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.models = list(GEMINI_MODELS)
        self.image_cache = image_cache

        # Daily rotation state
        self._request_counter = 0
        self._counter_date = ""
        self._exhausted_models: set[str] = set()

        logger.info(
            f"ImageParser initialized with {len(self.models)} models (daily rotation)"
        )

    def _reset_if_new_day(self):
        """Reset counter and exhausted models at the start of each new day."""
        today = time.strftime("%Y-%m-%d")
        if self._counter_date != today:
            self._counter_date = today
            self._request_counter = 0
            self._exhausted_models.clear()
            logger.info(f"Daily model rotation reset for {today}")

    def _get_next_model(self) -> str | None:
        """Get the next model in the rotation, skipping exhausted ones."""
        self._reset_if_new_day()

        if len(self._exhausted_models) >= len(self.models):
            return None

        base_index = self._request_counter % len(self.models)
        self._request_counter += 1

        for i in range(len(self.models)):
            index = (base_index + i) % len(self.models)
            if self.models[index] not in self._exhausted_models:
                return self.models[index]

        return None

    def _find_available_model(self) -> str | None:
        """Find any non-exhausted model as a fallback."""
        for model in self.models:
            if model not in self._exhausted_models:
                return model
        return None

    @staticmethod
    def compute_image_hash(image_data: bytes) -> str:
        """Compute a hash of the image for caching purposes."""
        return hashlib.sha256(image_data).hexdigest()[:32]

    async def extract_tickers(self, image_data: bytes) -> list[str]:
        """Extract stock tickers from an image using daily model rotation.

        Cycles through models round-robin per request (strongest first).
        Counter resets each new day. If a model hits quota, it's marked
        exhausted and the next available model is used.

        Raises:
            QuotaExceededError: If all models are exhausted for the day
        """
        # Check cache first
        image_hash = self.compute_image_hash(image_data)
        if self.image_cache:
            cached = self.image_cache.get(image_hash)
            if cached is not None:
                logger.info(f"Image cache hit for hash {image_hash[:8]}...")
                return cached

        prompt = """Analyze this image and extract any stock ticker symbols you can find.

Stock tickers are typically:
- 1-5 uppercase letters (e.g., AAPL, MSFT, GOOGL, META, TSLA)
- Sometimes followed by exchange suffixes (e.g., AAPL.US, MSFT.NASDAQ)

Return your response as a JSON object with this exact format:
{
    "tickers": ["TICKER1", "TICKER2"],
    "confidence": "high" | "medium" | "low",
    "notes": "optional notes about what you found"
}

If no tickers are found, return:
{
    "tickers": [],
    "confidence": "high",
    "notes": "No stock tickers found in the image"
}

Only include actual stock ticker symbols, not random text or abbreviations.
Remove any exchange suffixes - just return the base ticker (e.g., "AAPL" not "AAPL.US")."""

        model = self._get_next_model()
        if model is None:
            raise QuotaExceededError("All models exhausted for today. Try again tomorrow.")

        while model is not None:
            logger.info(f"Using model: {model}")

            for attempt in range(MAX_RETRIES):
                try:
                    image_part = types.Part.from_bytes(
                        data=image_data, mime_type="image/jpeg"
                    )

                    response = await self.client.aio.models.generate_content(
                        model=model,
                        contents=[prompt, image_part],
                        config=types.GenerateContentConfig(
                            temperature=0.1,
                            max_output_tokens=256,
                        ),
                    )

                    response_text = response.text if response.text else ""
                    logger.info(
                        f"Gemini response from {model} "
                        f"({len(response_text)} chars): {response_text[:200]}..."
                    )

                    if not response_text:
                        logger.warning("Gemini returned empty response")
                        return []

                    tickers = self._parse_response(response_text)

                    if self.image_cache:
                        self.image_cache.set(image_hash, tickers)

                    return tickers

                except Exception as e:
                    error_str = str(e).lower()

                    is_quota_error = any(x in error_str for x in [
                        "429", "quota", "rate limit", "resource exhausted",
                        "too many requests", "limit exceeded"
                    ])

                    if is_quota_error:
                        logger.warning(f"Model {model} quota hit, marking exhausted")
                        self._exhausted_models.add(model)
                        break  # Break retry loop, try next model

                    # Non-quota error — retry with backoff
                    logger.error(
                        f"Gemini error ({model}, attempt {attempt + 1}/{MAX_RETRIES}): {e}"
                    )
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(INITIAL_BACKOFF * (2 ** attempt))
                        continue
                    logger.error(f"Error extracting tickers: {e}")
                    return []

            # Current model exhausted, find next available
            model = self._find_available_model()

        raise QuotaExceededError("All models exhausted for today. Try again tomorrow.")

    def _parse_response(self, response_text: str) -> list[str]:
        """Parse the Gemini response to extract tickers."""
        # Log raw response for debugging
        logger.debug(f"Raw Gemini response: {response_text[:500]}")

        try:
            # Try to find JSON in markdown code blocks
            json_match = re.search(
                r"```(?:json)?\s*(\{.*\})\s*```", response_text, re.DOTALL
            )
            if json_match:
                json_str = json_match.group(1)
                logger.debug("Found JSON in code block")
            else:
                # Try to find raw JSON
                json_match = re.search(r'(\{.*"tickers".*\})', response_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(1)
                    logger.debug("Found raw JSON")
                else:
                    json_str = response_text.strip()
                    logger.debug("Using full response as JSON")

            data = json.loads(json_str)
            tickers = data.get("tickers", [])
            logger.info(f"Parsed tickers from JSON: {tickers}")

            # Validate and clean tickers
            valid_tickers = []
            for ticker in tickers:
                cleaned = self._clean_ticker(ticker)
                if cleaned and is_valid_ticker(cleaned):
                    valid_tickers.append(cleaned)
                elif cleaned:
                    logger.debug(f"Ticker '{ticker}' -> '{cleaned}' filtered out by validation")

            logger.info(f"Extracted {len(valid_tickers)} valid tickers: {valid_tickers}")
            return valid_tickers

        except json.JSONDecodeError as e:
            logger.warning(f"Could not parse JSON: {e}")
            logger.warning(f"Response was: {response_text[:200]}")
            return self._extract_tickers_regex(response_text)

    def _clean_ticker(self, ticker: str) -> Optional[str]:
        """Clean and normalize a ticker symbol."""
        if not ticker:
            return None

        ticker = ticker.upper().strip()
        ticker = re.sub(
            r"\.(US|NASDAQ|NYSE|AMEX|OTC|TSX|LSE)$", "", ticker, flags=re.IGNORECASE
        )
        ticker = re.sub(r"[^A-Z0-9.]", "", ticker)

        return ticker if ticker else None

    def _extract_tickers_regex(self, text: str) -> list[str]:
        """Fallback method to extract tickers using regex."""
        potential = re.findall(r"\b([A-Z]{1,5})\b", text)

        valid_tickers = []
        for ticker in potential:
            cleaned = self._clean_ticker(ticker)
            if cleaned and is_valid_ticker(cleaned):
                valid_tickers.append(cleaned)

        # Remove duplicates while preserving order
        seen = set()
        unique_tickers = []
        for t in valid_tickers:
            if t not in seen:
                seen.add(t)
                unique_tickers.append(t)

        return unique_tickers


def parse_text_for_tickers(text: str) -> list[str]:
    """Parse plain text for stock tickers.

    This doesn't require Gemini - it's a simple regex-based extraction
    for when users send text messages with tickers.
    """
    tickers = []

    # First, look for cashtags (most reliable)
    cashtags = re.findall(r"\$([A-Za-z]{1,5})\b", text)
    tickers.extend([t.upper() for t in cashtags])

    # Then look for standalone uppercase sequences that look like tickers
    words = text.split()
    for word in words:
        cleaned = re.sub(r"[^\w]", "", word)
        if cleaned.isupper() and 1 <= len(cleaned) <= 5 and cleaned.isalpha():
            tickers.append(cleaned)

    # Validate and deduplicate
    valid_tickers = []
    seen = set()
    for ticker in tickers:
        if ticker not in seen and is_valid_ticker(ticker):
            seen.add(ticker)
            valid_tickers.append(ticker)

    return valid_tickers
