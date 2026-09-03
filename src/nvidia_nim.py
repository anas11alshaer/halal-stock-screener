"""NVIDIA NIM client for eval bake-off (VLM/OCR + JSON chat)."""

from __future__ import annotations

import base64
import json
import logging
import mimetypes
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from image_parser import parse_text_for_tickers
from policy import Policy

# Eval screenshots include exchange suffixes (ALV.DE) and digit-leading symbols (9OC.SG).
# Production Gemini parser stays on the stricter image_parser.is_valid_ticker.
_EVAL_TICKER = re.compile(
    r"^(?:[A-Z]{1,5}(?:\.[A-Z]{1,3})?|[A-Z0-9]{2,5}\.[A-Z]{1,3})$"
)

logger = logging.getLogger(__name__)

SCREENSHOT_JOB = "screenshot_tickers"
_TABLE_HEADER_LINE = re.compile(r"(?m)^[ \t]*\[")


class NimError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def image_data_url(image_bytes: bytes, path: Path | None = None) -> str:
    mime = "image/png"
    if path is not None:
        guessed, _ = mimetypes.guess_type(str(path))
        if guessed:
            mime = guessed
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{b64}"


def is_eval_ticker(symbol: str) -> bool:
    return bool(symbol) and _EVAL_TICKER.match(symbol) is not None


def extract_tickers_from_text(text: str) -> list[str]:
    """Parse a VLM/OCR string into tickers. Prefers a JSON tickers list."""
    blob = _json_blob(text)
    if blob is not None:
        names: list[str] = []
        if isinstance(blob, dict) and isinstance(blob.get("tickers"), list):
            names = [str(t) for t in blob["tickers"]]
        elif isinstance(blob, list):
            names = [str(t) for t in blob]
        tickers = []
        seen: set[str] = set()
        for raw in names:
            symbol = raw.upper().strip()
            if symbol not in seen and is_eval_ticker(symbol):
                seen.add(symbol)
                tickers.append(symbol)
        if tickers:
            return tickers
    return parse_text_for_tickers(text)


def _json_blob(text: str) -> Any | None:
    stripped = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", stripped, re.S)
    if fenced:
        stripped = fenced.group(1)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", stripped, re.S)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None


def parse_ocr_payload(payload: dict[str, Any]) -> str:
    texts: list[str] = []
    data = payload.get("data") or payload.get("output") or []
    if isinstance(data, str):
        return data
    if isinstance(payload.get("text"), str):
        texts.append(payload["text"])
    if isinstance(data, dict):
        data = [data]
    for item in data:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("text"), str):
            texts.append(item["text"])
        for det in item.get("text_detections") or []:
            pred = det.get("text_prediction") if isinstance(det, dict) else None
            if isinstance(pred, dict) and pred.get("text"):
                texts.append(str(pred["text"]))
            elif isinstance(det, dict) and det.get("text"):
                texts.append(str(det["text"]))
    return "\n".join(texts)


class NvidiaNimClient:
    """httpx client for integrate.api.nvidia.com chat + OCR endpoints."""

    def __init__(
        self,
        api_key: str,
        policy: Policy,
        http: httpx.AsyncClient,
    ) -> None:
        self._api_key = api_key
        self._policy = policy
        self._http = http

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def extract_tickers(
        self,
        *,
        model_id: str,
        kind: str,
        image_bytes: bytes,
        image_path: Path | None = None,
        url: str | None = None,
    ) -> list[str]:
        if kind == "ocr":
            text = await self.ocr(
                model_id=model_id,
                image_bytes=image_bytes,
                image_path=image_path,
                url=url,
            )
        else:
            prompt = str(self._policy.get("nvidia", "ticker_prompt") or "")
            text = await self.chat_vlm(
                model_id=model_id,
                image_bytes=image_bytes,
                image_path=image_path,
                prompt=prompt,
                url=url,
            )
        return extract_tickers_from_text(text)

    async def chat_vlm(
        self,
        *,
        model_id: str,
        image_bytes: bytes,
        prompt: str,
        image_path: Path | None = None,
        url: str | None = None,
    ) -> str:
        endpoint = url or str(self._policy.get("nvidia", "chat_url"))
        payload = {
            "model": model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_data_url(image_bytes, image_path)
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 512,
            "temperature": 0,
            "stream": False,
        }
        response = await self._http.post(
            endpoint, headers=self._auth_headers(), json=payload
        )
        if response.status_code == 429:
            raise NimError(429, f"{model_id} rate limited")
        if response.status_code >= 400:
            raise NimError(response.status_code, response.text[:500])
        body = response.json()
        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise NimError(
                response.status_code, f"unexpected VLM payload: {exc}"
            ) from exc

    async def chat_text(
        self,
        *,
        model_id: str,
        prompt: str,
        max_tokens: int = 2048,
        url: str | None = None,
    ) -> str:
        """JSON chat completions. Default max_tokens=2048 (VLM 512 is too small for segments)."""
        endpoint = url or str(self._policy.get("nvidia", "chat_url"))
        payload = {
            "model": model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        response = await self._http.post(
            endpoint, headers=self._auth_headers(), json=payload
        )
        if response.status_code == 429:
            raise NimError(429, f"{model_id} rate limited")
        if response.status_code >= 400:
            raise NimError(response.status_code, response.text[:500])
        body = response.json()
        try:
            return str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise NimError(
                response.status_code, f"unexpected chat payload: {exc}"
            ) from exc

    async def ocr(
        self,
        *,
        model_id: str,
        image_bytes: bytes,
        image_path: Path | None = None,
        url: str | None = None,
    ) -> str:
        endpoint = url or str(self._policy.get("nvidia", "ocr_url"))
        payload = {
            "model": model_id,
            "input": [
                {
                    "type": "image_url",
                    "url": image_data_url(image_bytes, image_path),
                }
            ],
        }
        response = await self._http.post(
            endpoint, headers=self._auth_headers(), json=payload
        )
        if response.status_code == 429:
            raise NimError(429, f"{model_id} rate limited")
        if response.status_code >= 400:
            raise NimError(response.status_code, response.text[:500])
        body = response.json()
        if isinstance(body, dict):
            return parse_ocr_payload(body)
        return str(body)


@dataclass
class ModelScore:
    model_id: str
    kind: str
    mean_jaccard: float = 0.0
    exact_matches: int = 0
    fixtures: int = 0
    errors: int = 0
    rate_limited: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class BakeoffResult:
    scores: list[ModelScore]
    winner: str = ""
    fallback_429: str = ""
    wrote_policy: bool = False
    reason: str = ""


def jaccard(expected: set[str], got: set[str]) -> float:
    if not expected and not got:
        return 1.0
    union = expected | got
    if not union:
        return 0.0
    return len(expected & got) / len(union)


async def run_bakeoff(
    client: NvidiaNimClient,
    models: list[dict[str, Any]],
    fixtures: list[tuple[Path, list[str]]],
) -> BakeoffResult:
    scores: list[ModelScore] = []
    for spec in models:
        model_id = str(spec["id"])
        kind = str(spec.get("kind") or "vlm")
        url = spec.get("url")
        score = ModelScore(model_id=model_id, kind=kind)
        jaccards: list[float] = []
        for path, expected in fixtures:
            score.fixtures += 1
            try:
                image_bytes = path.read_bytes()
                got = await client.extract_tickers(
                    model_id=model_id,
                    kind=kind,
                    image_bytes=image_bytes,
                    image_path=path,
                    url=url,
                )
            except NimError as exc:
                score.errors += 1
                if exc.status_code == 429:
                    score.rate_limited = True
                    score.notes.append("429")
                else:
                    score.notes.append(f"HTTP {exc.status_code}")
                jaccards.append(0.0)
                continue
            except Exception as exc:
                score.errors += 1
                score.notes.append(str(exc)[:80])
                jaccards.append(0.0)
                continue
            expected_set = {t.upper() for t in expected}
            got_set = set(got)
            jaccards.append(jaccard(expected_set, got_set))
            if expected_set == got_set:
                score.exact_matches += 1
        if jaccards:
            score.mean_jaccard = sum(jaccards) / len(jaccards)
        scores.append(score)

    # Partial HTTP scores must not become policy defaults.
    ranked = sorted(
        [s for s in scores if s.errors == 0 and s.fixtures > 0],
        key=lambda s: (s.mean_jaccard, s.exact_matches),
        reverse=True,
    )
    winner = ranked[0].model_id if ranked else ""
    fallback_pool = [s for s in ranked[1:] if not s.rate_limited]
    fallback = fallback_pool[0].model_id if fallback_pool else ""
    return BakeoffResult(scores=scores, winner=winner, fallback_429=fallback)


def _toml_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _table_body_span(text: str, header: str) -> tuple[int, int]:
    header_re = re.compile(rf"(?m)^[ \t]*{re.escape(header)}[ \t]*(?:#[^\n]*)?\r?\n")
    match = header_re.search(text)
    if match is None:
        raise ValueError(f"Could not find table {header}")
    start = match.end()
    nxt = _TABLE_HEADER_LINE.search(text, start)
    end = nxt.start() if nxt else len(text)
    return start, end


def _patch_table_winner(text: str, header: str, winner: str, fallback: str) -> str:
    start, end = _table_body_span(text, header)
    body = text[start:end]
    body, n1 = re.subn(
        r'(?m)^(winner\s*=\s*)".*"',
        rf'\1"{_toml_escape(winner)}"',
        body,
        count=1,
    )
    body, n2 = re.subn(
        r'(?m)^(fallback_429\s*=\s*)".*"',
        rf'\1"{_toml_escape(fallback)}"',
        body,
        count=1,
    )
    if n1 != 1 or n2 != 1:
        raise ValueError(f"Could not patch nvidia winner fields in {header}")
    return text[:start] + body + text[end:]


def write_job_winner(policy_path: Path, job: str, winner: str, fallback: str) -> None:
    """Patch winner/fallback_429 only inside [nvidia.jobs.<job>]; fail if missing.

    screenshot_tickers also updates top-level [nvidia] as the screenshot alias.
    """
    if not re.fullmatch(r"[A-Za-z0-9_]+", job):
        raise ValueError(f"invalid nvidia job name {job!r}")
    text = policy_path.read_text(encoding="utf-8")
    text = _patch_table_winner(text, f"[nvidia.jobs.{job}]", winner, fallback)
    if job == SCREENSHOT_JOB:
        text = _patch_table_winner(text, "[nvidia]", winner, fallback)
    policy_path.write_text(text, encoding="utf-8")


def write_nvidia_winner(policy_path: Path, winner: str, fallback: str) -> None:
    """Patch only [nvidia.jobs.screenshot_tickers]; screenshot_tickers also patches [nvidia]."""
    write_job_winner(policy_path, SCREENSHOT_JOB, winner, fallback)


def format_bakeoff_table(result: BakeoffResult) -> str:
    headers = ["MODEL", "KIND", "JACCARD", "EXACT", "ERRORS", "429"]
    rows = [
        [
            s.model_id,
            s.kind,
            f"{s.mean_jaccard:.3f}",
            f"{s.exact_matches}/{s.fixtures}",
            str(s.errors),
            "yes" if s.rate_limited else "no",
        ]
        for s in result.scores
    ]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row: list[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    lines = [fmt(headers), "  ".join("-" * w for w in widths)]
    lines.extend(fmt(row) for row in rows)
    winner = result.winner or "(none — bake-off did not pick a model)"
    fallback = result.fallback_429 or "(none)"
    lines.append("")
    lines.append(f"winner: {winner}")
    lines.append(f"fallback_429: {fallback}")
    return "\n".join(lines)
