"""Test FIGR with actual scraper."""
import asyncio
import sys
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scraper import MusaffaScraper

async def test():
    scraper = MusaffaScraper()

    print("Testing FIGR...")
    result = await scraper.screen_ticker("FIGR")
    print(f"  Status: {result.status.value}")
    print(f"  Company: {result.company_name}")
    print(f"  Ranking: {result.compliance_ranking}")
    if result.error_message:
        print(f"  Error: {result.error_message}")

if __name__ == "__main__":
    asyncio.run(test())
