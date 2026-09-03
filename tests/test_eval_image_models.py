"""Image bake-off tests: mocked NIM only, no live NVIDIA calls."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nvidia_nim import (
    NimError,
    NvidiaNimClient,
    extract_tickers_from_text,
    run_bakeoff,
    write_nvidia_winner,
)
from policy import load_policy

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "config" / "screening_policy.toml"

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _load_cli():
    path = ROOT / "scripts" / "eval_image_models.py"
    spec = importlib.util.spec_from_file_location("eval_image_models_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _success_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "ocr" in url or "/cv/" in url:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "text_detections": [
                            {"text_prediction": {"text": "AAPL"}},
                            {"text_prediction": {"text": "MSFT"}},
                        ]
                    }
                ]
            },
        )
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": '{"tickers": ["AAPL", "MSFT"]}'}}]},
    )


def test_extract_tickers_from_json_payload() -> None:
    assert extract_tickers_from_text('{"tickers": ["AAPL", "MSFT"]}') == [
        "AAPL",
        "MSFT",
    ]


def test_extract_tickers_allows_exchange_suffix_and_digit_prefix() -> None:
    assert extract_tickers_from_text(
        '{"tickers": ["AMD", "SPCX", "TSLA", "9OC.SG", "ALV.DE"]}'
    ) == ["AMD", "SPCX", "TSLA", "9OC.SG", "ALV.DE"]


def test_cli_missing_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    policy_copy = tmp_path / "policy.toml"
    policy_copy.write_text(POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    cli = _load_cli()
    monkeypatch.setattr(cli, "NVIDIA_API_KEY", "")
    code = cli.main(["--policy", str(policy_copy), "--screenshots", str(tmp_path)])
    assert code == 2
    nvidia = load_policy(policy_copy).section("nvidia")
    committed = load_policy(POLICY_PATH).section("nvidia")
    assert nvidia.get("winner") == committed.get("winner")
    assert nvidia.get("fallback_429") == committed.get("fallback_429")


def test_cli_missing_screenshots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_copy = tmp_path / "policy.toml"
    policy_copy.write_text(POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    cli = _load_cli()
    monkeypatch.setattr(cli, "NVIDIA_API_KEY", "test-key")
    code = cli.main(["--policy", str(policy_copy), "--screenshots", str(tmp_path)])
    assert code == 2
    nvidia = load_policy(policy_copy).section("nvidia")
    committed = load_policy(POLICY_PATH).section("nvidia")
    assert nvidia.get("winner") == committed.get("winner")
    assert nvidia.get("fallback_429") == committed.get("fallback_429")


@pytest.mark.asyncio
async def test_success_parse_with_mocked_nim(tmp_path: Path) -> None:
    shot = tmp_path / "broker.png"
    shot.write_bytes(_PNG)
    policy = load_policy(POLICY_PATH)
    models = list(policy.section("nvidia").get("bakeoff_models") or [])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_success_handler)
    ) as http:
        client = NvidiaNimClient("test-key", policy, http)
        result = await run_bakeoff(client, models, [(shot, ["AAPL", "MSFT"])])
    assert result.winner in {str(m["id"]) for m in models}
    assert models
    assert all(s.exact_matches == 1 and s.errors == 0 for s in result.scores)


def test_cli_success_no_write_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shot = tmp_path / "broker.png"
    shot.write_bytes(_PNG)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"broker.png": ["AAPL", "MSFT"]}), encoding="utf-8"
    )
    policy_copy = tmp_path / "policy.toml"
    policy_copy.write_text(POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    cli = _load_cli()
    monkeypatch.setattr(cli, "NVIDIA_API_KEY", "test-key")
    real_async_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        return real_async_client(
            transport=httpx.MockTransport(_success_handler), follow_redirects=True
        )

    monkeypatch.setattr(cli.httpx, "AsyncClient", factory)
    code = cli.main(
        [
            "--policy",
            str(policy_copy),
            "--screenshots",
            str(tmp_path),
            "--no-write-policy",
        ]
    )
    assert code == 0
    nvidia = load_policy(policy_copy).section("nvidia")
    committed = load_policy(POLICY_PATH).section("nvidia")
    assert nvidia.get("winner") == committed.get("winner")
    assert nvidia.get("fallback_429") == committed.get("fallback_429")


@pytest.mark.asyncio
async def test_429_is_recorded_not_winner(tmp_path: Path) -> None:
    shot = tmp_path / "broker.png"
    shot.write_bytes(_PNG)
    policy = load_policy(POLICY_PATH)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("model") == "minimaxai/minimax-m3":
            return httpx.Response(429, text="rate limited")
        if "ocr" in str(request.url) or "/cv/" in str(request.url):
            return httpx.Response(200, json={"data": [{"text": "NVDA"}]})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"tickers": ["NVDA"]}'}}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = NvidiaNimClient("test-key", policy, http)
        result = await run_bakeoff(
            client,
            list(policy.section("nvidia").get("bakeoff_models") or []),
            [(shot, ["NVDA"])],
        )
    minimax = next(s for s in result.scores if s.model_id == "minimaxai/minimax-m3")
    assert minimax.rate_limited is True
    assert minimax.errors == 1
    assert result.winner
    assert result.winner != "minimaxai/minimax-m3"


@pytest.mark.asyncio
async def test_partial_errors_do_not_win_bakeoff(tmp_path: Path) -> None:
    fixtures = []
    for i in range(5):
        path = tmp_path / f"shot{i}.png"
        path.write_bytes(_PNG)
        fixtures.append((path, ["AAPL"]))

    class FakeClient:
        def __init__(self) -> None:
            self.calls: dict[str, int] = {}

        async def extract_tickers(self, *, model_id: str, **kwargs):
            n = self.calls.get(model_id, 0)
            self.calls[model_id] = n + 1
            if model_id == "flaky":
                if n < 4:
                    raise NimError(500, "boom")
                return ["AAPL"]
            return ["AAPL"]

    result = await run_bakeoff(
        FakeClient(),  # type: ignore[arg-type]
        [{"id": "flaky", "kind": "vlm"}, {"id": "steady", "kind": "vlm"}],
        fixtures,
    )
    flaky = next(s for s in result.scores if s.model_id == "flaky")
    assert flaky.errors == 4
    assert flaky.mean_jaccard == pytest.approx(0.2)
    assert result.winner == "steady"


def test_write_nvidia_winner_patches_toml_only(tmp_path: Path) -> None:
    policy_copy = tmp_path / "policy.toml"
    policy_copy.write_text(POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    write_nvidia_winner(policy_copy, "nvidia/demo-winner", "nvidia/demo-fallback")
    patched = load_policy(policy_copy).section("nvidia")
    assert patched["winner"] == "nvidia/demo-winner"
    assert patched["fallback_429"] == "nvidia/demo-fallback"
    committed = load_policy(POLICY_PATH).section("nvidia")
    assert committed["winner"] == "minimaxai/minimax-m3"
    assert committed["fallback_429"] == "nvidia/nemotron-nano-12b-v2-vl"


def test_eval_image_script_uses_policy_timeout_not_scraper_timeout() -> None:
    src = (ROOT / "scripts" / "eval_image_models.py").read_text(encoding="utf-8")
    assert "timeout_seconds" in src
    assert "REQUEST_TIMEOUT" not in src
