"""Merges multi-subagent review outputs into a single review dict.

``praas.tools.pr_reviewer.PRReviewer`` can optionally fan a review out into four
parallel, narrowly-scoped AI calls ("subagents") instead of one holistic call: ``correctness``,
``security``, ``testing``, and ``docs`` (see ``config.pr_reviewer.enable_multi_subagent_review``).
Each subagent's response is parsed independently (with the existing ``load_yaml`` helper) into
its own ``review`` dict. This module combines those dicts into a single review dict shaped
exactly like the one produced by the original single-call flow, so it can be handed straight
to ``praas.algo.utils.convert_to_markdown_v2`` without that renderer needing to know
multi-subagent review happened at all.

Field sourcing:
- ``key_issues_to_review``: concatenated from every subagent that produced one. Each issue is
  tagged with ``category`` = the subagent that raised it (the model is never asked for
  ``category`` - only ``severity`` is a model-requested field, since only the model can make
  that judgment call). Issues that overlap in ``(relevant_file, start_line, end_line)`` across
  subagents are deduped, keeping the more severe of the two. The final list is sorted so
  critical/high severity issues sort first.
- ``security_concerns``: from the ``security`` subagent only.
- ``relevant_tests``: from the ``testing`` subagent only.
- Every other top-level field (``score``, ``estimated_effort_to_review_[1-5]``,
  ``can_be_split``, ``todo_sections``, ``ticket_compliance_check``,
  ``contribution_time_cost_estimate``, ``insights_from_user_answers``) comes from the
  ``correctness`` subagent, unaltered.

A subagent that failed to call or failed to parse is simply absent from the ``subagent_reviews``
mapping passed in here - the merge degrades gracefully rather than failing the whole review.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Lower rank == more severe. Unknown/missing severities sort last.
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Top-level Review fields that are always sourced from the 'correctness' subagent when present.
CORRECTNESS_SOURCED_FIELDS = [
    "ticket_compliance_check",
    "estimated_effort_to_review_[1-5]",
    "contribution_time_cost_estimate",
    "score",
    "insights_from_user_answers",
    "todo_sections",
    "can_be_split",
]


def _severity_rank(issue: Dict[str, Any]) -> int:
    severity = str(issue.get("severity", "")).strip().lower()
    return _SEVERITY_RANK.get(severity, len(_SEVERITY_RANK))


def _issue_location_key(issue: Dict[str, Any]) -> Optional[Tuple[str, str, str]]:
    """A dedup key for an issue's location, or None if it doesn't have one to key on."""
    relevant_file = issue.get("relevant_file")
    start_line = issue.get("start_line")
    end_line = issue.get("end_line")
    if relevant_file is None or start_line is None or end_line is None:
        return None
    return (str(relevant_file).strip(), str(start_line).strip(), str(end_line).strip())


def _dedupe_and_sort_issues(issues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Dedupe issues that share a (file, start_line, end_line) across subagents, keeping the
    more severe copy, then sort the result so critical/high severity issues sort first.

    Issues without enough location info to key on are never deduped against each other,
    only sorted.
    """
    kept_by_location: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    unkeyed: List[Dict[str, Any]] = []

    for issue in issues:
        location_key = _issue_location_key(issue)
        if location_key is None:
            unkeyed.append(issue)
            continue
        existing = kept_by_location.get(location_key)
        if existing is None or _severity_rank(issue) < _severity_rank(existing):
            kept_by_location[location_key] = issue

    merged = list(kept_by_location.values()) + unkeyed
    # list.sort is stable, so issues of equal severity keep their relative (subagent) order.
    merged.sort(key=_severity_rank)
    return merged


def merge_subagent_reviews(subagent_reviews: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Merge per-subagent parsed review dicts into a single review dict.

    Args:
        subagent_reviews: mapping of subagent name ('correctness', 'security', 'testing', 'docs') to
            that subagent's parsed ``data['review']`` dict. A subagent that failed to call or parse
            should simply be omitted from this mapping - the merge degrades gracefully.

    Returns:
        A single review dict, shaped like the existing single-call review output.
    """
    merged: Dict[str, Any] = {}

    correctness = subagent_reviews.get("correctness")
    if isinstance(correctness, dict):
        for field in CORRECTNESS_SOURCED_FIELDS:
            if field in correctness:
                merged[field] = correctness[field]

    security = subagent_reviews.get("security")
    if isinstance(security, dict) and "security_concerns" in security:
        merged["security_concerns"] = security["security_concerns"]

    testing = subagent_reviews.get("testing")
    if isinstance(testing, dict) and "relevant_tests" in testing:
        merged["relevant_tests"] = testing["relevant_tests"]

    all_issues: List[Dict[str, Any]] = []
    for subagent_name in ("correctness", "security", "testing", "docs"):
        subagent_review = subagent_reviews.get(subagent_name)
        if not isinstance(subagent_review, dict):
            continue
        issues = subagent_review.get("key_issues_to_review")
        if not isinstance(issues, list):
            continue
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            tagged_issue = dict(issue)
            tagged_issue["category"] = subagent_name
            all_issues.append(tagged_issue)

    merged["key_issues_to_review"] = _dedupe_and_sort_issues(all_issues)

    return merged
