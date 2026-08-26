"""Tests for replaceable image ticker extraction."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from image_extractors import ImageExtractor
from plugins import load_image_extractor


class FakeImageExtractor(ImageExtractor):
    def __init__(self, image_cache=None):
        self.image_cache = image_cache

    async def extract_tickers(self, image_data: bytes) -> list[str]:
        return ["AAPL", "BRK.B"]


def test_image_extractor_can_be_disabled():
    assert load_image_extractor(plugin_path="") is None


def test_image_extractor_loads_by_import_path():
    extractor = load_image_extractor(plugin_path="test_image_extractor_plugin:FakeImageExtractor")
    assert asyncio.run(extractor.extract_tickers(b"image")) == ["AAPL", "BRK.B"]
