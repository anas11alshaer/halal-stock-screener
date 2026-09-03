# Engine completeness — DJIM/S&P 33%, doubtful→NOT HALAL, data-finder LLM

**Status:** research session (do **not** implement in the first pass). Planned 2026-08-22.

**Issue:** [#7](https://github.com/anas11alshaer/halal-stock-screener/issues/7)

**Branch:** stay on `fix/7-source-reliability`. Do not commit to `main`. Do not create `develop`. Do not wire `src/bot.py`. Do not delete Docker / drop Gemini in this research session.

**This file is the handoff.** Paste the **Next-session prompt** below into a new Grok chat (or run `/design` on it after reading this file). Implement only after the human signs off the research notes.

---

## Next-session prompt (copy from here)

```text
/design --effort 2

Work type: research / spike then a short design. Do not implement plugins, do not change production Telegram, do not commit to main, do not create develop. Stay on branch fix/7-source-reliability. Keep the existing dirty/uncommitted eval work (OpenFIGI-off-hot-path, STIV skip, series-id, Activity-both-fields, companyfacts-sum, scripts/compare_mz_bar.py). Do not revert it.

Read first: AGENTS.md, this file docs/planning/2026-08-22-engine-completeness-research.md, docs/planning/2026-08-22-source-reliability.md, docs/adr/0008-policy-driven-screener.md, 0009-nport-etf-lookthrough.md, 0010-nvidia-image-parser.md, config/screening_policy.toml, config/eval_fixtures.toml, src/verdict_engine.py, src/fusion.py, src/market_data.py, src/plugins/*, src/nvidia_nim.py, src/screener.py, src/bot.py, scripts/eval_screener.py, scripts/compare_mz_bar.py, tests/test_verdict_engine.py.

## Goal of this session

Research whether the current eval engine’s categories, data, and NVIDIA use are too thin to be a real Shariah screener — then write a design for the next implement session. The human will implement later. This session: read, review, research, recommend, stop.

Musaffa and Zoya are a TEST BAR only. They will be removed from production. Do not add them as VerdictEngine plugins. Do not parse their HTML for HALAL. We compared against them to see if our engine works; future production is our engine only.

## Human decisions already made (do not reopen)

1. Screening standard: switch ratios from AAOIFI 30% to DJIM / S&P Shariah 33% for debt (and research the rest of that pair — cash, receivables, trailing average market cap vs spot, post-2023 S&P dropping cash/AR screens). Put the named standard in config/screening_policy.toml, not in plugin code.
2. Doubtful is NOT HALAL. Engine stays binary HALAL / NOT_HALAL. If a fact is doubtful, the vote is fail-closed → NOT_HALAL. Do not add a DOUBTFUL user-facing verdict.
3. LLM job: fill missing data, or fill something we are not sure of. Never output HALAL / NOT_HALAL. Plugins + policy still fuse the verdict. Issue #7 lock remains: no LLM as Shariah judge.
4. Do not keep MiniMax fixed. Do not keep the LLM job fixed to screenshots. Research the NVIDIA NIM catalog (many models, VLM and text). Bake-off per job; winner + 429 fallback live in policy like today’s image bake-off.
5. Research whether video gaming (Xbox / Game Studios / Activision-class revenue) is actually haram under DJIM/S&P/AAOIFI/major boards. Do not hardcode a ticker exception for MSFT.
6. No ticker allowlists (no special-case SPUS/HLAL/AAPL/MSFT in plugin code).
7. Free sources first (SEC EDGAR/companyfacts, yfinance, N-PORT, HalalWallet CC-BY JSON). Paid search (Exa, etc.) only if research shows a hole free filings cannot fill — flag cost/license; do not add a paid API in this session.
8. Telegram stays on old Musaffa/Zoya resolver until a later sign-off. Do not wire VerdictEngine into src/bot.py in the follow-up implement until the human says so.

## What “review the current implementation” must cover

Write a blunt review: what works, what is too minimal, what will fight the new decisions. Use the live comparison evidence below. Then research. Then a PR-shaped plan for the implement session (files, policy keys, tests from disagreements — no extra features).

Stop for human sign-off. Do not implement.
```

---

## Review of the current implementation (2026-08-22)

Eval-only engine on `fix/7-source-reliability`. Latest commit `c21dcff` (fail-closed N-PORT + cache-only OpenFIGI). Untracked: `scripts/compare_mz_bar.py`. Production Telegram (`src/bot.py` → `src/screener.py`) still scrapes Musaffa + Zoya and `resolve_compliance` (most restrictive wins). `src/image_parser.py` is still Gemini.

### Architecture (eval path)

```
scripts/eval_screener.py
  → VerdictEngine.screen(ticker)
      → YFinanceMarketData (+ optional SEC companyfacts fill)
      → plugins: Activity, Ratios, HalalWallet, NportHoldings
      → fusion.conjunction (any FAIL → NOT_HALAL;
         required must all PASS and none ABSTAIN)
```

Policy file: `config/screening_policy.toml` via `src/policy.py`. Secrets only via `src/config.py` / `.env`.

Fusion today:

- EQUITY required: Activity + Ratios; **HalalWallet is also required when the ticker is in that dataset** (`add_required_when_in_dataset`).
- ETF/MUTUALFUND required: NportHoldings only. Activity/Ratios ABSTAIN on the wrapper.
- N-PORT: coverage ≥ 0.95 of non-skip weight; **any identified holding whose child screen ≠ HALAL → ETF FAIL**. Skip asset category `STIV` (cash/T-bills). CUSIP→ticker is local cache only (`scripts/cache_openfigi.py`), not live OpenFIGI on the hot path.
- Votes: PASS / FAIL / ABSTAIN only. ABSTAIN on a required plugin → NOT_HALAL.

### Categories today (too thin)

**Activity** (`src/plugins/activity.py`): yfinance **sector** and **industry** strings vs two denylist arrays. No 10-K business description, no segment revenue, no SIC/GICS code, no “percent of sales from X.”

Denied **sectors**: only `Financial Services`.

Denied **industries** (current list): Aerospace & Defense; Asset Management; Banks Diversified/Regional; Beverages Brewers / Wineries & Distilleries; Capital Markets; Credit Services; Financial Conglomerates; Gambling; Insurance *; Mortgage Finance; Resorts & Casinos; Tobacco.

**Missing vs typical Shariah activity screens** (research must confirm / expand in policy, not in plugin literals):

- Pork / non-halal food production and restaurants
- Alcohol **retail** and hotels (we only deny brewers/distillers)
- Adult entertainment, pornography, dating
- Music / cinema / TV production (AAOIFI boards differ)
- Weapons vs dual-use aerospace (we blanket-deny Aerospace & Defense)
- Conventional insurance already listed; takaful not distinguished
- Media / internet content / advertising (GOOGL-class)
- Video games / interactive entertainment (MSFT Xbox / Activision)
- Cannabis
- Gold/silver trading, hog-tied commodities, etc. as relevant

Entire **Financial Services** sector is blunt: it fails PYPL (agreed with M/Z on the last run) but would also fail Shariah-friendly fintech if we ever see one. DJIM uses industry classification + revenue mix, not one GICS sector axe.

**Ratios** (`src/plugins/ratios.py` + `src/market_data.py`):

| Policy key | Value now | Notes |
|---|---|---|
| debt_to_market_cap_pct | 30 | AAOIFI; human wants **DJIM/S&P 33%** |
| cash_to_market_cap_pct | 30 | same |
| impermissible_income_to_revenue_pct | 5 | interest_income / revenue only |
| margin_pct | 0 | 30.05% debt fails |
| Denominator | **spot** market cap | DJIM uses **24-mo average**; S&P often **36-mo** |
| Receivables | **not implemented** | DJIM 33% of avg cap; classic S&P 49% or dropped in 2023 revision — research |
| Interest income | yfinance row names, then companyfacts tags | Missing → **ABSTAIN → NOT_HALAL** even if the 10-K loaded and the line is $0 |
| Impure income | **interest only** | Does not add haram **segment** revenue (gaming, alcohol sales, …) |

`Fundamentals` has no accounts_receivable, no segment map, no trailing avg cap, no 10-K excerpt.

**HalalWallet**: CC-BY JSON. PASS if verdict in `pass_verdicts` (`halal`) and activity+financial flags not false. FAIL if those flags are false or verdict in `fail_verdicts`. **`conditional` → ABSTAIN**. Because fusion **requires** HalalWallet when the ticker is in the dataset, `conditional` becomes **NOT_HALAL**. That is a third-party “maybe” veto. Human does not want maybes: find the data instead.

**N-PORT**: correct source (SEC, free). Aggregation is too brittle: one holding with missing interest-income or HalalWallet `conditional` fails SPUS/HLAL despite ~97% coverage and a HalalWallet chip PASS on the wrapper. Junk ids (`EA*`, `HONGBP`, `DWDPEUR`, `LEN/B`, `TRI4EUR`, `EXMOC`) are treated as failed holdings / uncovered, not as “resolve this identifier.”

**NVIDIA** (`src/nvidia_nim.py`, ADR 0010): **screenshot ticker OCR/VLM only**. Live bake-off on one `image.png` picked `minimaxai/minimax-m3` with `nvidia/nemotron-nano-12b-v2-vl` 429 fallback. Policy `bakeoff_models` now lists **only those two** — the original catalog also had `nemotron-ocr-v2`, `nemotron-3-nano-omni-30b-a3b-reasoning`, `llama-3.2-11b-vision-instruct`. Production parser is still Gemini. **No text-extraction path, no 10-K path, no identifier-resolution path.** Human: do not keep MiniMax or the job fixed.

**Engine vs Telegram:** two stacks. Eval = plugins. Chat = HTML scrapers. SEO/JSON-LD false HALAL on banks was the original bug; last live scrape JPM/BAC/MO **agreed** NOT_HALAL with the engine.

### Live Musaffa/Zoya comparison (test bar only)

Fixtures + 50 extra failed holdings. Scrapers live. Engine = current policy (30% AAOIFI, HalalWallet required-when-in-dataset, any-holding ETF fail).

**Agreed HALAL:** AAPL, NVDA, TSLA, XOM.

**Agreed NOT_HALAL:** JPM, BAC, MO, SPY, QQQ, PYPL (extra).

**Engine HALAL, bar stricter (false HALAL vs test bar):**

- MSFT: Activity/Ratios/HW all PASS (int ~0.99%). Musaffa NOT_HALAL, Zoya DOUBTFUL → bar NOT_HALAL. Likely **gaming segment**, not debt.
- GOOGL: all PASS (int ~1.08%). Both DOUBTFUL. Likely **interest threshold school (1% vs 5%)** and/or **ads/YouTube**, not debt.

**Engine NOT_HALAL, bar HALAL (false NOT_HALAL vs test bar):**

- SPUS, HLAL: Musaffa ETF chip HALAL; Zoya NOT_COVERED; engine look-through fails 29–40 holdings.
- CDW, TSCO: debt 37% / 36% vs 30% cap (33% still fails CDW; TSCO is the 33% edge).
- CSX, ECL, ITW, KO: Activity+Ratios PASS; HW `conditional` required → NOT_HALAL.
- DHI, LULU: Ratios ABSTAIN **missing interest-income** (fail-closed on a Yahoo/tag hole).
- AMZN, WMT: HW `conditional`; bar DOUBTFUL (not HALAL).

Human mapping going forward: **doubtful → NOT_HALAL** (so GOOGL/AMZN/WMT matching the test bar is “engine should fail if we cannot clear the doubt with data”). MSFT needs a **real** activity/segment reason if gaming is haram, not a fake ratio fail.

### What is working (keep)

- Conjunction fusion; empty required set must not vacuously HALAL.
- Policy-driven thresholds/denylists; no ticker allowlists in plugins.
- N-PORT as ETF holdings source; incomplete coverage → NOT_HALAL.
- Companyfacts fill when yfinance interest is None (tags still too few).
- OpenFIGI off the hot path; STIV skip; series-id required.
- Image bake-off harness pattern (winner + 429 fallback in policy, tests mock NIM).
- Fail-closed tests in `tests/test_verdict_engine.py` (missing interest, empty required, N-PORT).

### What is not how we want it

1. **30% AAOIFI vs decided DJIM/S&P 33%.**
2. **Spot market cap** vs trailing average.
3. **No receivables screen.**
4. **Interest missing ≠ interest zero** — we refuse to pass when we did not find a line; human wants the model (or better SEC tags) to **fill** that.
5. **HalalWallet `conditional` required** — a maybe veto. Should not block; we must fetch our own facts. HW explicit `not_halal` can still FAIL.
6. **Activity is GICS-string only** — too minimal for gaming, ads, pork, alcohol retail, adult, music.
7. **Impure income is interest-only** — no segment 5% mix.
8. **ETF any-child-FAIL** — data holes and maybes sink Islamic ETFs. Want: fail the fund if it **is actually haram** (haram activity inside, or ratio-fail weight over a cap), not if LULU’s interest row is blank.
9. **NVIDIA job is screenshot-only; catalog shrunk to MiniMax + one VLM.**
10. **Binary engine has no “unsure fact” pipeline** — ABSTAIN is a dead end instead of a fetch.

---

## Research questions (must answer in the session)

### A. Standard: “DJIM / S&P 33%”

Do not treat DJIM and S&P as identical.

- DJIM: debt / 24-mo avg cap < 33%; historically cash+interest securities 33%; AR 33%.
- S&P Shariah: debt / 36-mo avg cap < 33%; cash/AR often 49%; **Sep 2023** some S&P/DJIM docs dropped cash and AR screens entirely — verify against current S&P methodology PDFs, not blogs.
- SPUS tracks **S&P 500 Shariah Industry Exclusions** — which board and which ratio set? (Ratings Intelligence vs S&P Shariah board.)
- Recommend **one named standard** in policy (`standard = "djim"` or `"sp_shariah"`) plus numeric keys. If DJIM and S&P disagree on cash/AR, pick one and say why.
- Denominator: how to get 24-mo or 36-mo average market cap from **free** sources (yfinance history vs SEC). If we cannot get trailing avg, document the fallback (spot cap) and that it is a known gap.

### B. Gaming — is it haram?

Primary motivation: MSFT. No ticker special-case.

Research AAOIFI SS 21 / SS 59, DJIM industry exclusions, S&P Shariah industry exclusions, Ratings Intelligence, major boards (e.g. whether **video game production** is “media/entertainment” like film, or only **gambling mechanics** / **haram content** count).

Outcomes to recommend (policy, not code literals):

- Deny all interactive entertainment revenue (then MSFT gaming ~8% likely fails a 5% impure cap if we add segments).
- Deny only gambling-like games / loot boxes / haram content share.
- Allow games; purify interest only.

Cite sources. If scholars split, human said **doubtful → NOT_HALAL**, so a split with no data → fail; a split with a clear DJIM/S&P index rule → follow **that named standard**, not Musaffa’s chip.

### C. Activity taxonomy

Propose a policy denylist that is complete enough for DJIM/S&P **and** still data-driven (yfinance industry + 10-K segments + optional LLM fill). Include pork, alcohol retail, adult, weapons granularity, insurance, music/film, ads, games (per B), cannabis. Map to yfinance strings **and** GICS if possible. Flag what yfinance cannot see (Microsoft “Software - Infrastructure” hides Xbox).

### D. Missing vs zero vs doubtful vs found

Define states for each ratio input and each activity fact:

| State | Meaning | Vote (given doubtful→NOT_HALAL) |
|---|---|---|
| found | number or classification in hand | PASS/FAIL vs threshold |
| zero | filing present, line absent or $0 | treat as 0 for non-financials |
| missing | we did not look / fetch failed | do not pass; trigger **data-finder** |
| doubtful | finder ran, still unsure | NOT_HALAL |

Human wants the model to **fill missing and unsure**, not to bless them.

### E. ETF aggregation (still N-PORT)

Keep SEC N-PORT. Research a rule that fails the ETF if it is **actually haram**:

- Haram **activity** on any identified holding (or above epsilon weight).
- Ratio fails **by NAV weight** vs a cap (candidate 5%, aligned with impure-income), not “one missing Yahoo row.”
- Materiality floor for expensive ratio fetches (e.g. ≥ 0.5% NAV) while still activity-scanning all identified names.
- Junk N-PORT identifiers: resolve (FIGI cache, name match, LLM fill) or count as uncovered, **not** as “this holding is not Halal.”

No SPUS/HLAL allowlist. If the fund becomes HALAL it is because holdings cleared the new rules.

### F. NVIDIA models — catalog, not MiniMax

Research **current** NVIDIA NIM integrate.api.nvidia.com models (text + vision + OCR). Do not assume MiniMax-M3 stays winner. For **each LLM job** in section G, recommend 3–6 bake-off candidates, a rubric (exact JSON schema match, no invented numbers, citation to excerpt), winner + 429 fallback written to **policy** after a real eval script, same pattern as `scripts/eval_image_models.py`.

Constraints: `NVIDIA_API_KEY` via config.py; httpx; tests mock NIM; free NIM credits vs paid; 429 behavior already in image bake-off.

Do not use Exa as default. Prefer SEC full-text 10-K/10-Q. If a job truly needs web search, list free alternatives (SEC, company IR PDF) and only then a paid search API with license.

### G. LLM jobs — find all of them (do not freeze one job)

The human does **not** want a single “10-K interest-income extractor” as the only LLM feature. Inventory **every** place in the **current** and **planned** system where a model could fill missing data or resolve unsure facts — then rank: must / should / later / never.

Start from this list and **add more** after reading the code:

**Current stack holes**

1. Screenshot ticker extraction (exists; re-bake-off full catalog, including dropped OCR/omni/llama).
2. Screenshot **holdings tables** (broker UI with many tickers, not one `image.png`).
3. Missing **interest income / revenue / debt / cash** from 10-K notes when XBRL tags miss.
4. Missing **accounts receivable** (new ratio).
5. Missing **sector/industry** when yfinance 404s.
6. **Segment revenue** mix from Item 1 / note 18 (gaming, ads, alcohol, weapons, pork).
7. Classify a segment name against the policy denylist (“More Personal Computing” → contains Gaming).
8. N-PORT **security name → ticker** when CUSIP cache misses.
9. Junk identifiers (`EA*`, `HONGBP`, `DWDPEUR`, `LEN/B`) → canonical listing ticker or “uncovered.”
10. Quote type wrong or UNKNOWN (equity vs ETF vs UIT).
11. Dual-class / foreign listings (GOOGL vs GOOG, LEN vs LEN.B).
12. 10-K **business description** vs activity denylist when GICS is “Software.”
13. Trailing 24/36-month average shares × price if yfinance history is thin (probably **not** LLM — prefer price history; say so).
14. HalalWallet `conditional`: do **not** ask the LLM “is this halal?”; ask it to **fetch the fact** HW was unsure about (which ratio or activity).
15. Log-quality **explanations** of votes (not shown in Telegram; not a verdict).

**Future Issue #7 slices (do not implement now; still inventory)**

16. Replace Gemini `ImageParser` (slice 7) — same bake-off, maybe different winner per image type.
17. User-pasted **PDF broker statement**.
18. ETF **prospectus** “Shariah screened” claims — extract methodology name only; **never** HALAL from marketing.
19. Purification **percentage** for logs/DB (chat stays Halal / Not Halal later).
20. Conflict clustering: many holdings fail for the same missing tag — one filing fetch should fill all.

**Never (LLM must not)**

- Output HALAL / NOT_HALAL / fatwa.
- Invent a number not present in the provided excerpt.
- Override a FAIL from Activity denylist (bank, tobacco, alcohol, weapons, gambling) to PASS.
- Use Musaffa/Zoya HTML as the source of truth.

For each must/should job: input, output JSON schema, which plugin consumes it, fail-closed behavior if the model is unsure, bake-off models.

### H. HalalWallet after “no maybes”

Recommend: required or not; FAIL-only on explicit not_halal; ignore `conditional`; or drop HW from required set entirely once our Activity+Ratios+segments are complete. HW stays a free CC-BY signal, not a judge.

---

## Out of scope this research session

- Implementing the plugins.
- Wiring Telegram / dropping Gemini / deleting Docker.
- Vercel, Oracle deploy, paid Musaffa/Zoya/Finnhub/Halal Terminal as primary.
- LLM as Shariah judge.
- Showing ratios in chat.
- Changing N-PORT as the holdings **source** (Yahoo-as-holdings still forbidden).
- Ticker allowlists.

---

## Deliverable

One design note (update this file or add `docs/planning/YYYY-MM-DD-engine-completeness-design.md`) plus ADRs only if a locked decision is **replaced** (0008 fusion still conjunction; 0009 N-PORT still the ETF source; 0010 bake-off pattern reused for **each** new NIM job).

Must include:

1. Review verdict: keep / change / drop for each current plugin behavior.
2. Named standard + exact policy keys (33%, trailing window, cash, AR).
3. Gaming recommendation with citations.
4. Activity denylist draft (policy TOML sketch).
5. Missing/zero/doubtful state machine.
6. ETF aggregation rule.
7. Full LLM-job inventory with rank and bake-off catalogs (**not** MiniMax-only).
8. PR-shaped implement plan for the **next** session (smallest slices, tests from real disagreements, no Telegram).
9. Open questions that still need the human.

**Stop.** Do not write production code. Do not open a PR.
