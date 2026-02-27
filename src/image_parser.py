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

from config import GEMINI_API_KEYS

logger = logging.getLogger(__name__)

# Rate limiting: minimum seconds between API calls
MIN_REQUEST_INTERVAL = 1.0

# Retry configuration
MAX_RETRIES = 3
INITIAL_BACKOFF = 1.0  # seconds

# Quota cooldown per key (5 minutes)
QUOTA_COOLDOWN_SECONDS = 300

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
    """Parse images to extract stock tickers using Gemini with multi-key support."""

    def __init__(self, image_cache=None):
        if not GEMINI_API_KEYS:
            raise ValueError("No Gemini API keys configured. Set GEMINI_API_KEYS or GEMINI_API_KEY.")

        # Create a client pool with one client per API key
        self.clients = [genai.Client(api_key=key) for key in GEMINI_API_KEYS]
        self.key_count = len(self.clients)
        self._current_key_index = 0
        self._key_cooldowns: dict[int, float] = {}  # key_index -> cooldown_until timestamp

        self.model_name = "gemini-2.5-flash-lite"  # Faster, lighter model
        self._last_request_time: float = 0
        self._rate_limit_lock = asyncio.Lock()
        self.image_cache = image_cache  # Optional ImageCache instance

        logger.info(f"ImageParser initialized with {self.key_count} API key(s)")

    def _get_available_client(self) -> tuple[int, genai.Client] | None:
        """Get the next available client that's not in cooldown.

        Returns:
            Tuple of (key_index, client) or None if all keys are in cooldown
        """
        now = time.time()
        checked = 0

        while checked < self.key_count:
            idx = self._current_key_index
            self._current_key_index = (self._current_key_index + 1) % self.key_count

            # Check if this key is in cooldown
            cooldown_until = self._key_cooldowns.get(idx, 0)
            if now >= cooldown_until:
                return (idx, self.clients[idx])

            checked += 1

        # All keys are in cooldown
        return None

    def _set_key_cooldown(self, key_index: int):
        """Set a cooldown for a specific key after quota error."""
        self._key_cooldowns[key_index] = time.time() + QUOTA_COOLDOWN_SECONDS
        logger.warning(f"API key {key_index + 1}/{self.key_count} in cooldown for {QUOTA_COOLDOWN_SECONDS}s")

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
        """Extract stock tickers from an image using rotating API keys.

        Args:
            image_data: Raw image bytes

        Returns:
            List of extracted ticker symbols

        Raises:
            QuotaExceededError: If all API keys are exhausted
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

        # Try each available key with retries
        keys_tried = 0
        last_exception = None

        while keys_tried < self.key_count:
            # Get next available client
            client_info = self._get_available_client()
            if client_info is None:
                # All keys in cooldown - find shortest wait time
                min_cooldown = min(self._key_cooldowns.values())
                remaining = int(min_cooldown - time.time())
                logger.warning(f"All {self.key_count} API keys in cooldown")
                raise QuotaExceededError(
                    f"All API keys exhausted. Please wait {max(1, remaining // 60)} minutes."
                )

            key_index, client = client_info
            logger.info(f"Using API key {key_index + 1}/{self.key_count}")

            # Retry loop for this key
            for attempt in range(MAX_RETRIES):
                await self._rate_limit()

                try:
                    image_part = types.Part.from_bytes(
                        data=image_data, mime_type="image/jpeg"
                    )

                    response = await client.aio.models.generate_content(
                        model=self.model_name,
                        contents=[prompt, image_part],
                        config=types.GenerateContentConfig(
                            temperature=0.1,
                            max_output_tokens=256,
                        ),
                    )

                    # Log response for debugging
                    response_text = response.text if response.text else ""
                    logger.info(f"Gemini response ({len(response_text)} chars): {response_text[:200]}...")

                    if not response_text:
                        logger.warning("Gemini returned empty response")
                        return []

                    tickers = self._parse_response(response_text)

                    # Cache successful result
                    if self.image_cache:
                        self.image_cache.set(image_hash, tickers)

                    return tickers

                except Exception as e:
                    error_str = str(e).lower()
                    logger.error(f"Gemini API error (key {key_index + 1}, attempt {attempt + 1}): {e}")

                    is_quota_error = any(x in error_str for x in [
                        "429", "quota", "rate limit", "resource exhausted",
                        "too many requests", "limit exceeded"
                    ])

                    if is_quota_error:
                        last_exception = e
                        is_daily_quota = "daily" in error_str or "per day" in error_str

                        if is_daily_quota or attempt >= MAX_RETRIES - 1:
                            # This key is exhausted, put it in cooldown and try next key
                            self._set_key_cooldown(key_index)
                            break  # Break inner retry loop, try next key

                        # Retry with backoff
                        backoff = INITIAL_BACKOFF * (2 ** attempt)
                        logger.warning(f"Rate limited, retrying in {backoff}s")
                        await asyncio.sleep(backoff)
                        continue

                    # Non-quota error - don't retry
                    logger.error(f"Error extracting tickers: {e}")
                    return []

            keys_tried += 1

        # All keys exhausted
        if last_exception:
            raise QuotaExceededError(
                f"All {self.key_count} API keys exhausted. Please try again later."
            )
        return []

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
