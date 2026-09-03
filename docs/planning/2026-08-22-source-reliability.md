# Source reliability — policy-driven free screens + NVIDIA images (Oracle)

**Status:** `ready to implement` — planned 2026-08-22. **Do not start the Vercel/plugin extraction** (`2026-08-21-vercel-modular-future.md`) until this Issue is closed.

**Issue:** [#7](https://github.com/anas11alshaer/halal-stock-screener/issues/7)

**Branch (next session):** `fix/7-source-reliability` from `main` (already created locally; **uncommitted**). Do not commit to `main`. Do not create `develop`.

**Work type:** `bugfix` + `new-feature`. First week is **eval only** (harness + fixture table + NVIDIA bake-off). Do not wire Telegram or delete Docker until the human has signed off the eval table.

**This file is the handoff.** Full decision log is the GitHub Issue. Session plan: grok session `plan.md` (approved).

---

## Next session — start here

1. Read this file + [Issue #7](https://github.com/anas11alshaer/halal-stock-screener/issues/7) (acceptance is the spec).
2. Continue on local `fix/7-source-reliability` (or `git switch main && git pull` then `git switch -c fix/7-source-reliability` if starting clean). **Do not commit this handoff to `main`.**
3. Implement **only slices 2–4** (policy file, `scripts/eval_screener.py`, `scripts/eval_image_models.py`) until the human reviews the stock/ETF table and the image bake-off winner.
4. Do **not** change `src/bot.py` message format, do **not** drop Gemini in production, do **not** delete `Dockerfile`/`deploy.bat` in that first PR unless the Issue is split and this slice includes it (default: **second PR** after eval sign-off).

---

## Locked decisions (do not reopen)

- Stay on **Oracle**. No Fly/Railway/Vercel in this work.
- **Docker is not required** on the VM. After eval: `scripts/` deploy (venv + systemd); stop using Docker as the run path; default is delete `Dockerfile` + `deploy.bat` (already gitignored). Keys stay in `data/oracle_cloud/` (gitignored).
- **No hardcoded signals:** thresholds, denylists, enabled plugins, fusion name, ETF coverage floors, NVIDIA model IDs live in a policy/config file. `src/config.py` is the only env reader. No ticker allowlists (including SPUS/HLAL).
- Verdict engine is **conjunction of plugins**, not an LLM. HALAL only if required plugins PASS with margin; else NOT HALAL. Telegram (later) shows **only** Halal / Not Halal; ratios stay in logs/DB.
- Images: **NVIDIA NIM only** (drop Gemini and Groq). Bake-off picks the model; do not hardcode MiniMax as winner.
- ETF: **SEC N-PORT look-through**, same stock pipeline on holdings. Incomplete holdings → NOT HALAL.
- Free sources only. No paid Musaffa/Zoya/Finnhub/Halal Terminal as primary.

---

## Slices

1. Issue (this handoff) — [#7](https://github.com/anas11alshaer/halal-stock-screener/issues/7).
2. Policy file + plugin interface.
3. `scripts/eval_screener.py` + fixture list of stocks **and** ETFs. Plugins: Activity, Ratios, HalalWallet, N-PORT as far as the harness needs.
4. `scripts/eval_image_models.py` on real screenshots; write winner (+ 429 fallback) into policy.
5. Human sign-off on the table.
6. Wire binary Telegram + optional fail-closed HTML chip plugin.
7. Replace `ImageParser` with NVIDIA-only using bake-off IDs; remove `google-genai`.
8. `scripts/deploy.ps1`, `remote-setup.sh`, systemd unit; remove Docker from the Oracle run path.

ADRs during implement (not this session): `0008-policy-driven-screener`, `0009-nport-etf-lookthrough`, `0010-nvidia-image-parser` (supersedes 0005), `0011-oracle-scripts-no-docker`.

---

## Acceptance (from human; do not add more)

- HALAL never comes from SEO/JSON-LD guessing.
- Human has signed off a fixture table before the bot uses the new verdicts.
- Chat shows only Halal / Not Halal (after wire-up, not in the eval PR).
- ETFs use holdings, not a chip and not a hardcoded list.
- Screenshots work on the NVIDIA model the bake-off picked.
- Deploy is `scripts/` to the existing Oracle box; Docker is not in that path.

## Out of scope

- Vercel, webhook, `halal_screener_core` package split, Postgres.
- LLM as Shariah judge.
- Paid Shariah APIs.
- Showing ratios in Telegram.
- Leaving Oracle.

## Hardware risk

No.
