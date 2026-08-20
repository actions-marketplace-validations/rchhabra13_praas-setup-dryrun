import asyncio
import copy
import datetime
from functools import partial
from typing import Dict, List, Optional, Tuple

from jinja2 import Environment, StrictUndefined

from praas.algo.ai_handlers.base_ai_handler import BaseAiHandler
from praas.algo.ai_handlers.litellm_ai_handler import LiteLLMAIHandler
from praas.algo.pr_processing import (add_ai_metadata_to_diff_files,
                                         get_pr_diff,
                                         retry_with_fallback_models)
from praas.algo.repo_context import build_repo_context
from praas.algo.review_aggregator import merge_subagent_reviews
from praas.algo.review_grounding import flag_ungrounded_issues
from praas.algo.review_subagent_selector import select_relevant_subagents
from praas.algo.run_details import init_run_details
from praas.algo.skills_loader import get_skills_context
from praas.algo.token_handler import TokenHandler
from praas.algo.utils import (ModelType, PRReviewHeader,
                                 convert_to_markdown_v2, github_action_output,
                                 load_yaml, show_relevant_configurations,
                                 show_run_details)
from praas.config_loader import get_settings
from praas.git_providers import (get_git_provider_with_context)
from praas.git_providers.git_provider import (IncrementalPR,
                                                 get_main_pr_language)
from praas.log import get_logger
from praas.servers.help import HelpMessage
from praas.tools.ticket_pr_compliance_check import (
    extract_and_cache_pr_tickets)

MAX_REVIEW_COVERAGE_FILES = 50

# Order in which multi-subagent review's parallel AI calls are fired. 'correctness' is the
# default/primary subagent: it keeps every top-level Review field the single-call flow requests
# (aside from security_concerns), scoped down only in its key_issues_to_review focus.
REVIEW_SUBAGENTS = ["correctness", "security", "testing", "docs"]


def _last_yaml_key_for_subagent(subagent: str) -> str:
    """The last top-level field pr_reviewer_prompts.toml's Review schema actually emits for a
    given subagent's scoped prompt - used as load_yaml's last_key so its truncating YAML-repair
    fallback (try_fix_yaml's 4th tier) cuts off *after* the real content instead of mid-response.

    Mirrors the schema's {%- if review_subagent == ... %} gating: 'security' is the only
    subagent whose prompt appends a field (security_concerns) after key_issues_to_review: for
    every other subagent, key_issues_to_review is genuinely last. Getting this wrong doesn't
    just fail to parse - it silently truncates a real, already-generated finding (e.g. a
    security concern) out of the response.
    """
    if subagent == "security":
        return "security_concerns"
    if subagent == "correctness":
        if get_settings().pr_reviewer.get("require_can_be_split_review", False):
            return "can_be_split"
        if get_settings().pr_reviewer.get("require_todo_scan", False):
            return "todo_sections"
    return "key_issues_to_review"


class PRReviewer:
    """
    The PRReviewer class is responsible for reviewing a pull request and generating feedback using an AI model.
    """

    def __init__(self, pr_url: str, is_answer: bool = False, is_auto: bool = False, args: list = None,
                 ai_handler: partial[BaseAiHandler,] = LiteLLMAIHandler):
        """
        Initialize the PRReviewer object with the necessary attributes and objects to review a pull request.

        Args:
            pr_url (str): The URL of the pull request to be reviewed.
            is_answer (bool, optional): Indicates whether the review is being done in answer mode. Defaults to False.
            is_auto (bool, optional): Indicates whether the review is being done in automatic mode. Defaults to False.
            ai_handler (BaseAiHandler): The AI handler to be used for the review. Defaults to None.
            args (list, optional): List of arguments passed to the PRReviewer class. Defaults to None.
        """
        self.git_provider = get_git_provider_with_context(pr_url)
        self.args = args
        self.incremental = self.parse_incremental(args)  # -i command
        if self.incremental and self.incremental.is_incremental:
            self.git_provider.get_incremental_commits(self.incremental)

        self.main_language = get_main_pr_language(
            self.git_provider.get_languages(), self.git_provider.get_files()
        )
        self.pr_url = pr_url
        self.is_answer = is_answer
        self.is_auto = is_auto

        if self.is_answer and not self.git_provider.is_supported("get_issue_comments"):
            raise Exception(f"Answer mode is not supported for {get_settings().config.git_provider} for now")
        self.ai_handler = ai_handler()
        self.ai_handler.main_pr_language = self.main_language
        self.patches_diff = None
        self.remaining_files_list = []
        self.prediction = None
        # Populated only when config.pr_reviewer.enable_multi_subagent_review fans this review out
        # into parallel subagent calls; maps subagent name -> that subagent's parsed review dict. None
        # otherwise, meaning "use self.prediction as a single-call YAML response" as before.
        self._multi_subagent_reviews: Optional[Dict[str, dict]] = None
        question_str, answer_str = self._get_user_answers()
        self.pr_description, self.pr_description_files = (
            self.git_provider.get_pr_description(split_changes_walkthrough=True))
        if (self.pr_description_files and get_settings().get("config.is_auto_command", False) and
                get_settings().get("config.enable_ai_metadata", False)):
            add_ai_metadata_to_diff_files(self.git_provider, self.pr_description_files)
            get_logger().debug(f"AI metadata added to the this command")
        else:
            get_settings().set("config.enable_ai_metadata", False)
            get_logger().debug(f"AI metadata is disabled for this command")

        self.vars = {
            "title": self.git_provider.pr.title,
            "branch": self.git_provider.get_pr_branch(),
            "description": self.pr_description,
            "language": self.main_language,
            "diff": "",  # empty diff for initial calculation
            "num_pr_files": self.git_provider.get_num_of_files(),
            "num_max_findings": get_settings().pr_reviewer.num_max_findings,
            "require_score": get_settings().pr_reviewer.require_score_review,
            "require_tests": get_settings().pr_reviewer.require_tests_review,
            "require_estimate_effort_to_review": get_settings().pr_reviewer.require_estimate_effort_to_review,
            "require_estimate_contribution_time_cost": get_settings().pr_reviewer.require_estimate_contribution_time_cost,
            'require_can_be_split_review': get_settings().pr_reviewer.require_can_be_split_review,
            'require_security_review': get_settings().pr_reviewer.require_security_review,
            'require_todo_scan': get_settings().pr_reviewer.get("require_todo_scan", False),
            # None here means "single holistic review call" (today's behavior, unchanged).
            # _get_prediction overrides this per-call when multi-subagent review is enabled.
            'review_subagent': None,
            'question_str': question_str,
            'answer_str': answer_str,
            "extra_instructions": get_settings().pr_reviewer.extra_instructions,
            "skills_context": get_skills_context(),
            "repo_context": build_repo_context(self.git_provider),
            "commit_messages_str": self.git_provider.get_commit_messages(),
            "custom_labels": "",
            "enable_custom_labels": get_settings().config.enable_custom_labels,
            "is_ai_metadata":  get_settings().get("config.enable_ai_metadata", False),
            "related_tickets": get_settings().get('related_tickets', []),
            'duplicate_prompt_examples': get_settings().config.get('duplicate_prompt_examples', False),
            "date": datetime.datetime.now().strftime('%Y-%m-%d'),
        }

        self.token_handler = TokenHandler(
            self.git_provider.pr,
            self.vars,
            get_settings().pr_review_prompt.system,
            get_settings().pr_review_prompt.user
        )

    def parse_incremental(self, args: List[str]):
        is_incremental = False
        if args and len(args) >= 1:
            arg = args[0]
            if arg == "-i":
                is_incremental = True
        incremental = IncrementalPR(is_incremental)
        return incremental

    async def run(self) -> None:
        init_run_details()
        try:
            if not self.git_provider.get_files():
                get_logger().info(f"PR has no files: {self.pr_url}, skipping review")
                return None

            if self.incremental.is_incremental:
                can_run = self._can_run_incremental_review()
                # If the gate disabled incremental (e.g., commits_range is None), fall through to full review.
                if not can_run and self.incremental.is_incremental:
                    return None

            # if isinstance(self.args, list) and self.args and self.args[0] == 'auto_approve':
            #     get_logger().info(f'Auto approve flow PR: {self.pr_url} ...')
            #     self.auto_approve_logic()
            #     return None

            get_logger().info(f'Reviewing PR: {self.pr_url} ...')
            relevant_configs = {'pr_reviewer': dict(get_settings().pr_reviewer),
                                'config': dict(get_settings().config)}
            get_logger().debug("Relevant configs", artifacts=relevant_configs)

            # ticket extraction if exists
            await extract_and_cache_pr_tickets(self.git_provider, self.vars)

            if (
                self.incremental.is_incremental
                and hasattr(self.git_provider, "unreviewed_files_map")
                and not self.git_provider.unreviewed_files_map
            ):
                get_logger().info(f"Incremental review is enabled for {self.pr_url} but there are no new files")
                previous_review_url = ""
                if hasattr(self.git_provider, "previous_review") and self.git_provider.previous_review is not None:
                    previous_review_url = getattr(self.git_provider.previous_review, "html_url", "") or ""
                if get_settings().config.publish_output:
                    self.git_provider.publish_comment(f"Incremental Review Skipped\n"
                                    f"No files were changed since the [previous PR Review]({previous_review_url})")
                return None

            if get_settings().config.publish_output and not get_settings().config.get('is_auto_command', False):
                self.git_provider.publish_comment("Preparing review...", is_temporary=True)

            await retry_with_fallback_models(self._prepare_prediction, model_type=ModelType.REGULAR)
            if not self.prediction:
                self.git_provider.remove_initial_comment()
                return None

            pr_review = self._prepare_pr_review()
            get_logger().debug(f"PR output", artifact=pr_review)

            should_publish = get_settings().config.publish_output and self._should_publish_review_no_suggestions(pr_review)
            if not should_publish:
                reason = "Review output is not published"
                if get_settings().config.publish_output:
                    reason += ": no major issues detected."
                get_logger().info(reason)
                get_settings().data = {"artifact": pr_review}
                self._safe_remove_initial_comment()
                return

            # publish the review
            # Providers that support it (GitLab) can post the review's final comment as a resolvable thread.
            # This intent applies to the review only - never to status comments or the output of other tools.
            review_thread_kwargs = {"as_thread": True} if self.git_provider.should_publish_review_as_thread() else {}
            if get_settings().pr_reviewer.persistent_comment and not self.incremental.is_incremental:
                final_update_message = get_settings().pr_reviewer.final_update_message
                self.git_provider.publish_persistent_comment(pr_review,
                                                            initial_header=f"{PRReviewHeader.REGULAR.value} 🔍",
                                                            update_header=True,
                                                            final_update_message=final_update_message,
                                                            **review_thread_kwargs)
            else:
                self.git_provider.publish_comment(pr_review, **review_thread_kwargs)

            self.git_provider.remove_initial_comment()
        except Exception as e:
            get_logger().error(f"Failed to review PR: {e}")
            # Whatever failed above, the "Preparing review..." temp comment (if one was posted)
            # must not be left stuck on the PR - clean it up before propagating/swallowing e.
            self._safe_remove_initial_comment()
            if get_settings().config.get("propagate_tool_errors", False):
                raise

    def _safe_remove_initial_comment(self) -> None:
        """
        Remove the temporary "Preparing review..." comment, if one was posted.

        Every git provider's ``remove_initial_comment`` is already a no-op (and internally
        exception-safe) when no temporary comment exists, but this is called from failure paths
        - guard it anyway so a misbehaving/custom provider can't turn cleanup itself into a new
        crash that masks the original error.
        """
        try:
            self.git_provider.remove_initial_comment()
        except Exception as cleanup_error:
            get_logger().exception(f"Failed to remove initial comment during cleanup, error: {cleanup_error}")

    def _should_publish_review_no_suggestions(self, pr_review: str) -> bool:
        return get_settings().pr_reviewer.get('publish_output_no_suggestions', True) or "No major issues detected" not in pr_review

    async def _prepare_prediction(self, model: str) -> None:
        output = get_pr_diff(self.git_provider,
                             self.token_handler,
                             model,
                             add_line_numbers_to_hunks=True,
                             disable_extra_lines=False,
                             return_remaining_files=True,)
        if isinstance(output, tuple):
            self.patches_diff, self.remaining_files_list = output
        else:
            self.patches_diff = output
            self.remaining_files_list = []

        self._multi_subagent_reviews = None
        if self.patches_diff:
            get_logger().debug(f"PR diff", diff=self.patches_diff)
            if get_settings().pr_reviewer.get("enable_multi_subagent_review", False):
                self.prediction, self._multi_subagent_reviews = await self._get_multi_subagent_prediction(model)
            else:
                self.prediction = await self._get_prediction(model)
        else:
            get_logger().warning(f"Empty diff for PR: {self.pr_url}")
            self.prediction = None

    async def _get_prediction(self, model: str, review_subagent: Optional[str] = None) -> str:
        """
        Generate an AI prediction for the pull request review.

        Args:
            model: A string representing the AI model to be used for the prediction.
            review_subagent: When set, scopes the prompt to a single review subagent ('correctness',
                'security', 'testing', or 'docs') instead of requesting a holistic review.
                None (the default) reproduces today's single-call prompt exactly.

        Returns:
            A string representing the AI prediction for the pull request review.
        """
        variables = copy.deepcopy(self.vars)
        variables["diff"] = self.patches_diff  # update diff
        variables["review_subagent"] = review_subagent

        environment = Environment(undefined=StrictUndefined)
        system_prompt = environment.from_string(get_settings().pr_review_prompt.system).render(variables)
        user_prompt = environment.from_string(get_settings().pr_review_prompt.user).render(variables)

        response, finish_reason = await self.ai_handler.chat_completion(
            model=model,
            temperature=get_settings().config.temperature,
            system=system_prompt,
            user=user_prompt
        )

        return response

    async def _get_multi_subagent_prediction(self, model: str) -> Tuple[Optional[str], Optional[Dict[str, dict]]]:
        """
        Fan the review out into one AI call per relevant subagent (a subset of
        correctness/security/testing/docs). The subset is ``config.pr_reviewer.subagents_override``
        when set, otherwise the non-AI heuristic in ``select_relevant_subagents``. Each subagent call
        runs concurrently and its response is parsed independently.

        A subagent whose call raises, or whose response fails to parse into a review dict, is
        logged and dropped - the review continues with whichever subagents succeeded. Only if
        every subagent fails does this raise, so retry_with_fallback_models can fall back to the
        next configured model, mirroring the single-call flow's failure behavior.

        Returns:
            A tuple of (a representative raw response string, for parity with the single-call
            flow's truthiness check in run(); a dict mapping subagent name -> that subagent's parsed
            review dict, containing only the subagents that succeeded).
        """
        subagents_override = get_settings().pr_reviewer.get("subagents_override", None) or []
        subagents_to_run = [subagent for subagent in subagents_override if subagent in REVIEW_SUBAGENTS]
        if subagents_override and not subagents_to_run:
            get_logger().warning(f"Multi-subagent review: subagents_override {subagents_override} matched "
                                 f"none of {REVIEW_SUBAGENTS}, falling back to auto-selection")
        if subagents_to_run:
            get_logger().debug(f"Multi-subagent review: forced subagents {subagents_to_run} (subagents_override)")
        else:
            diff_files = [f.filename for f in (self.git_provider.get_diff_files() or [])]
            subagents_to_run = select_relevant_subagents(diff_files, self.patches_diff or "")
            get_logger().debug(f"Multi-subagent review: selected subagents {subagents_to_run}",
                               artifact={"diff_files": diff_files})

        responses = await asyncio.gather(
            *[self._get_prediction(model, review_subagent=subagent) for subagent in subagents_to_run],
            return_exceptions=True,
        )

        subagent_reviews: Dict[str, dict] = {}
        raw_responses: Dict[str, str] = {}
        for subagent, response in zip(subagents_to_run, responses):
            if isinstance(response, BaseException):
                get_logger().warning(f"Multi-subagent review: '{subagent}' subagent call failed",
                                     artifact={"error": str(response)})
                continue
            raw_responses[subagent] = response
            parsed = load_yaml(response.strip(),
                               keys_fix_yaml=["ticket_compliance_check", "estimated_effort_to_review_[1-5]:",
                                              "security_concerns:", "key_issues_to_review:", "relevant_file:",
                                              "relevant_line:", "suggestion:"],
                               first_key='review', last_key=_last_yaml_key_for_subagent(subagent))
            if not parsed or 'review' not in parsed or not isinstance(parsed['review'], dict):
                get_logger().warning(f"Multi-subagent review: '{subagent}' subagent response failed to parse",
                                     artifact={"response": response})
                continue
            subagent_reviews[subagent] = parsed['review']

        if not subagent_reviews:
            raise Exception("Multi-subagent review: all subagent calls failed or produced unparsable output")

        # Prefer the correctness subagent's raw text as the "representative" prediction (used only
        # as a non-empty marker and for debug visibility); fall back to whichever subagent succeeded.
        representative_prediction = raw_responses.get('correctness') or next(iter(raw_responses.values()))

        return representative_prediction, subagent_reviews

    def _prepare_pr_review(self) -> str:
        """
        Prepare the PR review by processing the AI prediction and generating a markdown-formatted text that summarizes
        the feedback.
        """
        # getattr, not self._multi_subagent_reviews: some tests build a PRReviewer via
        # PRReviewer.__new__ and never run __init__, so the attribute may not exist.
        multi_subagent_reviews = getattr(self, '_multi_subagent_reviews', None)
        if multi_subagent_reviews:
            data = {'review': merge_subagent_reviews(multi_subagent_reviews)}
        else:
            first_key = 'review'
            last_key = 'security_concerns'
            data = load_yaml(self.prediction.strip(),
                             keys_fix_yaml=["ticket_compliance_check", "estimated_effort_to_review_[1-5]:", "security_concerns:", "key_issues_to_review:",
                                            "relevant_file:", "relevant_line:", "suggestion:"],
                             first_key=first_key, last_key=last_key)
        github_action_output(data, 'review')

        if 'review' not in data:
            get_logger().exception("Failed to parse review data", artifact={"data": data})
            return ""

        # move data['review'] 'key_issues_to_review' key to the end of the dictionary
        if 'key_issues_to_review' in data['review']:
            key_issues_to_review = data['review'].pop('key_issues_to_review')
            if isinstance(key_issues_to_review, list):
                diff_files = [f.filename for f in (self.git_provider.get_diff_files() or [])]
                key_issues_to_review = flag_ungrounded_issues(key_issues_to_review, diff_files)
            data['review']['key_issues_to_review'] = key_issues_to_review

        incremental_review_markdown_text = None
        # Add incremental review section
        if self.incremental.is_incremental:
            last_commit_url = f"{self.git_provider.get_pr_url()}/commits/" \
                              f"{self.git_provider.incremental.first_new_commit_sha}"
            incremental_review_markdown_text = f"Starting from commit {last_commit_url}"

        markdown_text = convert_to_markdown_v2(data, self.git_provider.is_supported("gfm_markdown"),
                                            incremental_review_markdown_text,
                                               git_provider=self.git_provider,
                                               files=self.git_provider.get_diff_files(),
                                               multi_subagent_mode=bool(multi_subagent_reviews))

        if self.remaining_files_list and get_settings().pr_reviewer.enable_review_coverage_footer:
            displayed_files = self.remaining_files_list[:MAX_REVIEW_COVERAGE_FILES]
            markdown_text += (
                "\n\n<hr>\n\n"
                "⚠️ **Review coverage:** The following files were not included in this review "
                "because of the token budget:\n"
                + "\n".join(f"- `{file}`" for file in displayed_files)
            )
            remaining_count = len(self.remaining_files_list) - len(displayed_files)
            if remaining_count:
                markdown_text += f"\n... and {remaining_count} more"

        # Add help text if gfm_markdown is supported
        if self.git_provider.is_supported("gfm_markdown") and get_settings().pr_reviewer.enable_help_text:
            markdown_text += "<hr>\n\n<details> <summary><strong>💡 Tool usage guide:</strong></summary><hr> \n\n"
            markdown_text += HelpMessage.get_review_usage_guide()
            markdown_text += "\n</details>\n"

        # Output the relevant configurations if enabled
        if get_settings().get('config', {}).get('output_relevant_configurations', False):
            markdown_text += show_relevant_configurations(relevant_section='pr_reviewer')

        # Output the agent run details (model, tokens, time cost) if enabled
        if get_settings().get('config', {}).get('output_run_details', False):
            markdown_text += show_run_details(self.git_provider.is_supported("gfm_markdown"))

        # Add custom labels from the review prediction (effort, security)
        self.set_review_labels(data)

        if markdown_text == None or len(markdown_text) == 0:
            markdown_text = ""

        return markdown_text

    def _get_user_answers(self) -> Tuple[str, str]:
        """
        Retrieves the question and answer strings from the discussion messages related to a pull request.

        Returns:
            A tuple containing the question and answer strings.
        """
        question_str = ""
        answer_str = ""

        if self.is_answer:
            discussion_messages = self.git_provider.get_issue_comments()

            # providers return the comments oldest-first. PyGithub's PaginatedList reverses lazily,
            # so prefer it and only materialise the plain lists other providers return.
            newest_first = getattr(discussion_messages, "reversed", None)
            if newest_first is None:
                newest_first = reversed(list(discussion_messages))

            for message in newest_first:
                if "Questions to better understand the PR:" in message.body:
                    question_str = message.body
                elif '/answer' in message.body:
                    answer_str = message.body

                if answer_str and question_str:
                    break

        return question_str, answer_str

    def _get_previous_review_comment(self):
        """
        Get the previous review comment if it exists.
        """
        try:
            if hasattr(self.git_provider, "get_previous_review"):
                return self.git_provider.get_previous_review(
                    full=not self.incremental.is_incremental,
                    incremental=self.incremental.is_incremental,
                )
        except Exception as e:
            get_logger().exception(f"Failed to get previous review comment, error: {e}")

    def _remove_previous_review_comment(self, comment):
        """
        Remove the previous review comment if it exists.
        """
        try:
            if comment:
                self.git_provider.remove_comment(comment)
        except Exception as e:
            get_logger().exception(f"Failed to remove previous review comment, error: {e}")

    def _can_run_incremental_review(self) -> bool:
        """
        Checks if we can run incremental review according the various configurations and previous review.
        """
        # checking if running is auto mode but there are no new commits
        if self.is_auto and not self.incremental.first_new_commit_sha:
            get_logger().info(f"Incremental review is enabled for {self.pr_url} but there are no new commits")
            return False

        if not hasattr(self.git_provider, "get_incremental_commits"):
            get_logger().info(f"Incremental review is not supported for {get_settings().config.git_provider}")
            return False
        if self.incremental.commits_range is None:
            get_logger().info(
                f"Incremental review not initialized for {get_settings().config.git_provider}; "
                f"falling back to full review."
            )
            self.incremental.is_incremental = False
            return False
        # checking if there are enough commits to start the review
        num_new_commits = len(self.incremental.commits_range)
        num_commits_threshold = get_settings().pr_reviewer.minimal_commits_for_incremental_review
        not_enough_commits = num_new_commits < num_commits_threshold
        # checking if the commits are not too recent to start the review
        recent_commits_threshold = datetime.datetime.now() - datetime.timedelta(
            minutes=get_settings().pr_reviewer.minimal_minutes_for_incremental_review
        )
        last_seen_commit_date = (
            self.incremental.last_seen_commit.commit.author.date if self.incremental.last_seen_commit else None
        )
        all_commits_too_recent = (
            last_seen_commit_date > recent_commits_threshold if self.incremental.last_seen_commit else False
        )
        # check all the thresholds or just one to start the review
        condition = any if get_settings().pr_reviewer.require_all_thresholds_for_incremental_review else all
        if condition((not_enough_commits, all_commits_too_recent)):
            get_logger().info(
                f"Incremental review is enabled for {self.pr_url} but didn't pass the threshold check to run:"
                f"\n* Number of new commits = {num_new_commits} (threshold is {num_commits_threshold})"
                f"\n* Last seen commit date = {last_seen_commit_date} (threshold is {recent_commits_threshold})"
            )
            return False
        return True

    def set_review_labels(self, data):
        if not get_settings().config.publish_output:
            return

        if not get_settings().pr_reviewer.require_estimate_effort_to_review:
            get_settings().pr_reviewer.enable_review_labels_effort = False # we did not generate this output
        if not get_settings().pr_reviewer.require_security_review:
            get_settings().pr_reviewer.enable_review_labels_security = False # we did not generate this output

        if (get_settings().pr_reviewer.enable_review_labels_security or
                get_settings().pr_reviewer.enable_review_labels_effort):
            try:
                review_labels = []
                if get_settings().pr_reviewer.enable_review_labels_effort:
                    # 'estimated_effort_to_review_[1-5]' is correctness-sourced (review_aggregator.py):
                    # absent when multi-subagent selection didn't run the correctness subagent
                    # (e.g. a docs-only PR) - not an error, just nothing to derive this label from.
                    estimated_effort = data['review'].get('estimated_effort_to_review_[1-5]')
                    if estimated_effort is None:
                        get_logger().debug("No estimated_effort_to_review_[1-5] in review data; "
                                          "skipping effort label")
                    else:
                        estimated_effort_number = 0
                        if isinstance(estimated_effort, str):
                            try:
                                estimated_effort_number = int(estimated_effort.split(',')[0])
                            except ValueError:
                                get_logger().warning(f"Invalid estimated_effort value: {estimated_effort}")
                        elif isinstance(estimated_effort, int):
                            estimated_effort_number = estimated_effort
                        else:
                            get_logger().warning(f"Unexpected type for estimated_effort: {type(estimated_effort)}")
                        if 1 <= estimated_effort_number <= 5:  # 1, because ...
                            review_labels.append(f'Review effort {estimated_effort_number}/5')
                if get_settings().pr_reviewer.enable_review_labels_security and get_settings().pr_reviewer.require_security_review:
                    # 'security_concerns' is security-subagent-sourced: absent when multi-subagent
                    # selection didn't run the security subagent, or that subagent's call/parse failed.
                    security_concerns = data['review'].get('security_concerns')
                    if security_concerns is None:
                        get_logger().debug("No security_concerns in review data; skipping security label")
                    else:
                        security_concerns_bool = 'yes' in security_concerns.lower() or 'true' in security_concerns.lower()
                        if security_concerns_bool:
                            review_labels.append('Possible security concern')

                current_labels = self.git_provider.get_pr_labels(update=True)
                if not current_labels:
                    current_labels = []
                get_logger().debug(f"Current labels:\n{current_labels}")
                if current_labels:
                    current_labels_filtered = [label for label in current_labels if
                                               not label.lower().startswith('review effort') and not label.lower().startswith(
                                                   'possible security concern')]
                else:
                    current_labels_filtered = []
                new_labels = review_labels + current_labels_filtered
                if (current_labels or review_labels) and sorted(new_labels) != sorted(current_labels):
                    get_logger().info(f"Setting review labels:\n{review_labels + current_labels_filtered}")
                    self.git_provider.publish_labels(new_labels)
                else:
                    get_logger().info(f"Review labels are already set:\n{review_labels + current_labels_filtered}")
            except Exception as e:
                get_logger().error(f"Failed to set review labels, error: {e}")

    def auto_approve_logic(self):
        """
        Auto-approve a pull request if it meets the conditions for auto-approval.
        """
        if get_settings().config.enable_auto_approval:
            is_auto_approved = self.git_provider.auto_approve()
            if is_auto_approved:
                get_logger().info("Auto-approved PR")
                self.git_provider.publish_comment("Auto-approved PR")
        else:
            get_logger().info("Auto-approval option is disabled")
            self.git_provider.publish_comment("Auto-approval option for praas is disabled. "
                                              "You can enable it via a [configuration file](https://docs.praas.ai/tools/review/#auto-approval-1)")
