"""Gemini-based image parser for extracting stock tickers."""

import json
import logging
import re
from typing import Optional

import google.generativeai as genai

from config import GEMINI_API_KEY

logger = logging.getLogger(__name__)


class ImageParser:
    """Parse images to extract stock tickers using Gemini."""

    def __init__(self):
        if not GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not set in environment variables")

        genai.configure(api_key=GEMINI_API_KEY)
        self.model = genai.GenerativeModel("gemini-1.5-flash")

    async def extract_tickers(self, image_data: bytes) -> list[str]:
        """Extract stock tickers from an image.

        Args:
            image_data: Raw image bytes

        Returns:
            List of extracted ticker symbols
        """
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

        try:
            # Create the image part for Gemini
            image_part = {
                "mime_type": "image/jpeg",  # Gemini handles most formats
                "data": image_data
            }

            # Generate response
            response = await self.model.generate_content_async(
                [prompt, image_part],
                generation_config=genai.GenerationConfig(
                    temperature=0.1,  # Low temperature for more deterministic output
                    max_output_tokens=1024,
                )
            )

            # Parse the response
            return self._parse_response(response.text)

        except Exception as e:
            logger.error(f"Error extracting tickers from image: {e}")
            return []

    def _parse_response(self, response_text: str) -> list[str]:
        """Parse the Gemini response to extract tickers."""
        try:
            # Try to find JSON in the response
            # Sometimes Gemini wraps it in markdown code blocks
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try to find raw JSON
                json_match = re.search(r'\{[^{}]*"tickers"[^{}]*\}', response_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                else:
                    # Fall back to treating the whole response as JSON
                    json_str = response_text.strip()

            data = json.loads(json_str)
            tickers = data.get("tickers", [])

            # Validate and clean tickers
            valid_tickers = []
            for ticker in tickers:
                cleaned = self._clean_ticker(ticker)
                if cleaned and self._is_valid_ticker(cleaned):
                    valid_tickers.append(cleaned)

            logger.info(f"Extracted tickers: {valid_tickers}")
            return valid_tickers

        except json.JSONDecodeError:
            logger.warning(f"Could not parse JSON from response: {response_text[:200]}")
            # Fall back to regex extraction
            return self._extract_tickers_regex(response_text)

    def _clean_ticker(self, ticker: str) -> Optional[str]:
        """Clean and normalize a ticker symbol."""
        if not ticker:
            return None

        # Remove exchange suffixes
        ticker = ticker.upper().strip()
        ticker = re.sub(r'\.(US|NASDAQ|NYSE|AMEX|OTC|TSX|LSE)$', '', ticker, flags=re.IGNORECASE)

        # Remove any non-alphanumeric characters except dots (for some tickers like BRK.A)
        ticker = re.sub(r'[^A-Z0-9.]', '', ticker)

        return ticker if ticker else None

    def _is_valid_ticker(self, ticker: str) -> bool:
        """Check if a string looks like a valid stock ticker."""
        if not ticker:
            return False

        # Most tickers are 1-5 letters, some have dots (BRK.A, BRK.B)
        if not re.match(r'^[A-Z]{1,5}(\.[A-Z])?$', ticker):
            return False

        # Filter out common false positives
        false_positives = {
            'CEO', 'CFO', 'CTO', 'COO', 'IPO', 'ETF', 'USD', 'EUR', 'GBP',
            'NYSE', 'NASDAQ', 'OTC', 'SEC', 'FDA', 'USA', 'API', 'PDF',
            'THE', 'AND', 'FOR', 'ARE', 'NOT', 'YOU', 'ALL', 'CAN', 'HAD',
            'HER', 'WAS', 'ONE', 'OUR', 'OUT', 'HAS', 'HIS', 'HOW', 'MAN',
            'NEW', 'NOW', 'OLD', 'SEE', 'WAY', 'WHO', 'BOY', 'DID', 'GET',
            'LET', 'PUT', 'SAY', 'SHE', 'TOO', 'USE', 'INC', 'LLC', 'LTD',
            'PLC', 'EST', 'YTD', 'QTR', 'AVG', 'MAX', 'MIN', 'TOP', 'BUY',
            'SELL', 'HOLD', 'CALL', 'PUT', 'LONG', 'SHORT', 'CASH', 'DEBT'
        }

        return ticker not in false_positives

    def _extract_tickers_regex(self, text: str) -> list[str]:
        """Fallback method to extract tickers using regex."""
        # Find potential tickers (1-5 uppercase letters)
        potential = re.findall(r'\b([A-Z]{1,5})\b', text)

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
    # Common patterns for tickers in text:
    # - $AAPL (cashtag)
    # - AAPL
    # - "check AAPL"

    tickers = []

    # First, look for cashtags (most reliable)
    cashtags = re.findall(r'\$([A-Za-z]{1,5})\b', text)
    tickers.extend([t.upper() for t in cashtags])

    # Then look for standalone uppercase sequences that look like tickers
    # But only if they're not part of a larger word
    words = text.split()
    for word in words:
        # Remove punctuation
        cleaned = re.sub(r'[^\w]', '', word)
        if cleaned.isupper() and 1 <= len(cleaned) <= 5 and cleaned.isalpha():
            tickers.append(cleaned)

    # Validate and deduplicate
    parser = ImageParser.__new__(ImageParser)  # Create without __init__ for validation methods
    valid_tickers = []
    seen = set()
    for ticker in tickers:
        if ticker not in seen and parser._is_valid_ticker(ticker):
            seen.add(ticker)
            valid_tickers.append(ticker)

    return valid_tickers
