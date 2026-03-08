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

from config import GEMINI_API_KEYS, GEMINI_MODELS

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


class _ModelSlot:
    """Tracks rate limit usage for a single (API key, model) pair."""

    def __init__(self, client: genai.Client, key_index: int, model_name: str, rpm: int, rpd: int):
        self.client = client
        self.key_index = key_index
        self.model_name = model_name
        self.rpm = rpm
        self.rpd = rpd
        self._minute_timestamps: list[float] = []
        self._day_count = 0
        self._day_date: str = ""  # YYYY-MM-DD string for current day
        self._cooldown_until: float = 0  # timestamp when cooldown ends

    def _current_day(self) -> str:
        return time.strftime("%Y-%m-%d")

    def is_available(self) -> bool:
        now = time.time()
        if now < self._cooldown_until:
            return False
        # Reset daily counter if new day
        today = self._current_day()
        if self._day_date != today:
            self._day_date = today
            self._day_count = 0
        if self._day_count >= self.rpd:
            return False
        # Prune old timestamps and check RPM
        cutoff = now - 60
        self._minute_timestamps = [t for t in self._minute_timestamps if t > cutoff]
        if len(self._minute_timestamps) >= self.rpm:
            return False
        return True

    def record_usage(self):
        now = time.time()
        self._minute_timestamps.append(now)
        today = self._current_day()
        if self._day_date != today:
            self._day_date = today
            self._day_count = 0
        self._day_count += 1

    def set_cooldown(self, seconds: float):
        self._cooldown_until = time.time() + seconds
        logger.warning(
            f"Slot key={self.key_index + 1} model={self.model_name} "
            f"in cooldown for {seconds}s"
        )

    def seconds_until_available(self) -> float:
        now = time.time()
        waits = []
        # Cooldown wait
        if now < self._cooldown_until:
            waits.append(self._cooldown_until - now)
        # RPM wait — time until oldest request in the window expires
        cutoff = now - 60
        active = [t for t in self._minute_timestamps if t > cutoff]
        if len(active) >= self.rpm and active:
            waits.append(active[0] + 60 - now)
        # RPD exhausted — wait until midnight (long wait)
        today = self._current_day()
        if self._day_date == today and self._day_count >= self.rpd:
            waits.append(3600)  # placeholder, effectively unavailable
        return max(waits) if waits else 0

    def __repr__(self):
        return f"Slot(key={self.key_index + 1}, model={self.model_name}, rpm={self.rpm}, rpd={self.rpd})"


class ImageParser:
    """Parse images to extract stock tickers using Gemini with multi-model, multi-key rotation."""

    def __init__(self, image_cache=None):
        if not GEMINI_API_KEYS:
            raise ValueError("No Gemini API keys configured. Set GEMINI_API_KEYS or GEMINI_API_KEY.")

        # Build slots: one per (key, model) pair, ordered by model preference
        self.slots: list[_ModelSlot] = []
        clients = [genai.Client(api_key=key) for key in GEMINI_API_KEYS]
        for model_cfg in GEMINI_MODELS:
            for key_idx, client in enumerate(clients):
                self.slots.append(_ModelSlot(
                    client=client,
                    key_index=key_idx,
                    model_name=model_cfg["name"],
                    rpm=model_cfg["rpm"],
                    rpd=model_cfg["rpd"],
                ))

        self.image_cache = image_cache
        logger.info(
            f"ImageParser initialized with {len(clients)} API key(s), "
            f"{len(GEMINI_MODELS)} models, {len(self.slots)} total slots"
        )

    def _get_available_slot(self) -> _ModelSlot | None:
        """Get the best available slot (first available in preference order)."""
        for slot in self.slots:
            if slot.is_available():
                return slot
        return None

    @staticmethod
    def compute_image_hash(image_data: bytes) -> str:
        """Compute a hash of the image for caching purposes."""
        return hashlib.sha256(image_data).hexdigest()[:32]

    async def extract_tickers(self, image_data: bytes) -> list[str]:
        """Extract stock tickers from an image, rotating across models and keys.

        Tries slots in preference order (highest RPM models first). On rate
        limit errors, marks the slot and moves to the next. If all slots are
        busy but some will free up soon, waits for the shortest cooldown.

        Raises:
            QuotaExceededError: If all slots are exhausted
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

        slots_tried = set()
        last_exception = None

        while len(slots_tried) < len(self.slots):
            slot = self._get_available_slot()

            if slot is None:
                # All slots busy — find the shortest wait
                min_wait = min(s.seconds_until_available() for s in self.slots)
                if min_wait > 300:
                    raise QuotaExceededError(
                        "All model slots exhausted. Please try again later."
                    )
                logger.info(f"All slots busy, waiting {min_wait:.1f}s for next available")
                await asyncio.sleep(min_wait + 0.1)
                continue

            slot_id = id(slot)
            if slot_id in slots_tried:
                # Already failed on this slot, skip
                continue

            logger.info(f"Using {slot}")

            for attempt in range(MAX_RETRIES):
                try:
                    slot.record_usage()

                    image_part = types.Part.from_bytes(
                        data=image_data, mime_type="image/jpeg"
                    )

                    response = await slot.client.aio.models.generate_content(
                        model=slot.model_name,
                        contents=[prompt, image_part],
                        config=types.GenerateContentConfig(
                            temperature=0.1,
                            max_output_tokens=256,
                        ),
                    )

                    response_text = response.text if response.text else ""
                    logger.info(
                        f"Gemini response from {slot.model_name} "
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
                    logger.error(
                        f"Gemini error ({slot}, attempt {attempt + 1}): {e}"
                    )

                    is_quota_error = any(x in error_str for x in [
                        "429", "quota", "rate limit", "resource exhausted",
                        "too many requests", "limit exceeded"
                    ])

                    if is_quota_error:
                        last_exception = e
                        is_daily = "daily" in error_str or "per day" in error_str
                        slot.set_cooldown(3600 if is_daily else 60)
                        slots_tried.add(slot_id)
                        break  # Try next slot

                    # Non-quota error — don't retry
                    logger.error(f"Error extracting tickers: {e}")
                    return []

            slots_tried.add(slot_id)

        if last_exception:
            raise QuotaExceededError(
                f"All {len(self.slots)} model slots exhausted. Please try again later."
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
