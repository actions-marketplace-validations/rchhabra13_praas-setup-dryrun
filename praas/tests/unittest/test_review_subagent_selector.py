"""Tests for praas.algo.review_subagent_selector.select_relevant_subagents - the non-AI heuristic
that picks which multi-subagent review subagents are worth running for a given diff, so multi-subagent
review doesn't unconditionally fan out to all four subagents (correctness/security/testing/docs)
on every review, e.g. burning a security-subagent API call on a docs-only change.
"""
from praas.algo.review_subagent_selector import ALL_SUBAGENTS, select_relevant_subagents
from praas.config_loader import get_settings


def _with_require_flags(security=True, tests=True):
    settings = get_settings()
    original = {
        "require_security_review": settings.pr_reviewer.require_security_review,
        "require_tests_review": settings.pr_reviewer.require_tests_review,
    }
    settings.pr_reviewer.require_security_review = security
    settings.pr_reviewer.require_tests_review = tests
    return settings, original


def _restore(settings, original):
    settings.pr_reviewer.require_security_review = original["require_security_review"]
    settings.pr_reviewer.require_tests_review = original["require_tests_review"]


def test_docs_only_diff_excludes_security_and_testing():
    settings, original = _with_require_flags()
    try:
        result = select_relevant_subagents(["README.md", "docs/usage.md", "CHANGELOG.md"], "some prose diff")
    finally:
        _restore(settings, original)

    assert result == ["docs"]
    assert "security" not in result
    assert "testing" not in result


def test_sql_query_diff_includes_security():
    settings, original = _with_require_flags()
    try:
        result = select_relevant_subagents(
            ["src/db/queries.py"],
            'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")',
        )
    finally:
        _restore(settings, original)

    assert "security" in result


def test_auth_file_diff_includes_security():
    settings, original = _with_require_flags()
    try:
        result = select_relevant_subagents(
            ["src/auth/session.py"],
            "def login(username, password):\n    session['user'] = username",
        )
    finally:
        _restore(settings, original)

    assert "security" in result


def test_new_application_logic_diff_includes_testing_correctness_and_security():
    """Security must run on the same footing as correctness/testing for any real code
    change, not only when a keyword happens to match - a vuln class the keyword list
    doesn't recognize (e.g. SSRF, broken access control) would otherwise silently never
    get a security pass."""
    settings, original = _with_require_flags()
    try:
        result = select_relevant_subagents(
            ["src/billing/invoice.py"],
            "+def calculate_total(items):\n+    return sum(item.price for item in items)\n",
        )
    finally:
        _restore(settings, original)

    assert "testing" in result
    assert "correctness" in result
    assert "security" in result


def test_ambiguous_diff_with_unrecognized_file_type_falls_back_to_all_four():
    settings, original = _with_require_flags()
    try:
        # A .proto file isn't docs, config, test, or a recognized code extension - the
        # heuristic can't classify it, so it should stay conservative and run everything.
        result = select_relevant_subagents(["api/service.proto"], "message Foo { string bar = 1; }")
    finally:
        _restore(settings, original)

    assert set(result) == set(ALL_SUBAGENTS)


def test_empty_diff_files_falls_back_to_all_four():
    settings, original = _with_require_flags()
    try:
        result = select_relevant_subagents([], "")
    finally:
        _restore(settings, original)

    assert set(result) == set(ALL_SUBAGENTS)


def test_require_security_review_false_always_excludes_security():
    settings, original = _with_require_flags(security=False, tests=True)
    try:
        # Even a blatant SQL-injection diff must not select security when the user has
        # explicitly disabled require_security_review globally.
        result = select_relevant_subagents(
            ["src/db/queries.py"],
            'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")',
        )
        assert "security" not in result

        # And the "ambiguous -> all four" fallback must also respect the flag.
        fallback_result = select_relevant_subagents([], "")
        assert "security" not in fallback_result
    finally:
        _restore(settings, original)


def test_require_tests_review_false_always_excludes_testing():
    settings, original = _with_require_flags(security=True, tests=False)
    try:
        result = select_relevant_subagents(
            ["src/billing/invoice.py"],
            "+def calculate_total(items):\n+    return sum(item.price for item in items)\n",
        )
        assert "testing" not in result

        fallback_result = select_relevant_subagents([], "")
        assert "testing" not in fallback_result
    finally:
        _restore(settings, original)


def test_result_never_empty_even_with_both_require_flags_off():
    settings, original = _with_require_flags(security=False, tests=False)
    try:
        result = select_relevant_subagents(["README.md"], "")
        assert result  # docs is never gated by require_security/tests

        result_code = select_relevant_subagents(
            ["src/billing/invoice.py"],
            "+def calculate_total(items):\n+    return sum(item.price for item in items)\n",
        )
        assert result_code  # correctness survives even with both flags off
    finally:
        _restore(settings, original)


def test_result_order_matches_all_subagents_order():
    settings, original = _with_require_flags()
    try:
        result = select_relevant_subagents([], "")
    finally:
        _restore(settings, original)

    assert result == list(ALL_SUBAGENTS)


def test_mixed_docs_and_code_diff_includes_docs_code_and_security_subagents():
    settings, original = _with_require_flags()
    try:
        result = select_relevant_subagents(
            ["src/feature.py", "docs/feature.md"],
            "+def new_feature():\n+    return True\n",
        )
    finally:
        _restore(settings, original)

    assert set(result) == {"correctness", "testing", "docs", "security"}


def test_security_included_even_when_no_keyword_pattern_matches():
    """The keyword/filename list in _looks_security_relevant can't cover every vuln class
    (e.g. SSRF via a plain HTTP client call with no "request."/"auth"/"token" literal
    anywhere in the diff). Security must still run because it's on by default for any
    real code change, not because a keyword happened to match."""
    settings, original = _with_require_flags()
    try:
        result = select_relevant_subagents(
            ["src/fetcher.py"],
            "+def fetch(url):\n+    return httpclient.get(url)\n",
        )
    finally:
        _restore(settings, original)

    assert "security" in result


def test_config_only_diff_selects_only_correctness():
    settings, original = _with_require_flags()
    try:
        result = select_relevant_subagents(["pyproject.toml", "Dockerfile"], "version = '1.2.3'")
    finally:
        _restore(settings, original)

    assert result == ["correctness"]


def test_test_only_diff_selects_only_correctness():
    settings, original = _with_require_flags()
    try:
        result = select_relevant_subagents(
            ["tests/unittest/test_invoice.py"],
            "+def test_calculate_total():\n+    assert calculate_total([]) == 0\n",
        )
    finally:
        _restore(settings, original)

    assert result == ["correctness"]
