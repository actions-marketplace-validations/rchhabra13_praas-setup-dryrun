"""Heuristic, non-AI pre-filter for multi-subagent review (see ``praas.tools.pr_reviewer``).

When ``config.pr_reviewer.enable_multi_subagent_review`` is on, a review otherwise fans out to
one AI call per subagent (correctness/security/testing/docs) unconditionally. That's 4x the API
calls of a single-call review, and wasteful when a diff obviously doesn't need all four
angles - e.g. a docs-only change has nothing for the security or testing subagent to find.

``select_relevant_subagents`` looks at the changed file list and the raw diff text (no AI call)
and returns the subset of subagents actually worth running. It is deliberately conservative:
whenever the diff doesn't clearly match a narrow "skip this subagent" pattern, it keeps that subagent
in (or, if nothing matches at all, falls back to running everything) - a missed finding is
worse than one extra API call.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence

from praas.config_loader import get_settings

# Mirrors praas.tools.pr_reviewer.REVIEW_SUBAGENTS. Duplicated (rather than imported) because
# pr_reviewer imports this module - importing back would be circular. Keep both lists in sync.
ALL_SUBAGENTS = ["correctness", "security", "testing", "docs"]

_DOC_EXTENSIONS = (".md", ".mdx", ".rst", ".txt", ".adoc")
_DOC_PATH_MARKERS = ("docs/", "doc/", "documentation/")
_DOC_FILENAME_MARKERS = (
    "readme", "changelog", "contributing", "code_of_conduct", "license", "release_notes",
)

_CONFIG_EXTENSIONS = (".yml", ".yaml", ".json", ".toml", ".ini", ".cfg", ".lock", ".properties")
_CONFIG_FILENAME_MARKERS = (
    "dockerfile", "makefile", ".gitignore", ".gitattributes", ".env.example", ".dockerignore",
)

_CODE_EXTENSIONS = (
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rb", ".php", ".c", ".cc", ".cpp",
    ".h", ".hpp", ".cs", ".rs", ".swift", ".kt", ".kts", ".scala", ".sh", ".bash", ".vue", ".m", ".mm",
)

_TEST_PATH_MARKERS = ("test/", "tests/", "__tests__/")
_TEST_FILENAME_RE = re.compile(r"(^|[/_.])(test_|_test\.|\.test\.|_spec\.|\.spec\.)", re.IGNORECASE)

# Deliberately broad: false positives here only cost one extra (cheap) API call, while a false
# negative means a real security-relevant change silently skips the security subagent.
_SECURITY_DIFF_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bselect\b[\s\S]{0,60}\bfrom\b",
    r"\binsert\s+into\b",
    r"\bupdate\b[\s\S]{0,60}\bset\b",
    r"\bdelete\s+from\b",
    r"\.execute\s*\(",
    r"cursor\.execute",
    # Lookaround (not \b) on purpose: \b treats "_" as a word char, so \btoken\b would miss
    # DEPLOY_TOKEN, and a plain \b misses the plural in "secrets.AWS_ROLE_ARN" - both are
    # exactly the CI/CD secret-wiring diffs this heuristic exists to catch.
    r"(?<![A-Za-z])passwords?(?![A-Za-z])",
    r"(?<![A-Za-z])passwd(?![A-Za-z])",
    r"(?<![A-Za-z])tokens?(?![A-Za-z])",
    r"(?<![A-Za-z])secrets?(?![A-Za-z])",
    r"(?<![A-Za-z])api[_-]?keys?(?![A-Za-z])",
    r"(?<![A-Za-z])credentials?",
    r"\bjwt\b",
    r"\boauth\b",
    r"\bsession\b",
    r"\bauth(entication|orization)?\b",
    r"os\.environ",
    r"getenv\s*\(",
    r"\.env\b",
    r"request\.(args|form|json|get|post|files|headers|cookies)",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"pickle\.loads",
    r"yaml\.load\s*\(",
    r"subprocess\.",
    r"os\.system\s*\(",
    r"os\.path\.join",
    r"\.\./",
    r"os\.remove",
    r"shutil\.",
))

_SECURITY_FILENAME_MARKERS = ("auth", "session", "login", "credential", "security", "token", "password")


_LEADING_DOT_SLASH_RE = re.compile(r"^(?:\./)+")


def _normalize(path: str) -> str:
    # A prefix strip, not a lstrip("./") char-set strip - the latter also eats the leading "."
    # of a dotfile (e.g. ".env.example"), which is otherwise a distinct filename.
    return _LEADING_DOT_SLASH_RE.sub("", path.strip().lower())


def _filename(path: str) -> str:
    return _normalize(path).rsplit("/", 1)[-1]


def _is_doc_file(path: str) -> bool:
    normalized = _normalize(path)
    if normalized.endswith(_DOC_EXTENSIONS):
        return True
    if any(marker in normalized for marker in _DOC_PATH_MARKERS):
        return True
    filename = _filename(path)
    return any(filename == marker or filename.startswith(marker) for marker in _DOC_FILENAME_MARKERS)


def _is_config_file(path: str) -> bool:
    normalized = _normalize(path)
    if _filename(path) in _CONFIG_FILENAME_MARKERS:
        return True
    return normalized.endswith(_CONFIG_EXTENSIONS)


def _is_test_file(path: str) -> bool:
    normalized = _normalize(path)
    if any(marker in normalized for marker in _TEST_PATH_MARKERS):
        return True
    return bool(_TEST_FILENAME_RE.search(normalized))


def _is_code_file(path: str) -> bool:
    return _normalize(path).endswith(_CODE_EXTENSIONS)


def _looks_security_relevant(diff_files: Sequence[str], patches_diff: str) -> bool:
    for path in diff_files:
        filename = _filename(path)
        if any(marker in filename for marker in _SECURITY_FILENAME_MARKERS):
            return True
    haystack = patches_diff or ""
    return any(pattern.search(haystack) for pattern in _SECURITY_DIFF_PATTERNS)


def select_relevant_subagents(diff_files: Iterable[str], patches_diff: str) -> List[str]:
    """
    Pick the multi-subagent review subagents worth running for a diff, without any AI call.

    Args:
        diff_files: changed file paths (e.g. ``FilePatchInfo.filename`` for every file in the
            diff).
        patches_diff: the raw unified-diff text sent to the model (used only for keyword
            scanning, e.g. to catch SQL/auth/env-var/file-path patterns for the security subagent).

    Returns:
        A non-empty subset of ``ALL_SUBAGENTS``, in ``ALL_SUBAGENTS`` order. Always respects the
        existing ``pr_reviewer.require_security_review`` / ``require_tests_review`` config
        toggles - a subagent gated off by one of those never appears here regardless of diff
        content. Falls back to every subagent the config allows whenever the diff doesn't clearly
        match a narrower pattern (empty file list, or a file type the heuristic doesn't
        recognize as docs/config/test/code).
    """
    diff_files = [f for f in diff_files if f]
    require_security = get_settings().pr_reviewer.require_security_review
    require_tests = get_settings().pr_reviewer.require_tests_review

    def _allowed(subagent: str) -> bool:
        if subagent == "security" and not require_security:
            return False
        if subagent == "testing" and not require_tests:
            return False
        return True

    def _finalize(selected: Iterable[str]) -> List[str]:
        result = [subagent for subagent in ALL_SUBAGENTS if subagent in selected and _allowed(subagent)]
        if result:
            return result
        # Everything selected got vetoed by a require_* flag - fall back to whatever the
        # config still allows, but never return an empty list.
        fallback = [subagent for subagent in ALL_SUBAGENTS if _allowed(subagent)]
        return fallback or list(ALL_SUBAGENTS)

    if not diff_files:
        # No file list to reason about at all - stay safe and run everything allowed.
        return _finalize(ALL_SUBAGENTS)

    doc_files = [f for f in diff_files if _is_doc_file(f)]
    if len(doc_files) == len(diff_files):
        # Every changed file is docs/markdown: nothing for security or testing to find.
        return _finalize(["docs"])

    selected = set()

    non_test_code_files = [f for f in diff_files if _is_code_file(f) and not _is_test_file(f)]
    if non_test_code_files:
        # Non-trivial application logic changed - correctness, test-coverage, and security all
        # apply by default here, on the same footing. Security must not be demoted to an
        # opt-in-by-keyword-match lens: a vuln class the keyword list doesn't happen to
        # recognize (e.g. SSRF via a code path with no "url"/"request" literal, broken access
        # control) would otherwise silently never get a security pass at all.
        selected.add("correctness")
        selected.add("testing")
        selected.add("security")

    if doc_files:
        selected.add("docs")

    if _looks_security_relevant(diff_files, patches_diff):
        selected.add("security")

    if not selected:
        # Nothing matched code/docs/security. If every file is at least classifiable as
        # config or test (with no security signal), a plain correctness pass is enough.
        # Otherwise we don't recognize what changed - stay safe and run everything.
        unclassified = [f for f in diff_files if not (_is_config_file(f) or _is_test_file(f) or _is_doc_file(f))]
        if unclassified:
            return _finalize(ALL_SUBAGENTS)
        selected.add("correctness")

    return _finalize(selected)
