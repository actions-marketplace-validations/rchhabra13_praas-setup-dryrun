"""Tests that the pr_review_prompt system template gates Review schema fields correctly
per multi-subagent `review_subagent` value, layered on top of the existing require_* flags rather
than replacing them, and that review_subagent=None (today's single-call path) is unaffected.
"""
from jinja2 import Environment, StrictUndefined

from praas.algo.repo_context import render_instruction_files
from praas.config_loader import get_settings

BASE_VARS = {
    "extra_instructions": "",
    "repo_context": render_instruction_files({}),
    "skills_context": "",
    "require_can_be_split_review": True,
    "related_tickets": [{"ticket_url": "https://example/1", "title": "Ticket"}],
    "require_estimate_contribution_time_cost": True,
    "require_score": True,
    "require_tests": True,
    "question_str": "some question",
    "require_security_review": True,
    "require_todo_scan": True,
    "require_estimate_effort_to_review": True,
    "num_max_findings": 3,
    "num_pr_files": 1,
    "is_ai_metadata": False,
}


def _render_system(review_subagent=None, **overrides):
    variables = dict(BASE_VARS)
    variables["review_subagent"] = review_subagent
    variables.update(overrides)
    template = get_settings().pr_review_prompt.system
    environment = Environment(undefined=StrictUndefined)
    return environment.from_string(template).render(variables)


def _review_class_body(rendered_system: str) -> str:
    start = rendered_system.index("class Review(BaseModel):")
    end = rendered_system.index("class PRReview(BaseModel):")
    return rendered_system[start:end]


def _key_issues_component_link_body(rendered_system: str) -> str:
    start = rendered_system.index("class KeyIssuesComponentLink(BaseModel):")
    end = rendered_system.index("start_line: int = Field", start)
    return rendered_system[start:end]


class TestSingleCallPathUnaffected:
    """review_subagent=None must reproduce today's exact schema - this is the 'flag off' path."""

    def test_all_require_x_gated_fields_present_when_flags_are_on(self):
        body = _review_class_body(_render_system(review_subagent=None))

        assert "ticket_compliance_check: List[TicketCompliance]" in body
        assert "estimated_effort_to_review_[1-5]: int" in body
        assert "contribution_time_cost_estimate: ContributionTimeCostEstimate" in body
        assert "score: str" in body
        assert "relevant_tests: str" in body
        assert "insights_from_user_answers: str" in body
        assert "security_concerns: str" in body
        assert "todo_sections: Union[List[TodoSection], str]" in body
        assert "can_be_split: List[SubPR]" in body

    def test_no_severity_field_requested(self):
        body = _key_issues_component_link_body(_render_system(review_subagent=None))
        assert "severity" not in body

    def test_no_subagent_scoping_sentence_present(self):
        rendered = _render_system(review_subagent=None)
        assert "scoped to a single subagent" not in rendered

    def test_require_x_flags_still_gate_fields_as_before(self):
        body = _review_class_body(_render_system(review_subagent=None, require_score=False,
                                                   require_can_be_split_review=False))
        assert "score: str" not in body
        assert "can_be_split: List[SubPR]" not in body


class TestCorrectnessSubagent:
    def test_keeps_all_existing_fields_except_security_concerns(self):
        body = _review_class_body(_render_system(review_subagent="correctness"))

        assert "ticket_compliance_check: List[TicketCompliance]" in body
        assert "estimated_effort_to_review_[1-5]: int" in body
        assert "contribution_time_cost_estimate: ContributionTimeCostEstimate" in body
        assert "score: str" in body
        assert "relevant_tests: str" in body  # requested as a placeholder; aggregator ignores it
        assert "insights_from_user_answers: str" in body
        assert "can_be_split: List[SubPR]" in body
        assert "todo_sections: Union[List[TodoSection], str]" in body
        # security_concerns is carved out specifically so correctness doesn't duplicate security's job
        assert "security_concerns: str" not in body

    def test_still_respects_global_require_x_flags(self):
        body = _review_class_body(_render_system(review_subagent="correctness", require_score=False))
        assert "score: str" not in body

    def test_severity_field_requested_on_key_issues(self):
        body = _key_issues_component_link_body(_render_system(review_subagent="correctness"))
        assert "severity: str = Field" in body

    def test_scoping_sentence_mentions_correctness(self):
        rendered = _render_system(review_subagent="correctness")
        assert "scoped to a single subagent: **correctness**" in rendered
        assert "logic bugs" in rendered


class TestSecuritySubagent:
    def test_only_requests_security_concerns_and_key_issues(self):
        body = _review_class_body(_render_system(review_subagent="security"))

        assert "security_concerns: str" in body
        assert "key_issues_to_review: List[KeyIssuesComponentLink]" in body
        for absent_field in ("ticket_compliance_check:", "estimated_effort_to_review_[1-5]:",
                             "contribution_time_cost_estimate:", "score: str", "relevant_tests: str",
                             "insights_from_user_answers:", "todo_sections:", "can_be_split:"):
            assert absent_field not in body

    def test_respects_global_require_security_review_flag(self):
        body = _review_class_body(_render_system(review_subagent="security", require_security_review=False))
        assert "security_concerns: str" not in body

    def test_scoping_sentence_mentions_security(self):
        rendered = _render_system(review_subagent="security")
        assert "scoped to a single subagent: **security**" in rendered
        assert "security vulnerabilities" in rendered


class TestTestingSubagent:
    def test_only_requests_relevant_tests_and_key_issues(self):
        body = _review_class_body(_render_system(review_subagent="testing"))

        assert "relevant_tests: str" in body
        assert "key_issues_to_review: List[KeyIssuesComponentLink]" in body
        for absent_field in ("ticket_compliance_check:", "estimated_effort_to_review_[1-5]:",
                             "contribution_time_cost_estimate:", "score: str",
                             "insights_from_user_answers:", "security_concerns:", "todo_sections:",
                             "can_be_split:"):
            assert absent_field not in body

    def test_respects_global_require_tests_flag(self):
        body = _review_class_body(_render_system(review_subagent="testing", require_tests=False))
        assert "relevant_tests: str" not in body


class TestDocsSubagent:
    def test_only_requests_key_issues_no_other_top_level_fields(self):
        body = _review_class_body(_render_system(review_subagent="docs"))

        assert "key_issues_to_review: List[KeyIssuesComponentLink]" in body
        for absent_field in ("ticket_compliance_check:", "estimated_effort_to_review_[1-5]:",
                             "contribution_time_cost_estimate:", "score: str", "relevant_tests: str",
                             "insights_from_user_answers:", "security_concerns:", "todo_sections:",
                             "can_be_split:"):
            assert absent_field not in body

    def test_scoping_sentence_mentions_documentation(self):
        rendered = _render_system(review_subagent="docs")
        assert "scoped to a single subagent: **docs**" in rendered
        assert "documentation" in rendered


class TestPromptInjectionDefense:
    """The PR diff, title, description, and commit messages come from the PR author - an
    untrusted party from the reviewer's perspective. The system prompt must explicitly tell
    the model to treat that content as passive data, not instructions, for every subagent
    (single-call path included, since review_subagent=None must still get this)."""

    def test_defense_present_for_every_subagent_and_single_call_path(self):
        for review_subagent in (None, "correctness", "security", "testing", "docs"):
            rendered = _render_system(review_subagent=review_subagent)
            assert "passive data" in rendered
            assert "not instructions to you" in rendered
