"""Diagnostic test: raw page text and extraction results for ETFs (VOO, QQQ)."""
import asyncio
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from scrapers import MusaffaScraper, ZoyaScraper
from scrapers.base import get_chromium_path, get_quote_type
from config import MUSAFFA_BASE_URL, ZOYA_BASE_URL, REQUEST_TIMEOUT

logging.basicConfig(level=logging.WARNING)  # suppress scraper noise; we print manually

TICKERS = ["VOO", "QQQ"]
MUSAFFA_ETF_BASE_URL = "https://musaffa.com/etf"
SEPARATOR = "-" * 70


def show_musaffa_extraction(ticker: str, text: str):
    """Replicate Musaffa _parse_content logic and show each decision point."""
    text_lower = text.lower()

    print(f"\n  [Musaffa extraction for {ticker}]")
    has_compliance = "shariah compliance" in text_lower
    print(f"  'shariah compliance' found in page: {has_compliance}")

    compliance_section = ""
    if has_compliance:
        idx = text_lower.find("shariah compliance")
        start = max(0, idx - 50)
        end = min(len(text), idx + 200)
        compliance_section = text_lower[start:end]
        print(f"  Compliance window ({start}:{end}):\n    {repr(compliance_section)}")

    checks = {
        "'not halal' in window":  "not halal" in compliance_section,
        "'non-halal' in window":  "non-halal" in compliance_section,
        "'doubtful' in window":   "doubtful" in compliance_section,
        "'halal' in window":      "halal" in compliance_section,
        r"'\bhalal\b' anywhere":  bool(re.search(r'\bhalal\b', text_lower)),
        r"'\bnot\s+halal\b' anywhere": bool(re.search(r'\bnot\s+halal\b', text_lower)),
    }
    for label, val in checks.items():
        print(f"  {label}: {val}")


def show_zoya_extraction(ticker: str, text: str):
    """Replicate Zoya _parse_content logic and show each decision point."""
    text_lower = text.lower()

    print(f"\n  [Zoya extraction for {ticker}]")
    checks = {
        "'is not shariah-compliant'":  "is not shariah-compliant" in text_lower,
        "'not shariah compliant'":     "not shariah compliant" in text_lower,
        "'not considered halal'":      "not considered halal" in text_lower,
        "'flagged'":                   "flagged" in text_lower,
        "'questionable'":              "questionable" in text_lower,
        "'is shariah-compliant'":      "is shariah-compliant" in text_lower,
        "'shariah-compliant'":         "shariah-compliant" in text_lower,
        "'considered halal'":          "considered halal" in text_lower,
    }
    for label, val in checks.items():
        print(f"  {label}: {val}")


async def fetch_musaffa_authenticated(scraper: MusaffaScraper, ticker: str, etf: bool) -> tuple[str, str | None]:
    """Fetch Musaffa page text using the scraper's authenticated session.

    Returns (page_text, chip_text_or_None).
    """
    base = MUSAFFA_ETF_BASE_URL if etf else MUSAFFA_BASE_URL
    url = f"{base}/{ticker.upper()}/"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=get_chromium_path())
        context = await scraper._get_context(browser)
        page = await context.new_page()
        try:
            await page.goto(url, timeout=REQUEST_TIMEOUT * 1000)
        except PlaywrightTimeout:
            await browser.close()
            return "", None
        try:
            await page.wait_for_selector("text=Shariah Compliance", timeout=10000)
        except PlaywrightTimeout:
            pass
        import asyncio as _a; await _a.sleep(0.5)

        text = await page.evaluate("() => document.body.innerText")

        # Check DOM chip (mirrors _parse_chip logic)
        chip_text = None
        locked = await page.query_selector('div.compliance-chip.locked-chip')
        if not locked:
            el = await page.query_selector('div.compliance-chip:not(.locked-chip) h5.status-text')
            if el:
                chip_text = (await el.inner_text()).strip()

        await browser.close()
        return text, chip_text


async def fetch_zoya_raw(ticker: str) -> str:
    url = f"{ZOYA_BASE_URL}/{ticker.lower()}"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=get_chromium_path())
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()
        try:
            await page.goto(url, timeout=REQUEST_TIMEOUT * 1000)
        except PlaywrightTimeout:
            await browser.close()
            return ""
        try:
            await page.wait_for_selector("text=Shariah", timeout=10000)
        except PlaywrightTimeout:
            pass
        await asyncio.sleep(0.5)
        text = await page.evaluate("() => document.body.innerText")
        await browser.close()
        return text


async def run():
    musaffa = MusaffaScraper()
    zoya = ZoyaScraper()

    for ticker in TICKERS:
        print(f"\n{'=' * 70}")
        print(f"  TICKER: {ticker}")
        print('=' * 70)

        # Determine URL type via yfinance (same as scraper)
        quote_type = await get_quote_type(ticker)
        is_etf = quote_type == "ETF"
        print(f"\nyfinance quote type: {quote_type}  =>  using {'ETF' if is_etf else 'stock'} URL")

        # --- Musaffa (authenticated) ---
        url_label = f"musaffa.com/etf/{ticker}/" if is_etf else f"musaffa.com/stock/{ticker}/"
        print(f"\n--- MUSAFFA (authenticated, {url_label}) ---")
        print("Fetching page text with login session...")
        raw, chip_text = await fetch_musaffa_authenticated(musaffa, ticker, etf=is_etf)
        if raw:
            low = raw.lower()
            idx = low.find("shariah compliance")
            if idx != -1:
                snippet_start = max(0, idx - 100)
                snippet_end = min(len(raw), idx + 500)
                print(f"Page snippet around 'Shariah Compliance':")
                print(raw[snippet_start:snippet_end])
            else:
                print("'Shariah Compliance' NOT found. First 800 chars of page:")
                print(raw[:800])
            print(f"\n  DOM chip text (authenticated): {repr(chip_text)}")
            show_musaffa_extraction(ticker, raw)
        else:
            print("  ERROR: could not fetch page")

        print(f"\nScraper result (screen_ticker — uses yfinance + login):")
        result = await musaffa.screen_ticker(ticker)
        print(f"  status={result.status.value}  source={result.source}  error={result.error_message}")

        # --- Zoya ---
        print(f"\n--- ZOYA ---")
        print(f"Fetching raw page text...")
        raw = await fetch_zoya_raw(ticker)
        if raw:
            low = raw.lower()
            idx = low.find("shariah")
            if idx != -1:
                snippet_start = max(0, idx - 100)
                snippet_end = min(len(raw), idx + 500)
                print(f"Page snippet around 'Shariah' ({snippet_start}:{snippet_end}):")
                print(raw[snippet_start:snippet_end])
            else:
                print("'Shariah' NOT found. First 800 chars of page:")
                print(raw[:800])
            show_zoya_extraction(ticker, raw)
        else:
            print("  ERROR: could not fetch page")

        print(f"\nScraper result:")
        result = await zoya.screen_ticker(ticker)
        print(f"  status={result.status.value}  source={result.source}  error={result.error_message}")


if __name__ == "__main__":
    asyncio.run(run())
