"""Test scrapers with actual sites."""
import asyncio
import sys
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from scraper import MusaffaScraper, ComplianceStatus, ScreeningResult
from zoya_scraper import ZoyaScraper
from resolver import resolve_compliance


async def test_musaffa():
    """Test Musaffa scraper."""
    scraper = MusaffaScraper()

    print("Testing Musaffa scraper with AAPL...")
    result = await scraper.screen_ticker("AAPL")
    print(f"  Status: {result.status.value}")
    print(f"  Source: {result.source}")
    print(f"  Company: {result.company_name}")
    print(f"  Ranking: {result.compliance_ranking}")
    if result.error_message:
        print(f"  Error: {result.error_message}")
    return result


async def test_zoya():
    """Test Zoya scraper."""
    scraper = ZoyaScraper()

    print("\nTesting Zoya scraper with AAPL...")
    result = await scraper.screen_ticker("AAPL")
    print(f"  Status: {result.status.value}")
    print(f"  Source: {result.source}")
    if result.error_message:
        print(f"  Error: {result.error_message}")
    return result


def test_resolver():
    """Test resolver conflict logic."""
    print("\nTesting resolver conflict logic...")

    # Test case 1: Both agree HALAL
    r1 = ScreeningResult(ticker="TEST", status=ComplianceStatus.HALAL, source="musaffa")
    r2 = ScreeningResult(ticker="TEST", status=ComplianceStatus.HALAL, source="zoya")
    final, conflict = resolve_compliance(r1, r2)
    print(f"  Both HALAL: final={final.status.value}, conflict={conflict}")
    assert final.status == ComplianceStatus.HALAL
    assert not conflict

    # Test case 2: Conflict - Musaffa HALAL, Zoya NOT_HALAL
    r1 = ScreeningResult(ticker="TEST", status=ComplianceStatus.HALAL, source="musaffa")
    r2 = ScreeningResult(ticker="TEST", status=ComplianceStatus.NOT_HALAL, source="zoya")
    final, conflict = resolve_compliance(r1, r2)
    print(f"  HALAL vs NOT_HALAL: final={final.status.value}, conflict={conflict}")
    assert final.status == ComplianceStatus.NOT_HALAL  # More restrictive wins
    assert conflict

    # Test case 3: One source NOT_COVERED
    r1 = ScreeningResult(ticker="TEST", status=ComplianceStatus.HALAL, source="musaffa")
    r2 = ScreeningResult(ticker="TEST", status=ComplianceStatus.NOT_COVERED, source="zoya")
    final, conflict = resolve_compliance(r1, r2)
    print(f"  HALAL vs NOT_COVERED: final={final.status.value}, conflict={conflict}")
    assert final.status == ComplianceStatus.HALAL  # Use the valid source
    assert not conflict

    # Test case 4: Conflict - HALAL vs DOUBTFUL
    r1 = ScreeningResult(ticker="TEST", status=ComplianceStatus.HALAL, source="musaffa")
    r2 = ScreeningResult(ticker="TEST", status=ComplianceStatus.DOUBTFUL, source="zoya")
    final, conflict = resolve_compliance(r1, r2)
    print(f"  HALAL vs DOUBTFUL: final={final.status.value}, conflict={conflict}")
    assert final.status == ComplianceStatus.DOUBTFUL  # More restrictive wins
    assert conflict

    print("  All resolver tests passed!")


async def test_combined():
    """Test both scrapers with the same ticker."""
    print("\nTesting both scrapers with AAPL...")

    musaffa_result = await test_musaffa()
    zoya_result = await test_zoya()

    print("\nResolving combined results...")
    final, is_conflict = resolve_compliance(musaffa_result, zoya_result)
    print(f"  Final status: {final.status.value}")
    print(f"  Is conflict: {is_conflict}")


if __name__ == "__main__":
    # Run resolver tests first (no network needed)
    test_resolver()

    # Run scraper tests
    asyncio.run(test_combined())
