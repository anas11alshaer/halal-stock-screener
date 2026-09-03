"""Text job scorer tests: mocked NIM only, no live NVIDIA calls."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nvidia_nim import NimError, NvidiaNimClient, write_job_winner  # noqa: E402
from policy import load_policy  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "config" / "screening_policy.toml"

_MSFT_EXCERPT = (
    "Gaming revenue was $23.455 billion of total $281.724 billion (approximately 8.3%)."
)
_ITEM_1B_HEADING = "Item 1. Business\nItem 1B. Unresolved Staff Comments"
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def _load_jobs_cli():
    path = ROOT / "scripts" / "eval_nim_jobs.py"
    spec = importlib.util.spec_from_file_location("eval_nim_jobs_cli", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses with from __future__ import annotations look up sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _jobs(path: Path) -> dict:
    nvidia = load_policy(path).section("nvidia")
    jobs = nvidia.get("jobs") or {}
    return jobs if isinstance(jobs, dict) else {}


def _two_job_policy() -> str:
    return (
        "[nvidia]\n"
        'winner = "top-winner"\n'
        'fallback_429 = "top-fallback"\n'
        "\n[nvidia.jobs.screenshot_tickers]\n"
        'winner = "shot-winner"\n'
        'fallback_429 = "shot-fallback"\n'
        "\n[nvidia.jobs.notes]\n"
        'winner = "notes-winner"\n'
        'fallback_429 = "notes-fallback"\n'
    )


def test_billion_aliases_normalize_to_same_scale() -> None:
    jobs = _load_jobs_cli()
    a = jobs.extract_normalized_numbers("$23.455B")
    b = jobs.extract_normalized_numbers("$23,455 million")
    c = jobs.extract_normalized_numbers("23.455 billion")
    assert len(a) == 1 and len(b) == 1 and len(c) == 1
    assert math.isclose(a[0], b[0], rel_tol=1e-6)
    assert math.isclose(a[0], c[0], rel_tol=1e-6)
    assert math.isclose(a[0], 23.455e9, rel_tol=1e-6)
    million_only = jobs.extract_normalized_numbers("23.455 million")
    assert million_only
    assert not math.isclose(million_only[0], a[0], rel_tol=1e-3)
    pct = jobs.extract_normalized_numbers("8.3%")
    pct_word = jobs.extract_normalized_numbers("8.3 percent")
    assert pct and pct_word
    assert math.isclose(pct[0], pct_word[0], rel_tol=1e-6)
    assert math.isclose(pct[0], 8.3, rel_tol=1e-6)
    dollar_b = jobs.extract_normalized_numbers("$1B")
    decimal_b = jobs.extract_normalized_numbers("23.455B")
    assert dollar_b and jobs.numbers_close(dollar_b[0], 1e9)
    assert decimal_b and jobs.numbers_close(decimal_b[0], 23.455e9)


def test_item_1b_heading_is_not_a_billion() -> None:
    jobs = _load_jobs_cli()
    nums = jobs.extract_normalized_numbers(_ITEM_1B_HEADING)
    assert 1e9 not in nums
    assert not any(jobs.numbers_close(1e9, n) for n in nums)
    assert (
        jobs.checker_should_accept({"value": 1_000_000_000}, _ITEM_1B_HEADING) is False
    )


def test_number_not_in_excerpt_is_not_cited() -> None:
    jobs = _load_jobs_cli()
    cited = {"field": "revenue", "value": 23_455_000_000}
    invented = {"field": "revenue", "value": 999_000_000_000}
    assert jobs.all_cited(cited, _MSFT_EXCERPT) is True
    assert jobs.all_cited(invented, _MSFT_EXCERPT) is False
    score = jobs.score_searcher(
        '{"field": "revenue", "value": 999000000000}', _MSFT_EXCERPT
    )
    assert score.exact is False
    assert score.citation_rate < 1.0


def test_planted_invented_number_checker_rejects() -> None:
    jobs = _load_jobs_cli()
    planted = {"field": "revenue", "value": 999_000_000_000}
    assert jobs.checker_should_accept(planted, _MSFT_EXCERPT) is False
    wrong = jobs.score_checker(
        '{"accepted": true, "reason": "looks good"}', planted, _MSFT_EXCERPT
    )
    assert wrong.expected_accepted is False
    assert wrong.accepted is True
    assert wrong.exact is False
    assert wrong.errors == 0
    right = jobs.score_checker(
        '{"accepted": false, "reason": "not in excerpt"}', planted, _MSFT_EXCERPT
    )
    assert right.exact is True
    cited = {"field": "revenue", "value": 23_455_000_000}
    assert jobs.checker_should_accept(cited, _MSFT_EXCERPT) is True


def test_payload_with_halal_or_core_fail_is_rejected() -> None:
    jobs = _load_jobs_cli()
    searcher = jobs.score_searcher(
        '{"value": 23455000000, "verdict": "HALAL"}', _MSFT_EXCERPT
    )
    assert searcher.has_verdict_keys is True
    assert searcher.errors == 1
    core = jobs.score_searcher(
        '{"value": 23455000000, "core_fail": true}', _MSFT_EXCERPT
    )
    assert core.errors == 1
    checker = jobs.score_checker(
        '{"accepted": true, "reason": "NOT_HALAL"}',
        {"value": 23_455_000_000},
        _MSFT_EXCERPT,
    )
    assert checker.errors == 1
    assert checker.has_verdict_keys is True


def test_empty_or_echo_searcher_does_not_win() -> None:
    jobs = _load_jobs_cli()
    result = jobs.score_canned_fixtures(
        {
            "role": "searcher",
            "excerpt": _MSFT_EXCERPT,
            "models": [
                {"id": "echo", "response": "{}"},
                {
                    "id": "echo-excerpt",
                    "response": json.dumps({"excerpt": _MSFT_EXCERPT}),
                },
                {"id": "real", "response": '{"value": 23455000000}'},
            ],
        }
    )
    assert result.winner == "real"
    echo = next(s for s in result.scores if s.model_id == "echo")
    assert echo.citation_rate == 0.0
    assert echo.exact is False
    echoed = next(s for s in result.scores if s.model_id == "echo-excerpt")
    assert echoed.citation_rate == 0.0
    assert echoed.exact is False


def test_erroring_model_cannot_win_text_bakeoff() -> None:
    jobs = _load_jobs_cli()
    result = jobs.finalize_text_bakeoff(
        [
            jobs.TextScore(model_id="flaky", errors=1, citation_rate=1.0, exact=True),
            jobs.TextScore(model_id="steady", errors=0, citation_rate=0.5, exact=False),
        ]
    )
    assert result.winner == "steady"
    assert result.winner != "flaky"


def test_write_job_a_does_not_change_job_b(tmp_path: Path) -> None:
    path = tmp_path / "policy.toml"
    path.write_text(_two_job_policy(), encoding="utf-8")
    write_job_winner(path, "notes", "new-notes", "new-notes-fb")
    jobs = _jobs(path)
    nvidia = load_policy(path).section("nvidia")
    assert jobs["notes"]["winner"] == "new-notes"
    assert jobs["notes"]["fallback_429"] == "new-notes-fb"
    assert jobs["screenshot_tickers"]["winner"] == "shot-winner"
    assert nvidia["winner"] == "top-winner"


def test_jobs_cli_missing_key_or_fixtures_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_copy = tmp_path / "policy.toml"
    policy_copy.write_text(_two_job_policy(), encoding="utf-8")
    cli = _load_jobs_cli()
    monkeypatch.setattr(cli, "NVIDIA_API_KEY", "")
    code = cli.main(["--policy", str(policy_copy), "--job", "notes"])
    assert code == 2
    assert _jobs(policy_copy)["notes"]["winner"] == "notes-winner"

    monkeypatch.setattr(cli, "NVIDIA_API_KEY", "test-key")
    code = cli.main(["--policy", str(policy_copy), "--job", "notes"])
    assert code == 2
    missing = tmp_path / "missing.json"
    code = cli.main(
        ["--policy", str(policy_copy), "--job", "notes", "--fixtures", str(missing)]
    )
    assert code == 2
    assert _jobs(policy_copy)["notes"]["winner"] == "notes-winner"
    assert _jobs(policy_copy)["screenshot_tickers"]["winner"] == "shot-winner"


def test_jobs_cli_write_patches_only_named_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_copy = tmp_path / "policy.toml"
    policy_copy.write_text(_two_job_policy(), encoding="utf-8")
    fixtures = tmp_path / "notes.json"
    fixtures.write_text(
        json.dumps(
            {
                "role": "searcher",
                "excerpt": _MSFT_EXCERPT,
                "models": [
                    {
                        "id": "nvidia/demo-winner",
                        "response": '{"field": "revenue", "value": 23455000000}',
                    },
                    {
                        "id": "nvidia/demo-fallback",
                        "response": '{"field": "revenue", "value": 23455000000}',
                    },
                    {"id": "ratey", "status_code": 429},
                    {
                        "id": "judge",
                        "response": '{"verdict": "HALAL", "value": 23455000000}',
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    cli = _load_jobs_cli()
    monkeypatch.setattr(cli, "NVIDIA_API_KEY", "test-key")
    code = cli.main(
        [
            "--policy",
            str(policy_copy),
            "--job",
            "notes",
            "--fixtures",
            str(fixtures),
        ]
    )
    assert code == 0
    jobs = _jobs(policy_copy)
    nvidia = load_policy(policy_copy).section("nvidia")
    assert jobs["notes"]["winner"] == "nvidia/demo-winner"
    assert jobs["notes"]["fallback_429"] == "nvidia/demo-fallback"
    assert jobs["screenshot_tickers"]["winner"] == "shot-winner"
    assert nvidia["winner"] == "top-winner"


def test_jobs_cli_missing_job_table_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy_copy = tmp_path / "policy.toml"
    policy_copy.write_text(POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    fixtures = tmp_path / "notes.json"
    fixtures.write_text(
        json.dumps(
            {
                "role": "searcher",
                "excerpt": _MSFT_EXCERPT,
                "models": [
                    {
                        "id": "x",
                        "response": '{"value": 23455000000}',
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cli = _load_jobs_cli()
    monkeypatch.setattr(cli, "NVIDIA_API_KEY", "test-key")
    code = cli.main(
        [
            "--policy",
            str(policy_copy),
            "--job",
            "notes",
            "--fixtures",
            str(fixtures),
        ]
    )
    assert code == 2
    nvidia = load_policy(policy_copy).section("nvidia")
    committed = load_policy(POLICY_PATH).section("nvidia")
    assert nvidia.get("winner") == committed.get("winner")
    jobs = _jobs(policy_copy)
    assert "notes" not in jobs
    assert "3" not in jobs and "6" not in jobs and "checker" not in jobs


@pytest.mark.asyncio
async def test_chat_text_json_object_and_429() -> None:
    policy = load_policy(POLICY_PATH)
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        if captured["body"].get("model") == "rate-limited":
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = NvidiaNimClient("test-key", policy, http)
        text = await client.chat_text(model_id="nvidia/demo", prompt="give json")
        assert text == "{}"
        assert captured["body"]["response_format"] == {"type": "json_object"}
        assert captured["body"]["max_tokens"] == 2048
        assert captured["body"]["temperature"] == 0
        assert captured["body"]["messages"] == [
            {"role": "user", "content": "give json"}
        ]
        chat_url = str(policy.get("nvidia", "chat_url"))
        assert captured["url"] == chat_url
        vlm = await client.chat_vlm(
            model_id="nvidia/demo", image_bytes=_PNG, prompt="give json"
        )
        assert vlm == "{}"
        assert captured["body"]["max_tokens"] == 512
        assert "response_format" not in captured["body"]
        with pytest.raises(NimError) as excinfo:
            await client.chat_text(model_id="rate-limited", prompt="x")
        assert excinfo.value.status_code == 429
