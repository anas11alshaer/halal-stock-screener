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
    SCREENSHOT_JOB,
    NimError,
    NvidiaNimClient,
    extract_tickers_from_text,
    run_bakeoff,
    write_job_winner,
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


def _jobs(path: Path) -> dict:
    nvidia = load_policy(path).section("nvidia")
    jobs = nvidia.get("jobs") or {}
    return jobs if isinstance(jobs, dict) else {}


def _two_winner_toml(*, notes_first: bool) -> str:
    nvidia = '[nvidia]\nwinner = "top-winner"\nfallback_429 = "top-fallback"\n'
    shot = (
        "[nvidia.jobs.screenshot_tickers]\n"
        'winner = "shot-winner"\n'
        'fallback_429 = "shot-fallback"\n'
    )
    notes = (
        "[nvidia.jobs.notes]\n"
        'winner = "notes-winner"\n'
        'fallback_429 = "notes-fallback"\n'
    )
    if notes_first:
        return notes + "\n" + nvidia + "\n" + shot
    return nvidia + "\n" + shot + "\n" + notes


def test_write_nvidia_winner_patches_toml_only(tmp_path: Path) -> None:
    policy_copy = tmp_path / "policy.toml"
    text = POLICY_PATH.read_text(encoding="utf-8")
    text += (
        '\n[nvidia.jobs.notes]\nwinner = "keep-notes"\nfallback_429 = "keep-notes-fb"\n'
    )
    policy_copy.write_text(text, encoding="utf-8")
    write_nvidia_winner(policy_copy, "nvidia/demo-winner", "nvidia/demo-fallback")
    patched = load_policy(policy_copy).section("nvidia")
    assert patched["winner"] == "nvidia/demo-winner"
    assert patched["fallback_429"] == "nvidia/demo-fallback"
    jobs = _jobs(policy_copy)
    assert jobs["screenshot_tickers"]["winner"] == "nvidia/demo-winner"
    assert jobs["screenshot_tickers"]["fallback_429"] == "nvidia/demo-fallback"
    assert jobs["notes"]["winner"] == "keep-notes"
    assert jobs["notes"]["fallback_429"] == "keep-notes-fb"
    committed = load_policy(POLICY_PATH).section("nvidia")
    assert committed["winner"] == "minimaxai/minimax-m3"
    assert committed["fallback_429"] == "nvidia/nemotron-nano-12b-v2-vl"


def test_write_job_winner_two_tables_independent(tmp_path: Path) -> None:
    # Notes header must be first so a file-global winner= replace fails this test.
    path = tmp_path / "policy.toml"
    path.write_text(_two_winner_toml(notes_first=True), encoding="utf-8")
    write_job_winner(path, SCREENSHOT_JOB, "new-shot", "new-shot-fb")
    jobs = _jobs(path)
    nvidia = load_policy(path).section("nvidia")
    assert nvidia["winner"] == "new-shot"
    assert nvidia["fallback_429"] == "new-shot-fb"
    assert jobs["screenshot_tickers"]["winner"] == "new-shot"
    assert jobs["notes"]["winner"] == "notes-winner"
    assert jobs["notes"]["fallback_429"] == "notes-fallback"

    path.write_text(_two_winner_toml(notes_first=False), encoding="utf-8")
    write_job_winner(path, "notes", "new-notes", "new-notes-fb")
    jobs = _jobs(path)
    nvidia = load_policy(path).section("nvidia")
    assert nvidia["winner"] == "top-winner"
    assert jobs["screenshot_tickers"]["winner"] == "shot-winner"
    assert jobs["notes"]["winner"] == "new-notes"
    assert jobs["notes"]["fallback_429"] == "new-notes-fb"


def test_write_job_winner_missing_keys_does_not_patch_file(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    original = (
        "[nvidia]\n"
        'winner = "top-winner"\n'
        'fallback_429 = "top-fallback"\n'
        "\n[nvidia.jobs.screenshot_tickers]\n"
        "# table present; winner/fallback_429 keys omitted\n"
        "\n[nvidia.jobs.notes]\n"
        'winner = "notes-winner"\n'
        'fallback_429 = "notes-fallback"\n'
    )
    path.write_text(original, encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError):
        write_job_winner(path, SCREENSHOT_JOB, "x", "y")
    assert path.read_bytes() == before

    path.write_text(
        "[nvidia]\n"
        'winner = "top-winner"\n'
        'fallback_429 = "top-fallback"\n'
        "\n[nvidia.jobs.screenshot_tickers]\n"
        'winner = "shot-winner"\n'
        "\n[nvidia.jobs.notes]\n"
        'winner = "notes-winner"\n'
        'fallback_429 = "notes-fallback"\n',
        encoding="utf-8",
    )
    before = path.read_bytes()
    with pytest.raises(ValueError):
        write_job_winner(path, SCREENSHOT_JOB, "x", "y")
    assert path.read_bytes() == before


def test_write_job_winner_missing_table_does_not_patch_file(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    path.write_text(
        "[nvidia]\n"
        'winner = "top-winner"\n'
        'fallback_429 = "top-fallback"\n'
        "\n[nvidia.jobs.notes]\n"
        'winner = "notes-winner"\n'
        'fallback_429 = "notes-fallback"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        write_job_winner(path, SCREENSHOT_JOB, "x", "y")
    text = path.read_text(encoding="utf-8")
    assert 'winner = "top-winner"' in text
    assert 'winner = "notes-winner"' in text
    assert 'winner = "x"' not in text


def test_screenshot_cli_write_does_not_patch_10k_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    shot = tmp_path / "broker.png"
    shot.write_bytes(_PNG)
    (tmp_path / "manifest.json").write_text(
        json.dumps({"broker.png": ["AAPL"]}), encoding="utf-8"
    )
    policy_copy = tmp_path / "policy.toml"
    text = POLICY_PATH.read_text(encoding="utf-8")
    text += (
        '\n[nvidia.jobs.notes]\nwinner = "keep-notes"\nfallback_429 = "keep-notes-fb"\n'
    )
    policy_copy.write_text(text, encoding="utf-8")
    cli = _load_cli()
    monkeypatch.setattr(cli, "NVIDIA_API_KEY", "test-key")
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        model = body.get("model")
        if model == "nvidia/nemotron-nano-12b-v2-vl":
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"tickers": ["AAPL"]}'}}]},
            )
        if "ocr" in str(request.url) or "/cv/" in str(request.url):
            return httpx.Response(200, json={"data": [{"text": "NOPE"}]})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"tickers": ["NOPE"]}'}}]},
        )

    def factory(*args, **kwargs):
        return real_async_client(
            transport=httpx.MockTransport(handler), follow_redirects=True
        )

    monkeypatch.setattr(cli.httpx, "AsyncClient", factory)
    code = cli.main(["--policy", str(policy_copy), "--screenshots", str(tmp_path)])
    assert code == 0
    nvidia = load_policy(policy_copy).section("nvidia")
    jobs = _jobs(policy_copy)
    assert nvidia["winner"] == "nvidia/nemotron-nano-12b-v2-vl"
    assert jobs["screenshot_tickers"]["winner"] == "nvidia/nemotron-nano-12b-v2-vl"
    assert jobs["notes"]["winner"] == "keep-notes"
    assert jobs["notes"]["fallback_429"] == "keep-notes-fb"
    src = (ROOT / "scripts" / "eval_image_models.py").read_text(encoding="utf-8")
    assert "write_job_winner" in src


def test_committed_policy_has_screenshot_job_not_must_jobs() -> None:
    nvidia = load_policy(POLICY_PATH).section("nvidia")
    jobs = nvidia.get("jobs") or {}
    assert "screenshot_tickers" in jobs
    for name in ("3", "6", "7", "12", "checker"):
        assert name not in jobs
    ids = [str(m["id"]) for m in (nvidia.get("bakeoff_models") or [])]
    assert "nvidia/nemotron-ocr-v2" in ids
    assert nvidia.get("ocr_url")
    assert nvidia["winner"] == "minimaxai/minimax-m3"
    assert nvidia["fallback_429"] == "nvidia/nemotron-nano-12b-v2-vl"


def test_eval_image_script_uses_policy_timeout_not_scraper_timeout() -> None:
    src = (ROOT / "scripts" / "eval_image_models.py").read_text(encoding="utf-8")
    assert "timeout_seconds" in src
    assert "REQUEST_TIMEOUT" not in src
    assert "write_job_winner" in src
    assert "write_nvidia_winner" not in src
