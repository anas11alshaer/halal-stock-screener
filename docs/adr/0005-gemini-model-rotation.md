# ADR 0005: Gemini quota — multi-key round-robin → daily model rotation

Date: 2026-03-15
Status: superseded by ADR 0010 (production ImageParser still Gemini until Issue #7 slice 7)

## Context

`gemini-2.5-flash-lite` free tier 10 RPM / 500 RPD blows on portfolio screenshots. Need higher effective quota without paying. `src/image_parser.py:112`, `src/config.py:40`.

## Options

1. Single key + one model (`gemini-2.5-flash`) — initial, exhausted quickly.
2. Multi-key rotation `GEMINI_API_KEYS` comma-separated, per-key cooldown 300s (`5928c31`) — key management overhead.
3. Multi-model + multi-key `_ModelSlot` matrix tracking RPM/RPD (`47223f4`) — complex wait logic.
4. Single key, 4-model daily round-robin counter resetting at midnight, per-model `exhausted` set (`850c457`) — chosen.

## Decision

We pick **option 4**. Final state: `GEMINI_API_KEY` single, `GEMINI_MODELS=[gemini-3.1-flash-lite-preview, gemini-3-flash-preview, gemini-2.5-flash, gemini-2.5-flash-lite]` (`src/config.py:40`); `ImageParser` picks `_get_next_model()` round-robin per request, `exhausted_models` on `429/quota`, raises `QuotaExceededError` when all 4 exhausted (`src/image_parser.py:277`).

## Consequences

- Good: ops simpler, daily reset predictable, no multi-key secrets.
- Cost / follow-up: total daily capacity capped by 4 models’ RPD (~1050 req/day theoretical) vs unlimited keys before; still no per-minute throttling after simplification.
- Hardware / recovery: N/A.
