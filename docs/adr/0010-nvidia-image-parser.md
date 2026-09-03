# ADR 0010: NVIDIA NIM image ticker bake-off (supersedes ADR 0005 for new work)

Date: 2026-08-22
Status: accepted (eval bake-off; production `ImageParser` still Gemini until Issue #7 slice 7)

## Context

Gemini daily model rotation (ADR 0005) includes IDs that can shut down and is not the Issue #7 image path. Screenshots must move to NVIDIA NIM after a bake-off of OCR and VLMs. Do not hardcode MiniMax as winner. Prefer `httpx` to NIM. `NVIDIA_API_KEY` via `src/config.py` only.

## Options

1. Keep Gemini (`google-genai`) in production forever — rejected for new work (Issue: drop Gemini after bake-off).
2. Hardcode MiniMax-M3 as the screenshot model — rejected (Issue: bake-off must pick winner + 429 fallback).
3. `scripts/eval_image_models.py` over policy-listed NIM models (`nemotron-ocr-v2`, `minimax-m3`, `nemotron-nano-12b-v2-vl`, `nemotron-3-nano-omni-30b-a3b-reasoning`, `llama-3.2-11b-vision-instruct`); write winner + 429 fallback into policy only after a real run — chosen.

## Decision

We pick **option 3**. Live bake-off on `image.png` (2026-08-22) set `nvidia.winner = minimaxai/minimax-m3` and `nvidia.fallback_429 = nvidia/nemotron-nano-12b-v2-vl`. Catalog in policy is those two VLMs only. Production `src/image_parser.py` is still Gemini until slice 7.

## Consequences

- Good: model IDs live in policy; tests mock NIM (no live NVIDIA in pytest).
- Cost / follow-up: slice 7 wires these IDs into `ImageParser` and removes `google-genai`.
- Hardware / recovery: N/A.
