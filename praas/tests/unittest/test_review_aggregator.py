from praas.algo.review_aggregator import merge_subagent_reviews


def _issue(relevant_file="src/a.py", start_line=1, end_line=2, severity="medium", **extra):
    issue = {
        "relevant_file": relevant_file,
        "issue_header": extra.pop("issue_header", "Some issue"),
        "issue_content": extra.pop("issue_content", "Some content"),
        "start_line": start_line,
        "end_line": end_line,
        "severity": severity,
    }
    issue.update(extra)
    return issue


class TestFieldSourcing:
    def test_correctness_fields_pass_through_unchanged(self):
        subagent_reviews = {
            "correctness": {
                "score": "89",
                "estimated_effort_to_review_[1-5]": 3,
                "can_be_split": [{"relevant_files": ["a.py"], "title": "Split A"}],
                "todo_sections": "No",
                "ticket_compliance_check": [{"ticket_url": "https://x/1"}],
                "key_issues_to_review": [],
            },
        }

        merged = merge_subagent_reviews(subagent_reviews)

        assert merged["score"] == "89"
        assert merged["estimated_effort_to_review_[1-5]"] == 3
        assert merged["can_be_split"] == [{"relevant_files": ["a.py"], "title": "Split A"}]
        assert merged["todo_sections"] == "No"
        assert merged["ticket_compliance_check"] == [{"ticket_url": "https://x/1"}]

    def test_security_concerns_sourced_only_from_security_subagent(self):
        subagent_reviews = {
            "correctness": {"key_issues_to_review": []},
            "security": {"security_concerns": "SQL injection: ...", "key_issues_to_review": []},
        }

        merged = merge_subagent_reviews(subagent_reviews)

        assert merged["security_concerns"] == "SQL injection: ..."

    def test_relevant_tests_sourced_only_from_testing_subagent(self):
        subagent_reviews = {
            "correctness": {"relevant_tests": "yes", "key_issues_to_review": []},  # should be ignored
            "testing": {"relevant_tests": "No", "key_issues_to_review": []},
        }

        merged = merge_subagent_reviews(subagent_reviews)

        assert merged["relevant_tests"] == "No"

    def test_missing_subagent_simply_omits_its_fields(self):
        # security subagent failed/absent entirely: no security_concerns key at all, not a crash.
        subagent_reviews = {
            "correctness": {"score": "70", "key_issues_to_review": []},
        }

        merged = merge_subagent_reviews(subagent_reviews)

        assert "security_concerns" not in merged
        assert "relevant_tests" not in merged
        assert merged["score"] == "70"

    def test_empty_mapping_produces_empty_review_with_empty_issue_list(self):
        merged = merge_subagent_reviews({})

        assert merged["key_issues_to_review"] == []
        assert "score" not in merged
        assert "security_concerns" not in merged


class TestIssueTaggingAndConcatenation:
    def test_each_issue_tagged_with_its_producing_subagent_as_category(self):
        subagent_reviews = {
            "correctness": {"key_issues_to_review": [_issue(relevant_file="a.py")]},
            "security": {"key_issues_to_review": [_issue(relevant_file="b.py")]},
            "testing": {"key_issues_to_review": [_issue(relevant_file="c.py")]},
            "docs": {"key_issues_to_review": [_issue(relevant_file="d.py")]},
        }

        merged = merge_subagent_reviews(subagent_reviews)

        categories_by_file = {issue["relevant_file"]: issue["category"] for issue in merged["key_issues_to_review"]}
        assert categories_by_file == {
            "a.py": "correctness",
            "b.py": "security",
            "c.py": "testing",
            "d.py": "docs",
        }

    def test_model_supplied_category_is_overwritten_by_the_producing_subagent(self):
        # category is never asked of the model; even if present in a subagent's raw output it
        # must be overwritten with the actual subagent that produced it.
        subagent_reviews = {
            "security": {"key_issues_to_review": [_issue(category="not-security")]},
        }

        merged = merge_subagent_reviews(subagent_reviews)

        assert merged["key_issues_to_review"][0]["category"] == "security"

    def test_non_dict_or_non_list_key_issues_are_ignored_without_raising(self):
        subagent_reviews = {
            "correctness": {"key_issues_to_review": "No"},  # model returned a string, not a list
            "security": {"key_issues_to_review": [None, "not-a-dict", _issue(relevant_file="ok.py")]},
        }

        merged = merge_subagent_reviews(subagent_reviews)

        assert len(merged["key_issues_to_review"]) == 1
        assert merged["key_issues_to_review"][0]["relevant_file"] == "ok.py"


class TestDedupeAndSort:
    def test_same_location_across_subagents_is_deduped_keeping_higher_severity(self):
        subagent_reviews = {
            "correctness": {
                "key_issues_to_review": [_issue(relevant_file="x.py", start_line=10, end_line=12, severity="low",
                                                issue_header="Correctness view")],
            },
            "security": {
                "key_issues_to_review": [_issue(relevant_file="x.py", start_line=10, end_line=12, severity="critical",
                                                issue_header="Security view")],
            },
        }

        merged = merge_subagent_reviews(subagent_reviews)

        assert len(merged["key_issues_to_review"]) == 1
        kept = merged["key_issues_to_review"][0]
        assert kept["severity"] == "critical"
        assert kept["issue_header"] == "Security view"
        assert kept["category"] == "security"

    def test_different_locations_are_not_deduped(self):
        subagent_reviews = {
            "correctness": {"key_issues_to_review": [_issue(relevant_file="x.py", start_line=10, end_line=12)]},
            "security": {"key_issues_to_review": [_issue(relevant_file="x.py", start_line=50, end_line=52)]},
        }

        merged = merge_subagent_reviews(subagent_reviews)

        assert len(merged["key_issues_to_review"]) == 2

    def test_critical_and_high_severity_issues_sort_first(self):
        subagent_reviews = {
            "docs": {"key_issues_to_review": [_issue(relevant_file="d.py", start_line=1, end_line=1, severity="low")]},
            "correctness": {"key_issues_to_review": [
                _issue(relevant_file="c1.py", start_line=1, end_line=1, severity="medium"),
                _issue(relevant_file="c2.py", start_line=2, end_line=2, severity="critical"),
            ]},
            "security": {"key_issues_to_review": [_issue(relevant_file="s.py", start_line=1, end_line=1, severity="high")]},
        }

        merged = merge_subagent_reviews(subagent_reviews)

        severities_in_order = [issue["severity"] for issue in merged["key_issues_to_review"]]
        assert severities_in_order == ["critical", "high", "medium", "low"]

    def test_issues_without_location_info_are_not_deduped_but_still_sorted(self):
        subagent_reviews = {
            "docs": {"key_issues_to_review": [{"issue_header": "no location", "issue_content": "...",
                                               "severity": "low"}]},
            "security": {"key_issues_to_review": [{"issue_header": "also no location", "issue_content": "...",
                                                    "severity": "critical"}]},
        }

        merged = merge_subagent_reviews(subagent_reviews)

        assert len(merged["key_issues_to_review"]) == 2
        assert merged["key_issues_to_review"][0]["severity"] == "critical"

    def test_unknown_or_missing_severity_sorts_last(self):
        subagent_reviews = {
            "correctness": {"key_issues_to_review": [
                _issue(relevant_file="a.py", start_line=1, end_line=1, severity="critical"),
                _issue(relevant_file="b.py", start_line=1, end_line=1, severity="unexpected-value"),
            ]},
        }

        merged = merge_subagent_reviews(subagent_reviews)

        assert merged["key_issues_to_review"][0]["severity"] == "critical"
        assert merged["key_issues_to_review"][1]["severity"] == "unexpected-value"
