"""Tests for image parser optimizations."""

import asyncio
import hashlib
import json
import pytest
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from image_parser import (
    ImageParser,
    is_valid_ticker,
    parse_text_for_tickers,
    QuotaExceededError,
    FALSE_POSITIVE_TICKERS,
    MAX_RETRIES,
    INITIAL_BACKOFF,
)
from database import ImageCache, init_database, get_connection
from config import DATABASE_PATH, CACHE_TTL_HOURS


class TestIsValidTicker:
    """Test the standalone is_valid_ticker function."""

    def test_valid_tickers(self):
        """Test valid ticker symbols."""
        assert is_valid_ticker("AAPL") is True
        assert is_valid_ticker("MSFT") is True
        assert is_valid_ticker("GOOGL") is True
        assert is_valid_ticker("META") is True
        assert is_valid_ticker("TSLA") is True
        assert is_valid_ticker("A") is True  # Single letter
        assert is_valid_ticker("BRK.B") is True  # With suffix

    def test_invalid_tickers(self):
        """Test invalid ticker symbols."""
        assert is_valid_ticker("") is False
        assert is_valid_ticker("TOOLONG") is False  # > 5 letters
        assert is_valid_ticker("123") is False  # Numbers
        assert is_valid_ticker("AB12") is False  # Mixed
        assert is_valid_ticker("abc") is False  # Lowercase

    def test_false_positives(self):
        """Test that common false positives are rejected."""
        assert is_valid_ticker("CEO") is False
        assert is_valid_ticker("IPO") is False
        assert is_valid_ticker("USD") is False
        assert is_valid_ticker("NYSE") is False
        assert is_valid_ticker("API") is False
        assert is_valid_ticker("THE") is False
        assert is_valid_ticker("AND") is False

    def test_edge_cases(self):
        """Test edge cases."""
        assert is_valid_ticker(None) is False
        assert is_valid_ticker("A.B.C") is False  # Multiple dots
        assert is_valid_ticker(".AAPL") is False  # Leading dot


class TestImageCache:
    """Test ImageCache database operations."""

    @pytest.fixture(autouse=True)
    def setup_database(self):
        """Initialize clean database for each test."""
        import config
        import database
        import tempfile
        original_config_path = config.DATABASE_PATH
        original_db_path = database.DATABASE_PATH

        # Create a temporary database file
        temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        config.DATABASE_PATH = temp_db.name
        database.DATABASE_PATH = temp_db.name
        temp_db.close()

        init_database()
        yield

        # Clean up
        config.DATABASE_PATH = original_config_path
        database.DATABASE_PATH = original_db_path
        import os
        try:
            os.unlink(temp_db.name)
        except:
            pass

    def test_cache_set_and_get(self):
        """Test setting and getting cached tickers."""
        image_hash = "abc123def456"
        tickers = ["AAPL", "MSFT", "GOOGL"]

        ImageCache.set(image_hash, tickers)
        result = ImageCache.get(image_hash)

        assert result == tickers

    def test_cache_miss(self):
        """Test cache miss returns None."""
        result = ImageCache.get("nonexistent_hash")
        assert result is None

    def test_cache_json_serialization(self):
        """Test that tickers are properly serialized as JSON."""
        image_hash = "test_hash"
        tickers = ["AAPL", "MSFT"]

        ImageCache.set(image_hash, tickers)

        # Check raw database value
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT tickers FROM image_cache WHERE image_hash = ?", (image_hash,))
            row = cursor.fetchone()

            # Should be JSON string
            assert row is not None
            stored_json = row["tickers"]
            assert isinstance(stored_json, str)
            assert json.loads(stored_json) == tickers

    def test_cache_expiry(self):
        """Test that expired cache entries are not returned."""
        image_hash = "expired_hash"
        tickers = ["AAPL"]

        # Insert with expired timestamp
        with get_connection() as conn:
            cursor = conn.cursor()
            expired_time = (datetime.now() - timedelta(hours=CACHE_TTL_HOURS + 1)).isoformat()
            cursor.execute(
                "INSERT INTO image_cache (image_hash, tickers, cached_at) VALUES (?, ?, ?)",
                (image_hash, json.dumps(tickers), expired_time)
            )

        # Should return None and delete the entry
        result = ImageCache.get(image_hash)
        assert result is None

        # Verify it was deleted
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM image_cache WHERE image_hash = ?", (image_hash,))
            count = cursor.fetchone()[0]
            assert count == 0

    def test_cache_clear_expired(self):
        """Test clearing expired cache entries."""
        # Add fresh entry
        ImageCache.set("fresh_hash", ["AAPL"])

        # Add expired entry
        with get_connection() as conn:
            cursor = conn.cursor()
            expired_time = (datetime.now() - timedelta(hours=CACHE_TTL_HOURS + 1)).isoformat()
            cursor.execute(
                "INSERT INTO image_cache (image_hash, tickers, cached_at) VALUES (?, ?, ?)",
                ("expired_hash", json.dumps(["MSFT"]), expired_time)
            )

        # Clear expired
        ImageCache.clear_expired()

        # Fresh should remain, expired should be gone
        assert ImageCache.get("fresh_hash") is not None
        assert ImageCache.get("expired_hash") is None

    def test_cache_empty_list(self):
        """Test caching empty ticker list."""
        image_hash = "empty_hash"
        tickers = []

        ImageCache.set(image_hash, tickers)
        result = ImageCache.get(image_hash)

        assert result == []


class TestImageParserHash:
    """Test image hash computation."""

    def test_compute_image_hash(self):
        """Test hash computation produces consistent results."""
        image_data = b"fake image bytes"

        hash1 = ImageParser.compute_image_hash(image_data)
        hash2 = ImageParser.compute_image_hash(image_data)

        assert hash1 == hash2
        assert len(hash1) == 32  # SHA-256 truncated to 32 chars

    def test_different_images_different_hashes(self):
        """Test different images produce different hashes."""
        image1 = b"image 1"
        image2 = b"image 2"

        hash1 = ImageParser.compute_image_hash(image1)
        hash2 = ImageParser.compute_image_hash(image2)

        assert hash1 != hash2

    def test_hash_deterministic(self):
        """Test hash is deterministic for same input."""
        image_data = b"test image content"
        expected_hash = hashlib.sha256(image_data).hexdigest()[:32]

        result = ImageParser.compute_image_hash(image_data)
        assert result == expected_hash


class TestParseTextForTickers:
    """Test parse_text_for_tickers function without ImageParser instance."""

    def test_cashtag_extraction(self):
        """Test extraction of tickers with dollar signs."""
        text = "Check out $AAPL and $MSFT today!"
        result = parse_text_for_tickers(text)

        assert "AAPL" in result
        assert "MSFT" in result

    def test_uppercase_word_extraction(self):
        """Test extraction of standalone uppercase words."""
        text = "I like AAPL and GOOGL for long term"
        result = parse_text_for_tickers(text)

        assert "AAPL" in result
        assert "GOOGL" in result

    def test_false_positive_filtering(self):
        """Test that false positives are filtered out."""
        text = "The CEO said the IPO will be on NYSE"
        result = parse_text_for_tickers(text)

        # These are all in FALSE_POSITIVE_TICKERS
        assert "CEO" not in result
        assert "IPO" not in result
        assert "NYSE" not in result
        assert "THE" not in result

    def test_deduplication(self):
        """Test that duplicate tickers are removed."""
        text = "$AAPL AAPL $AAPL"
        result = parse_text_for_tickers(text)

        assert result.count("AAPL") == 1

    def test_mixed_case_normalization(self):
        """Test that lowercase is converted to uppercase."""
        text = "$aapl $MsFt"
        result = parse_text_for_tickers(text)

        assert "AAPL" in result
        assert "MSFT" in result

    def test_no_tickers(self):
        """Test empty result when no tickers found."""
        text = "This is just regular text with no tickers"
        result = parse_text_for_tickers(text)

        assert result == []


@pytest.mark.asyncio
class TestImageParserDailyRotation:
    """Test daily model rotation logic."""

    @patch('image_parser.genai.Client')
    def test_counter_resets_on_new_day(self, mock_client):
        """Test that request counter and exhausted models reset daily."""
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'}):
            parser = ImageParser()

            # Simulate some usage
            parser._request_counter = 5
            parser._exhausted_models.add("gemini-2.5-flash")
            parser._counter_date = "2025-01-01"  # Old date

            model = parser._get_next_model()

            assert parser._request_counter == 1  # Reset to 0 then incremented
            assert len(parser._exhausted_models) == 0
            assert model == parser.models[0]  # Starts from first model

    @patch('image_parser.genai.Client')
    def test_round_robin_rotation(self, mock_client):
        """Test that models rotate round-robin per request."""
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'}):
            parser = ImageParser()

            models_picked = []
            for _ in range(len(parser.models) * 2):
                model = parser._get_next_model()
                models_picked.append(model)

            # Should cycle through all models twice
            assert models_picked[:4] == parser.models
            assert models_picked[4:8] == parser.models

    @patch('image_parser.genai.Client')
    def test_skips_exhausted_models(self, mock_client):
        """Test that exhausted models are skipped in rotation."""
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'}):
            parser = ImageParser()

            # Set today's date so reset doesn't clear exhausted set
            parser._counter_date = time.strftime("%Y-%m-%d")
            parser._exhausted_models.add(parser.models[0])

            model = parser._get_next_model()
            assert model == parser.models[1]

    @patch('image_parser.genai.Client')
    def test_all_models_exhausted_returns_none(self, mock_client):
        """Test that None is returned when all models are exhausted."""
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'}):
            parser = ImageParser()

            parser._counter_date = time.strftime("%Y-%m-%d")
            for m in parser.models:
                parser._exhausted_models.add(m)

            assert parser._get_next_model() is None


@pytest.mark.asyncio
class TestImageParserRetryLogic:
    """Test retry and model rotation on errors."""

    @patch('image_parser.genai.Client')
    async def test_quota_error_rotates_to_next_model(self, mock_client):
        """Test that quota errors mark model exhausted and try next."""
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'}):
            parser = ImageParser()

            call_count = 0
            mock_response = MagicMock()
            mock_response.text = '{"tickers": ["AAPL"], "confidence": "high"}'

            async def mock_generate(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise Exception("429 Rate limit exceeded")
                return mock_response

            mock_client.return_value.aio.models.generate_content = mock_generate

            result = await parser.extract_tickers(b"test image")

            assert "AAPL" in result
            assert call_count == 3
            # First two models should be marked exhausted
            assert len(parser._exhausted_models) == 2

    @patch('image_parser.genai.Client')
    async def test_all_models_exhausted_raises_quota_exceeded(self, mock_client):
        """Test that QuotaExceededError is raised when all models hit quota."""
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'}):
            parser = ImageParser()

            async def mock_generate(*args, **kwargs):
                raise Exception("429 Rate limit exceeded")

            mock_client.return_value.aio.models.generate_content = mock_generate

            with pytest.raises(QuotaExceededError):
                await parser.extract_tickers(b"test image")

            assert len(parser._exhausted_models) == len(parser.models)

    @patch('image_parser.genai.Client')
    async def test_non_quota_error_retries_with_backoff(self, mock_client):
        """Test that non-quota errors retry with exponential backoff on same model."""
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'}):
            parser = ImageParser()

            call_count = 0
            mock_response = MagicMock()
            mock_response.text = '{"tickers": ["MSFT"], "confidence": "high"}'

            async def mock_generate(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count < 3:
                    raise Exception("Some server error")
                return mock_response

            mock_client.return_value.aio.models.generate_content = mock_generate

            start = time.time()
            result = await parser.extract_tickers(b"test image")
            duration = time.time() - start

            assert "MSFT" in result
            assert call_count == 3
            # Backoff: 1s + 2s = 3s minimum
            assert duration >= 3.0
            # No models should be marked exhausted (not a quota error)
            assert len(parser._exhausted_models) == 0

    @patch('image_parser.genai.Client')
    async def test_non_quota_error_returns_empty_after_max_retries(self, mock_client):
        """Test that persistent non-quota errors return empty list."""
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'}):
            parser = ImageParser()

            async def mock_generate(*args, **kwargs):
                raise Exception("Some other error")

            mock_client.return_value.aio.models.generate_content = mock_generate

            result = await parser.extract_tickers(b"test image")

            assert result == []


@pytest.mark.asyncio
class TestImageParserCacheIntegration:
    """Test ImageParser integration with ImageCache."""

    @pytest.fixture(autouse=True)
    def setup_database(self):
        """Initialize clean database for each test."""
        import config
        import database
        import tempfile
        original_config_path = config.DATABASE_PATH
        original_db_path = database.DATABASE_PATH

        # Create a temporary database file
        temp_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        config.DATABASE_PATH = temp_db.name
        database.DATABASE_PATH = temp_db.name
        temp_db.close()

        init_database()
        yield

        # Clean up
        config.DATABASE_PATH = original_config_path
        database.DATABASE_PATH = original_db_path
        import os
        try:
            os.unlink(temp_db.name)
        except:
            pass

    @patch('image_parser.genai.Client')
    async def test_cache_hit_skips_api_call(self, mock_client):
        """Test that cache hit prevents API call."""
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'}):
            parser = ImageParser(image_cache=ImageCache)

            image_data = b"test image"
            image_hash = ImageParser.compute_image_hash(image_data)

            # Pre-populate cache
            ImageCache.set(image_hash, ["AAPL", "MSFT"])

            # Mock should never be called
            mock_client.return_value.aio.models.generate_content = AsyncMock()

            result = await parser.extract_tickers(image_data)

            assert result == ["AAPL", "MSFT"]
            mock_client.return_value.aio.models.generate_content.assert_not_called()

    @patch('image_parser.genai.Client')
    async def test_cache_miss_calls_api_and_caches(self, mock_client):
        """Test that cache miss calls API and stores result."""
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'}):
            parser = ImageParser(image_cache=ImageCache)

            # Mock successful API response
            mock_response = MagicMock()
            mock_response.text = '{"tickers": ["GOOGL"], "confidence": "high"}'

            async def mock_generate(*args, **kwargs):
                return mock_response

            mock_client.return_value.aio.models.generate_content = mock_generate

            # Use different image data to avoid collision with previous test
            image_data = b"different test image for cache miss"
            image_hash = ImageParser.compute_image_hash(image_data)

            # Ensure cache is empty
            assert ImageCache.get(image_hash) is None

            result = await parser.extract_tickers(image_data)

            # Result should be correct
            assert "GOOGL" in result

            # Result should be cached
            cached = ImageCache.get(image_hash)
            assert cached == result

    @patch('image_parser.genai.Client')
    async def test_parser_works_without_cache(self, mock_client):
        """Test that parser works when no cache is provided."""
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test_key'}):
            parser = ImageParser(image_cache=None)

            mock_response = MagicMock()
            mock_response.text = '{"tickers": ["META"], "confidence": "high"}'

            async def mock_generate(*args, **kwargs):
                return mock_response

            mock_client.return_value.aio.models.generate_content = mock_generate

            image_data = b"test image"
            result = await parser.extract_tickers(image_data)

            assert "META" in result


class TestPhotoSelection:
    """Test photo selection logic (conceptual test)."""

    def test_photo_index_calculation(self):
        """Test the photo index calculation logic from bot.py."""
        # Simulating the logic: photo_index = min(len(photos) - 1, max(1, len(photos) // 2))

        # 1 photo - should use index 0
        photos = [1]
        index = min(len(photos) - 1, max(1, len(photos) // 2))
        assert index == 0

        # 2 photos - should use index 1
        photos = [1, 2]
        index = min(len(photos) - 1, max(1, len(photos) // 2))
        assert index == 1

        # 3 photos - should use index 1 (middle)
        photos = [1, 2, 3]
        index = min(len(photos) - 1, max(1, len(photos) // 2))
        assert index == 1

        # 4 photos - should use index 2
        photos = [1, 2, 3, 4]
        index = min(len(photos) - 1, max(1, len(photos) // 2))
        assert index == 2

        # 5 photos - should use index 2
        photos = [1, 2, 3, 4, 5]
        index = min(len(photos) - 1, max(1, len(photos) // 2))
        assert index == 2

        # 10 photos - should use index 5
        photos = list(range(10))
        index = min(len(photos) - 1, max(1, len(photos) // 2))
        assert index == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
