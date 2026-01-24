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

from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)

# Rate limiting: minimum seconds between API calls
MIN_REQUEST_INTERVAL = 1.0

# Retry configuration
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds

# Quota cooldown: pause API calls after quota error (5 minutes)
QUOTA_COOLDOWN_SECONDS = 300
_quota_cooldown_until: float = 0  # Global cooldown timestamp

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
    """Parse images to extract stock tickers using Gemini."""

    def __init__(self, image_cache=None):
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set in environment variables")

        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.model_name = "gemini-2.5-flash-lite"  # Faster, lighter model
        self._last_request_time: float = 0
        self._rate_limit_lock = asyncio.Lock()
        self.image_cache = image_cache  # Optional ImageCache instance

    async def _rate_limit(self):
        """Enforce rate limiting between API calls (async-safe)."""
        async with self._rate_limit_lock:
            elapsed = time.time() - self._last_request_time
            if elapsed < MIN_REQUEST_INTERVAL:
                await asyncio.sleep(MIN_REQUEST_INTERVAL - elapsed)
            self._last_request_time = time.time()

    @staticmethod
    def compute_image_hash(image_data: bytes) -> str:
        """Compute a hash of the image for caching purposes."""
        return hashlib.sha256(image_data).hexdigest()[:32]

    async def extract_tickers(self, image_data: bytes) -> list[str]:
        """Extract stock tickers from an image.

        Args:
            image_data: Raw image bytes

        Returns:
            List of extracted ticker symbols

        Raises:
            QuotaExceededError: If API quota is exceeded after all retries
        """
        global _quota_cooldown_until

        # Check if we're in cooldown period after quota error
        if time.time() < _quota_cooldown_until:
            remaining = int(_quota_cooldown_until - time.time())
            logger.warning(f"Quota cooldown active, {remaining}s remaining")
            raise QuotaExceededError(
                f"Daily quota exceeded. Please wait {remaining // 60} minutes or try again later."
            )

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

        # Retry loop with exponential backoff
        last_exception = None
        for attempt in range(MAX_RETRIES):
            # Apply rate limiting
            await self._rate_limit()

            try:
                image_part = types.Part.from_bytes(
                    data=image_data, mime_type="image/jpeg"
                )

                response = await self.client.aio.models.generate_content(
                    model=self.model_name,
                    contents=[prompt, image_part],
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=256,  # Reduced - we only need ticker list
                    ),
                )

                tickers = self._parse_response(response.text)

                # Cache successful result
                if self.image_cache:
                    self.image_cache.set(image_hash, tickers)

                return tickers

            except Exception as e:
                error_str = str(e).lower()
                logger.error(f"Gemini API error (attempt {attempt + 1}): {e}")

                # Check for various quota/rate limit indicators
                is_quota_error = any(x in error_str for x in [
                    "429", "quota", "rate limit", "resource exhausted",
                    "too many requests", "limit exceeded"
                ])

                if is_quota_error:
                    last_exception = e
                    # Check if it's a daily quota (not just rate limit)
                    is_daily_quota = "daily" in error_str or "per day" in error_str

                    if is_daily_quota or attempt >= MAX_RETRIES - 1:
                        # Set cooldown to prevent hammering the API
                        _quota_cooldown_until = time.time() + QUOTA_COOLDOWN_SECONDS
                        logger.warning(
                            f"Quota exceeded, cooldown set for {QUOTA_COOLDOWN_SECONDS}s"
                        )
                        raise QuotaExceededError(
                            "API quota exceeded. Please wait a few minutes or try again later."
                        )

                    # Retry with backoff for rate limits
                    backoff = INITIAL_BACKOFF * (2 ** attempt)
                    logger.warning(
                        f"Rate limited, retrying in {backoff}s (attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    await asyncio.sleep(backoff)
                    continue

                # Non-quota error - don't retry
                logger.error(f"Error extracting tickers from image: {e}")
                return []

        # Should not reach here, but just in case
        if last_exception:
            _quota_cooldown_until = time.time() + QUOTA_COOLDOWN_SECONDS
            raise QuotaExceededError("API quota exceeded. Please try again later.")
        return []

    def _parse_response(self, response_text: str) -> list[str]:
        """Parse the Gemini response to extract tickers."""
        try:
            # Try to find JSON in markdown code blocks
            json_match = re.search(
                r"```(?:json)?\s*(\{.*\})\s*```", response_text, re.DOTALL
            )
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try to find raw JSON
                json_match = re.search(r'(\{.*"tickers".*\})', response_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(1)
                else:
                    json_str = response_text.strip()

            data = json.loads(json_str)
            tickers = data.get("tickers", [])

            # Validate and clean tickers
            valid_tickers = []
            for ticker in tickers:
                cleaned = self._clean_ticker(ticker)
                if cleaned and self._is_valid_ticker(cleaned):
                    valid_tickers.append(cleaned)

            logger.info(f"Extracted {len(valid_tickers)} tickers from image")
            return valid_tickers

        except json.JSONDecodeError:
            logger.warning("Could not parse JSON from Gemini response")
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

    def _is_valid_ticker(self, ticker: str) -> bool:
        """Check if a string looks like a valid stock ticker."""
        return is_valid_ticker(ticker)

    def _extract_tickers_regex(self, text: str) -> list[str]:
        """Fallback method to extract tickers using regex."""
        potential = re.findall(r"\b([A-Z]{1,5})\b", text)

        valid_tickers = []
        for ticker in potential:
            cleaned = self._clean_ticker(ticker)
            if cleaned and self._is_valid_ticker(cleaned):
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
