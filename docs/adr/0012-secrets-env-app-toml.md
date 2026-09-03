# ADR 0012: Secrets in .env, non-secrets in app.toml

Date: 2026-08-22
Status: accepted

## Context

Issue #7 needs operator-specific values (API keys, SEC contact email) and committed knobs (timeouts, scraper URLs, Gemini model IDs, Shariah thresholds). Putting an email or key in `config/screening_policy.toml` either commits a personal address or blocks EDGAR with a placeholder. `src/config.py` was a mix of `os.getenv` defaults and Python literals.

## Options

1. Keep everything in `src/config.py` + `.env` — secrets stay out of git, but timeouts and model IDs are code.
2. One TOML for all settings, including secrets — rejected (keys and email would be committed or the file gitignored and then policy is unreviewable).
3. Three layers, one reader: `.env` secrets, `config/app.toml` non-secret app settings, `config/screening_policy.toml` Shariah signals; `src/config.py` is the only env reader — chosen.

## Decision

We pick **option 3**. Declared secrets: `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `NVIDIA_API_KEY`, `SEC_CONTACT_EMAIL`. `load_app_toml` refuses those key names if they appear in TOML. `bot.py` still reads `PORT` from the environment (host platform). Screening thresholds stay in `screening_policy.toml` (ADR 0008).

## Consequences

- Good: no contact email or API key in git; operators edit `.env`; reviewers see knobs in TOML.
- Cost / follow-up: `CACHE_TTL_HOURS` / `LOG_LEVEL` default in `config/app.toml` and may still be overridden from `.env`.
- Hardware / recovery: N/A.
