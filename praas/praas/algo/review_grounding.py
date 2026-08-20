"""Deterministic, non-AI grounding check for review findings (see docs/confidence-score research:
external, verifiable signals beat a model's self-reported confidence).

An LLM occasionally cites a `relevant_file` that was never part of the diff it was actually shown -
a cheap, zero-API-call tell that a finding may be miscited even when the underlying observation is
real. Ungrounded issues are flagged, not dropped: the observation itself may still be a real, useful
finding, just possibly attributed to the wrong location - and a false "this file doesn't exist"
positive here (e.g. a path-normalization mismatch) would silently delete a real finding, which is
worse than one unnecessary caveat.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

UNGROUNDED_FILE_WARNING = (
    "⚠️ Location not verified: this file was not found in the PR diff shown to the model - "
    "double-check the file/location before acting on this finding."
)

_LEADING_DOT_SLASH_RE = re.compile(r"^(?:\./)+")


def _normalize_path(path: str) -> str:
    # A prefix strip, not a lstrip("./") char-set strip - the latter also eats the leading "."
    # of a dotfile (e.g. ".env"), colliding it with an unrelated file named "env".
    return _LEADING_DOT_SLASH_RE.sub("", str(path).strip())


def flag_ungrounded_issues(issues: Iterable[Dict[str, Any]], diff_files: Iterable[str]) -> List[Dict[str, Any]]:
    """
    Tag each issue whose ``relevant_file`` doesn't match any file actually in the diff.

    Args:
        issues: parsed ``key_issues_to_review`` entries (dicts with at least ``relevant_file``).
        diff_files: the changed file paths the model was actually shown (e.g.
            ``FilePatchInfo.filename`` for every file in the diff).

    Returns:
        The same issues, in the same order. Each dict whose ``relevant_file`` isn't among
        ``diff_files`` gets its ``issue_content`` prefixed with a short caveat; everything else
        (including issues with no ``relevant_file`` at all, and non-dict entries) passes through
        unchanged. If ``diff_files`` is empty, nothing is flagged - an empty diff-file list means
        there's nothing trustworthy to check against, not that every issue is ungrounded.
    """
    known_files = {_normalize_path(f) for f in diff_files if f}
    if not known_files:
        return list(issues)

    result: List[Dict[str, Any]] = []
    for issue in issues:
        if not isinstance(issue, dict):
            result.append(issue)
            continue
        relevant_file = issue.get("relevant_file")
        if not relevant_file or _normalize_path(relevant_file) in known_files:
            result.append(issue)
            continue
        tagged = dict(issue)
        existing_content = str(tagged.get("issue_content", "")).strip()
        tagged["issue_content"] = (
            f"{UNGROUNDED_FILE_WARNING}\n\n{existing_content}" if existing_content else UNGROUNDED_FILE_WARNING
        )
        result.append(tagged)
    return result
