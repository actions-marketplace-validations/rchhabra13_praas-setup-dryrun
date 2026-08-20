"""Tests for praas.algo.review_grounding.flag_ungrounded_issues - a deterministic, non-AI
check that a review issue's relevant_file was actually part of the diff the model was shown.
"""
from praas.algo.review_grounding import UNGROUNDED_FILE_WARNING, flag_ungrounded_issues


def test_issue_referencing_a_diff_file_is_left_unchanged():
    issues = [{"relevant_file": "src/a.py", "issue_content": "Off-by-one error."}]

    result = flag_ungrounded_issues(issues, ["src/a.py", "src/b.py"])

    assert result == issues


def test_issue_referencing_a_file_outside_the_diff_is_flagged():
    issues = [{"relevant_file": "src/nonexistent.py", "issue_content": "Off-by-one error."}]

    result = flag_ungrounded_issues(issues, ["src/a.py"])

    assert result[0]["issue_content"].startswith(UNGROUNDED_FILE_WARNING)
    assert "Off-by-one error." in result[0]["issue_content"]


def test_flagging_does_not_mutate_the_original_issue_dict():
    original = {"relevant_file": "src/nonexistent.py", "issue_content": "Original."}
    issues = [original]

    flag_ungrounded_issues(issues, ["src/a.py"])

    assert original["issue_content"] == "Original."


def test_leading_dot_slash_normalization_does_not_false_positive():
    issues = [{"relevant_file": "./src/a.py", "issue_content": "content"}]

    result = flag_ungrounded_issues(issues, ["src/a.py"])

    assert result == issues


def test_empty_diff_files_flags_nothing():
    """An empty diff_files list means there's nothing trustworthy to check against - not that
    every issue is ungrounded. False positives here would bury real findings under noise."""
    issues = [{"relevant_file": "src/a.py", "issue_content": "content"}]

    result = flag_ungrounded_issues(issues, [])

    assert result == issues


def test_issue_without_relevant_file_passes_through_unflagged():
    issues = [{"issue_content": "content"}]

    result = flag_ungrounded_issues(issues, ["src/a.py"])

    assert result == issues


def test_non_dict_entries_pass_through_unchanged():
    issues = ["not a dict", {"relevant_file": "src/a.py", "issue_content": "content"}]

    result = flag_ungrounded_issues(issues, ["src/a.py"])

    assert result == issues


def test_order_and_count_preserved_with_mixed_grounded_and_ungrounded():
    issues = [
        {"relevant_file": "src/a.py", "issue_content": "first"},
        {"relevant_file": "src/ghost.py", "issue_content": "second"},
        {"relevant_file": "src/b.py", "issue_content": "third"},
    ]

    result = flag_ungrounded_issues(issues, ["src/a.py", "src/b.py"])

    assert len(result) == 3
    assert result[0]["issue_content"] == "first"
    assert result[1]["issue_content"].startswith(UNGROUNDED_FILE_WARNING)
    assert result[2]["issue_content"] == "third"
