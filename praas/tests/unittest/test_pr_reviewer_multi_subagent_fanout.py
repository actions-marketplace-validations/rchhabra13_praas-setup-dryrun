"""Tests for PRReviewer's opt-in multi-subagent review fan-out: config.pr_reviewer.enable_multi_subagent_review
runs four parallel, narrowly-scoped AI calls (via the existing ai_handler.chat_completion) instead of
one holistic call, and merges their parsed results. Covers the fan-out itself, partial-subagent-failure
handling, and (when every subagent fails) that the flow still fails the same way the single-call flow does
so retry_with_fallback_models can move on to the next model.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from praas.config_loader import get_settings
from praas.tools.pr_reviewer import REVIEW_SUBAGENTS, PRReviewer

CANNED_YAML = {
    "correctness": """
review:
  score: |
    80
  key_issues_to_review:
    - relevant_file: |
        src/a.py
      issue_header: |
        Logic bug
      issue_content: |
        Off-by-one error in the loop bound.
      severity: |
        high
      start_line: 10
      end_line: 12
""",
    "security": """
review:
  security_concerns: |
    SQL injection: unescaped input reaches a raw query.
  key_issues_to_review:
    - relevant_file: |
        src/b.py
      issue_header: |
        SQL Injection
      issue_content: |
        User input is concatenated directly into a query string.
      severity: |
        critical
      start_line: 20
      end_line: 22
""",
    "testing": """
review:
  relevant_tests: |
    No
  key_issues_to_review: []
""",
    "docs": """
review:
  key_issues_to_review:
    - relevant_file: |
        src/c.py
      issue_header: |
        Missing docstring
      issue_content: |
        The new public function has no docstring.
      severity: |
        low
      start_line: 5
      end_line: 5
""",
}


def _base_vars():
    return {
        "title": "Add feature",
        "branch": "feature/x",
        "description": "desc",
        "language": "Python",
        "diff": "some diff",
        "num_pr_files": 1,
        "num_max_findings": 3,
        "require_score": True,
        "require_tests": True,
        "require_estimate_effort_to_review": True,
        "require_estimate_contribution_time_cost": False,
        "require_can_be_split_review": False,
        "require_security_review": True,
        "require_todo_scan": False,
        "review_subagent": None,
        "question_str": "",
        "answer_str": "",
        "extra_instructions": "",
        "skills_context": "",
        "repo_context": "",
        "commit_messages_str": "",
        "custom_labels": "",
        "enable_custom_labels": False,
        "is_ai_metadata": False,
        "related_tickets": [],
        "duplicate_prompt_examples": False,
        "date": "2026-08-17",
    }


def _make_reviewer(fail_subagents=()):
    """A PRReviewer wired for _prepare_prediction, with chat_completion mocked to return
    canned YAML per subagent (identified by the subagent-scoping sentence in the rendered system
    prompt), raising for any subagent named in `fail_subagents`.
    """
    reviewer = PRReviewer.__new__(PRReviewer)
    reviewer.git_provider = MagicMock()
    reviewer.git_provider.get_diff_files.return_value = []
    reviewer.git_provider.is_supported.return_value = True
    reviewer.git_provider.get_line_link.return_value = ""
    reviewer.pr_url = "https://example/pr/1"
    reviewer.token_handler = MagicMock()
    reviewer.remaining_files_list = []
    reviewer.incremental = SimpleNamespace(is_incremental=False)
    reviewer.prediction = None
    reviewer._multi_subagent_reviews = None
    reviewer.vars = _base_vars()
    reviewer.set_review_labels = MagicMock()

    async def fake_chat_completion(model, temperature, system, user):
        for subagent in REVIEW_SUBAGENTS:
            if f"scoped to a single subagent: **{subagent}**" in system:
                if subagent in fail_subagents:
                    raise RuntimeError(f"{subagent} subagent exploded")
                return CANNED_YAML[subagent], "stop"
        raise AssertionError("could not determine which subagent this system prompt is for")

    reviewer.ai_handler = SimpleNamespace(chat_completion=AsyncMock(side_effect=fake_chat_completion))
    return reviewer


def _with_multi_subagent_enabled():
    settings = get_settings()
    original = settings.pr_reviewer.get("enable_multi_subagent_review", False)
    settings.pr_reviewer.enable_multi_subagent_review = True
    return settings, original


@pytest.mark.asyncio
async def test_prepare_prediction_fans_out_to_all_four_subagents_and_calls_chat_completion_four_times():
    reviewer = _make_reviewer()
    settings, original = _with_multi_subagent_enabled()
    try:
        with patch("praas.tools.pr_reviewer.get_pr_diff", return_value="some diff"):
            await reviewer._prepare_prediction("gpt-x")
    finally:
        settings.pr_reviewer.enable_multi_subagent_review = original

    assert reviewer.ai_handler.chat_completion.await_count == 4
    assert reviewer._multi_subagent_reviews is not None
    assert set(reviewer._multi_subagent_reviews.keys()) == {"correctness", "security", "testing", "docs"}
    assert reviewer.prediction  # non-empty marker, like the single-call flow's raw YAML


@pytest.mark.asyncio
async def test_prepare_prediction_merged_review_renders_critical_banner_and_severity_badges():
    reviewer = _make_reviewer()
    settings, original = _with_multi_subagent_enabled()
    try:
        with patch("praas.tools.pr_reviewer.get_pr_diff", return_value="some diff"):
            await reviewer._prepare_prediction("gpt-x")
    finally:
        settings.pr_reviewer.enable_multi_subagent_review = original

    markdown = reviewer._prepare_pr_review()

    # Critical findings banner, above the normal table, for the critical security issue.
    assert "> [!CAUTION]" in markdown
    banner_index = markdown.index("> [!CAUTION]")
    table_index = markdown.index("<table>")
    assert banner_index < table_index
    assert "SQL injection: unescaped input reaches a raw query." in markdown[banner_index:table_index]

    # Severity badges rendered next to issues.
    assert "SQL Injection" in markdown
    assert "🔴 Critical" in markdown  # security issue
    assert "🟠 High" in markdown  # correctness issue
    assert "🔵 Low" in markdown  # docs issue

    # Fields sourced from their dedicated subagent.
    assert "No tests added for these changes" in markdown  # from testing subagent's relevant_tests: No


@pytest.mark.asyncio
async def test_prepare_prediction_survives_one_subagent_failing():
    """One of the four subagent calls raises; the review must still complete using the other
    three subagents' worth of data rather than failing outright."""
    reviewer = _make_reviewer(fail_subagents=("testing",))
    settings, original = _with_multi_subagent_enabled()
    try:
        with patch("praas.tools.pr_reviewer.get_pr_diff", return_value="some diff"):
            await reviewer._prepare_prediction("gpt-x")  # must not raise
    finally:
        settings.pr_reviewer.enable_multi_subagent_review = original

    assert set(reviewer._multi_subagent_reviews.keys()) == {"correctness", "security", "docs"}

    markdown = reviewer._prepare_pr_review()
    # The surviving subagents' content is still present...
    assert "SQL Injection" in markdown
    assert "Missing docstring" in markdown
    # ...but the failed subagent's dedicated field is simply absent, not a crash / placeholder error.
    assert "No tests added for these changes" not in markdown
    assert "Tests included in this PR" not in markdown


@pytest.mark.asyncio
async def test_prepare_prediction_raises_when_every_subagent_fails():
    """All four subagents failing must raise (not silently produce an empty review), so
    retry_with_fallback_models can fall back to the next configured model - the same
    failure contract the single-call flow has today."""
    reviewer = _make_reviewer(fail_subagents=("correctness", "security", "testing", "docs"))
    settings, original = _with_multi_subagent_enabled()
    try:
        with patch("praas.tools.pr_reviewer.get_pr_diff", return_value="some diff"):
            with pytest.raises(Exception):
                await reviewer._prepare_prediction("gpt-x")
    finally:
        settings.pr_reviewer.enable_multi_subagent_review = original


@pytest.mark.asyncio
async def test_prepare_prediction_only_calls_chat_completion_for_selected_subagents():
    """_get_multi_subagent_prediction must consult select_relevant_subagents and only fan out to the
    subagents it returns, not unconditionally all four - the whole point of the selector is to
    avoid burning API calls (and hitting rate limits) on subagents that don't apply to the diff.
    """
    reviewer = _make_reviewer()
    settings, original = _with_multi_subagent_enabled()
    try:
        with (
            patch("praas.tools.pr_reviewer.get_pr_diff", return_value="some diff"),
            patch(
                "praas.tools.pr_reviewer.select_relevant_subagents",
                return_value=["security", "docs"],
            ) as selector,
        ):
            await reviewer._prepare_prediction("gpt-x")
    finally:
        settings.pr_reviewer.enable_multi_subagent_review = original

    selector.assert_called_once()
    assert reviewer.ai_handler.chat_completion.await_count == 2
    assert reviewer._multi_subagent_reviews is not None
    assert set(reviewer._multi_subagent_reviews.keys()) == {"security", "docs"}


@pytest.mark.asyncio
async def test_prepare_prediction_passes_diff_files_and_patches_diff_to_selector():
    """The selector needs the changed-file list and raw diff text to make its call - confirm
    _get_multi_subagent_prediction actually threads real data through rather than placeholders."""
    from praas.algo.types import FilePatchInfo

    reviewer = _make_reviewer()
    reviewer.git_provider.get_diff_files.return_value = [
        FilePatchInfo(base_file="", head_file="", patch="", filename="src/a.py"),
        FilePatchInfo(base_file="", head_file="", patch="", filename="docs/readme.md"),
    ]
    settings, original = _with_multi_subagent_enabled()
    try:
        with (
            patch("praas.tools.pr_reviewer.get_pr_diff", return_value="the raw diff text"),
            patch(
                "praas.tools.pr_reviewer.select_relevant_subagents",
                return_value=["correctness"],
            ) as selector,
        ):
            await reviewer._prepare_prediction("gpt-x")
    finally:
        settings.pr_reviewer.enable_multi_subagent_review = original

    selector.assert_called_once_with(["src/a.py", "docs/readme.md"], "the raw diff text")
    assert reviewer.ai_handler.chat_completion.await_count == 1


@pytest.mark.asyncio
async def test_multi_subagent_review_disabled_by_default_uses_single_call_path():
    """Flag off (the default) must take the single-call path: exactly one chat_completion call,
    no subagent-scoping sentence in the rendered prompt, and no aggregator involvement."""
    reviewer = _make_reviewer()
    assert get_settings().pr_reviewer.get("enable_multi_subagent_review", False) is False

    single_call_yaml = CANNED_YAML["correctness"]
    reviewer.ai_handler = SimpleNamespace(chat_completion=AsyncMock(return_value=(single_call_yaml, "stop")))

    with patch("praas.tools.pr_reviewer.get_pr_diff", return_value="some diff"):
        await reviewer._prepare_prediction("gpt-x")

    assert reviewer.ai_handler.chat_completion.await_count == 1
    called_system_prompt = reviewer.ai_handler.chat_completion.await_args.kwargs["system"]
    assert "scoped to a single subagent" not in called_system_prompt
    assert reviewer._multi_subagent_reviews is None
    assert reviewer.prediction == single_call_yaml


@pytest.mark.asyncio
async def test_subagents_override_runs_only_the_requested_subagents_and_skips_the_selector():
    """config.pr_reviewer.subagents_override, when set, must take priority over
    select_relevant_subagents - the whole point is a caller-chosen subagent, not a heuristic guess."""
    reviewer = _make_reviewer()
    settings, original = _with_multi_subagent_enabled()
    original_override = settings.pr_reviewer.get("subagents_override", [])
    settings.pr_reviewer.subagents_override = ["security"]
    try:
        with (
            patch("praas.tools.pr_reviewer.get_pr_diff", return_value="some diff"),
            patch("praas.tools.pr_reviewer.select_relevant_subagents") as selector,
        ):
            await reviewer._prepare_prediction("gpt-x")
    finally:
        settings.pr_reviewer.enable_multi_subagent_review = original
        settings.pr_reviewer.subagents_override = original_override

    selector.assert_not_called()
    assert reviewer.ai_handler.chat_completion.await_count == 1
    assert set(reviewer._multi_subagent_reviews.keys()) == {"security"}


@pytest.mark.asyncio
async def test_subagents_override_with_unknown_subagent_falls_back_to_selector():
    """A typo'd or stale subagent name in subagents_override must not silently run zero subagents -
    fall back to the normal heuristic instead."""
    reviewer = _make_reviewer()
    settings, original = _with_multi_subagent_enabled()
    original_override = settings.pr_reviewer.get("subagents_override", [])
    settings.pr_reviewer.subagents_override = ["not-a-real-subagent"]
    try:
        with (
            patch("praas.tools.pr_reviewer.get_pr_diff", return_value="some diff"),
            patch(
                "praas.tools.pr_reviewer.select_relevant_subagents",
                return_value=["correctness"],
            ) as selector,
        ):
            await reviewer._prepare_prediction("gpt-x")
    finally:
        settings.pr_reviewer.enable_multi_subagent_review = original
        settings.pr_reviewer.subagents_override = original_override

    selector.assert_called_once()
    assert set(reviewer._multi_subagent_reviews.keys()) == {"correctness"}
