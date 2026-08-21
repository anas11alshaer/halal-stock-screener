# Future Architecture Plan — Vercel + Modular Plugin (Deferred Execution)

**Status:** `planning` — documented 2026-08-21, not yet implemented. Next build will start from this file. Work type for implementation will be `new-feature` + `refactor` + `release` as sliced.

**Rule from this session:** Project architecture is always `src/` + `tests/` at root; inside `src/` are **packagenames** (per `D:\projects\guide\language-packs\python.md:28`). No flat `apps/bot/modules/...` future. Current `src/bot.py`, `src/screener.py`, etc. will be migrated into packages.

## 1. Scope of the large change (what you asked)

1.  **Deployment:** Oracle → Vercel. Decide Docker fate after.
2.  **Reliability:** Musaffa + Zoya today are brittle (SSR chip parsing `src/scrapers/musaffa.py:32` + `zoya.py`, `get_quote_type` via `yfinance` `src/scrapers/base.py:49`). You manually re-check answers. Need trust without manual work.
3.  **Modularity:** Code should feel like **plugins** — core screener usable as library by any future stock project, not tied to Telegram.
4.  **Usability:** Telegram-only today (`src/bot.py:1`). Need alternatives / improvements so anyone can use it.

## 2. Target layout (src + tests, packagenames inside src)

House python pack rule (`templates/python/`): `src/<packagename>/` + `tests/`. For this repo we split into **one core library + thin adapters**, all still under single `src/`:

```
halal-stock-screener/
├── src/
│   ├── halal_screener_core/        # pip-installable core — NO telegram, NO os.environ except config boundary
│   │   ├── __init__.py
│   │   ├── screening/              # ScreeningEngine, Batch, parallel orchestration (from src/screener.py:190)
│   │   ├── sources/                # SourcePlugin interface + implementations
│   │   │   ├── base.py            # from src/scrapers/base.py:96 (BaseScraper → SourcePlugin)
│   │   │   ├── musaffa.py         # from src/scrapers/musaffa.py:32
│   │   │   └── zoya.py            # from src/scrapers/zoya.py:28 (ETF guard)
│   │   ├── resolver/               # ConservativeResolver (from src/resolver.py:11) as pluggable policy
│   │   ├── cache/                  # CachePolicy + Store (from src/database.py:115, src/config.py:26)
│   │   ├── image/                  # ImageParser (from src/image_parser.py:112, src/config.py:40)
│   │   └── config.py               # from src/config.py:1 (only place that reads env)
│   ├── halal_screener_transports/  # transport adapters — each is a plugin
│   │   ├── telegram/               # from src/bot.py:62-238
│   │   ├── web_api/                # FastAPI POST /screen → JSON (future, for Vercel)
│   │   └── cli/                    # pipx-style CLI (future)
│   └── halal_screener_app/         # thin composition roots (not libraries)
│       ├── bot.py                  # polling entry (Oracle phase)
│       └── vercel_api.py           # webhook entry (Vercel phase, see §4)
├── tests/
│   ├── test_core/                  # mirrors core/*
│   ├── test_sources/
│   ├── test_transports/
│   └── test_image_parser.py        # from tests/test_image_parser.py:1
├── docs/
│   ├── adr/0001..0007 (existing) + 0008..0012 future
│   └── planning/2026-08-21-vercel-modular-future.md (this file)
├── .github/workflows/ci.yml        # already at .github/workflows/ci.yml:1 (ruff + pytest on PR)
├── requirements.txt                # kept per your instruction (single source; pyproject.toml deleted)
├── .env.example                    # TELEGRAM_BOT_TOKEN, GEMINI_API_KEY, DATABASE_URL (future)
└── data/  logs/  (gitignored, see .gitignore:25, .gitignore:31)
```

**Why this shape:** `core` has zero Telegram import, so any future stock project can `pip install` or `import halal_screener_core` and compose its own sources/transports. Transports and sources are swappable without touching `core`.

## 3. Plugin contracts (to be typed in python pack)

- **SourcePlugin** (replaces `BaseScraper`):
  ```python
  class SourcePlugin(ABC):
      name: str
      async def fetch(self, ticker: str, client: httpx.AsyncClient) -> ComplianceResult
      def supports(self, quote_type: str) -> bool  # ETF vs stock
  ```
  Returns `{status: ComplianceStatus, company_name, evidence_url, raw_hash, raw_snippet}` — evidence kept for audit, fixes cache display bug (`database.py` only stored status).

- **ResolverPolicy** (today conservative `NOT_HALAL > DOUBTFUL > HALAL > NOT_COVERED` from `src/resolver.py:11`):
  ```python
  def resolve(results: list[ComplianceResult]) -> FinalVerdict
  ```

- **TransportAdapter**:
  ```python
  async def parse_input(self, update) -> list[str]   # text or image
  async def send(self, verdicts) -> None
  ```

## 4. Deployment — Oracle today → Vercel future (Docker decision deferred)

- **Today:** Polling `Application.run_polling` + health thread `HTTPServer("0.0.0.0", PORT)` `src/bot.py:50-55` on Oracle via `Dockerfile`/`deploy.bat` (kept per cleanup, to be removed later). SQLite `data/stock_screener.db:1` is file-local.
- **Vercel reality:** Vercel is serverless — no always-on polling. Bot must switch to **webhook** (`setWebhook` → Vercel Function `src/halal_screener_app/vercel_api.py: POST /api/webhook`). No `HTTPServer`, no `PORT` thread, no local SQLite (ephemeral). Need managed store (`DATABASE_URL` → Postgres via Vercel Postgres/Neon or Upstash) and Vercel Cron for cache expiry.
- **Docker question you raised:** If Vercel is only for **web UI/API** (hybrid), keep `Dockerfile` for bot. If bot itself moves to Vercel webhook, Docker is no longer used for deploy — keep it only for local dev then delete as you planned. **Decision deferred** until Phase 1 proves `core` extraction.

**Recommended migration path (no code yet):**
- Phase 0 (this doc): lock interfaces, leave Oracle polling running.
- Phase 1: Extract `core` packages without changing runtime — Oracle still polling, tests mirror new `src/` packages.
- Phase 2: Add `web_api` transport on Vercel (reads `core` as lib), keep bot on Oracle — validate usability for “anyone” without Telegram.
- Phase 3 (if you choose Vercel for bot): webhook + DB migration, then delete `Dockerfile`/`deploy.bat`.

## 5. Reliability — fixing Musaffa/Zoya trust without manual re-check

- **Short-term (keep scrapers):** Add `evidence_hash` + `raw_snippet` + `confidence` logging in `database.py:115`, alert when selectors fall back, add 3rd free sanity check (e.g., SEC SIC or Islamicly public page) behind plugin flag.
- **Medium-term:** `SourcePlugin` lets you plug **IdealRatings / AAOIFI / MSCI Islamic** behind feature flag without touching `core`. Resolver stays conservative but weighted when 3 sources present. Requires your paid-feed budget call.
- **Long-term:** Confidence score + “needs human review” queue when sources disagree or `NOT_COVERED` > threshold. That queue lives in future `apps/` replacement (not current `apps/` deleted).

## 6. Usability — beyond Telegram

- **As library:** `from halal_screener_core import ScreeningEngine; engine = ScreeningEngine(sources=[Musaffa(), Zoya()], resolver=Conservative())` — any app imports it.
- **As service:** `POST /screen {tickers: ["AAPL","SPUS"]}` → JSON — works from any stack; what Vercel web exposes.
- **Transports (priority to be chosen next session):** `telegram` (keep), `web_api` (Vercel), `cli` (`pipx halal-screen AAPL`), `discord`/`whatsapp` later. Credentials stay in `.env.example` (`TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, future `DATABASE_URL`).

## 7. Open decisions to lock next planning session (answer before any build)

1. Vercel scope: (a) bot on Vercel, (b) web UI/API on Vercel + bot stays on Oracle, (c) full off Oracle?
2. Paid Shariah feed budget: free scraping only, or IdealRatings/AAOIFI paid to reduce manual checks?
3. “Anyone” means: pip library vs public web app with no Telegram needed?
4. Transport priority after Telegram: web_api, cli, discord, whatsapp?
5. Core distribution: pip-installable vs service HTTP API (or both)?
6. Docker fate: keep for local dev / non-Vercel, or drop entirely?
7. DB for Vercel: Postgres/Neon/Upstash vs keep SQLite (only viable if not on Vercel)?
8. Accuracy bar to trust without manual check: 2/2 agree, or 3 sources + confidence + audit log?

## 8. Next build — how to start (when you say go)

- Create Issue using `.github/ISSUE_TEMPLATE/ISSUE.md:1` (work type `refactor` + `new-feature`), paste this doc link in Notes.
- Branch `refactor/core-plugin-extraction` from `main` (`688e7bd`), move code into `src/halal_screener_core/...` keeping `src/`+`tests/` pack shape, mirror tests, keep Oracle polling until Phase 2.
- ADRs to write then: `0008-vercel-vs-polling`, `0009-core-plugin-boundaries`, `0010-database-choice`, `0011-source-reliability-strategy`.

---
*Deferred execution per your request — no code moved yet. This file is the handoff for the next planning → build session.*
