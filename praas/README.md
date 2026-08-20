# praas

`praas` is the core pull request review service maintained inside the [`praas/`](./) directory of the [`praas-test`](../README.md) repository. It includes provider integrations, model routing logic, multi-subagent review workflows, and diff analysis tools.

The installed console command is `praas`.

---

## Authoritative CLI Commands

The command registry in [`praas/agent/praas.py`](praas/agent/praas.py) defines the primary CLI entry points:

- **`review`**: Performs automated code review on a pull request or diff.
- **`describe`**: Generates or updates PR title, description, and labels.
- **`improve`**: Proposes specific, line-by-line code suggestions.
- **`ask`**: Answers questions regarding the changes in a pull request.
- **`ask_line`**: Answers questions targeting specified code lines.
- **`update_changelog`**: Generates changelog entries based on PR changes.
- **`add_docs`**: Drafts documentation updates for modified code.
- **`generate_labels`**: Suggests labels based on PR diff analysis.
- **`similar_issue`**: Searches for related GitHub issues (requires optional vector database dependencies).
- **`config` / `settings` / `help`**: Displays current settings or command usage assistance.

---

## Supported Git & Code Providers

`praas` supports integration with multiple code host platforms and local diff formats:

- **Git Hosts**: GitHub, GitLab, Bitbucket Cloud, Bitbucket Server, Azure DevOps, Gitea, Gerrit, AWS CodeCommit.
- **Local / Plain Diffs**: Local Git repositories, standalone unified diff files (`--diff-file`), and standard input streams (`--stdin`).

Platform support for specific capabilities is evaluated dynamically via each provider's `is_supported()` handler.

---

## Local Setup & Installation

### Requirements

- Python 3.12 or newer.
- `pip` and `virtualenv`.

### Step-by-Step Environment Setup

1. Create and activate a Python virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install runtime and development dependencies:

   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   pip install -e .
   ```

3. Execute a review against a remote pull request:

   ```bash
   OPENAI__KEY="your_api_key" \
   GITHUB__USER_TOKEN="your_github_token" \
   praas --pr_url https://github.com/owner/repository/pull/123 review
   ```

4. Execute a review against a local diff file or standard input:

   ```bash
   # Review a diff file
   praas --diff-file changes.diff review

   # Review via stdin
   git diff | praas --stdin review
   ```

   Plain-diff mode writes output to stdout. Add `--output <path>` to save review results to a file.

---

## Configuration Management

Configuration defaults and prompt templates are defined in TOML files under [`praas/settings/`](praas/settings/).

To override default configuration values for a repository, create `.praas.toml` at the repository root containing only the specific keys to override.

Example `.praas.toml`:

```toml
[config]
model = "openai/gpt-5.6"

[pr_reviewer]
num_max_findings = 5
```

> [!IMPORTANT]
> Secrets must be supplied via environment variables or an untracked `.secrets.toml` file. The runtime ignores `.env` files and deliberately does not load environment variables from unauthenticated disk locations.

### Multi-Subagent Review Configuration

Multi-subagent review allows `praas` to parallelize PR analysis across specialized review roles: `correctness`, `security`, `testing`, and `docs`.

To enable multi-subagent reviews programmatically or in `.praas.toml`:

```toml
[pr_reviewer]
enable_multi_subagent_review = true
subagents_override = ["correctness", "security", "testing", "docs"]
```

The repository workflows in [`.github/workflows/`](../.github/workflows/) set `PR_REVIEWER__ENABLE_MULTI_SUBAGENT_REVIEW="true"` by default when running containerized reviews.

---

## Testing & Verification

Run unit tests and verification steps from the `praas/` directory:

```bash
# Execute unit tests
PYTHONPATH=. ./.venv/bin/pytest tests/unittest -q

# Run pre-commit code formatting and syntax checks
PRE_COMMIT_HOME=/tmp/praas-pre-commit ./.venv/bin/pre-commit run --all-files

# Verify Python syntax compilation
./.venv/bin/python -m compileall -q praas tests scripts setup.py
```

---

## Codebase Map

- [`praas/agent/`](praas/agent/): Command registry and CLI entry points.
- [`praas/tools/`](praas/tools/): Tool implementations (`review`, `describe`, `improve`, `ask`, `docs`, `changelog`).
- [`praas/algo/`](praas/algo/): AI model adapters, prompt token management, and diff processing.
- [`praas/git_providers/`](praas/git_providers/): Git host provider adapters and plain diff handlers.
- [`praas/servers/`](praas/servers/): Webhook, GitHub App, and GitHub Action server handlers.
- [`praas/mosaico/`](praas/mosaico/): A2A server integration and telemetry modules.
- [`praas/settings/`](praas/settings/): Default configurations, TOML settings, and prompt templates.
- [`tests/unittest/`](tests/unittest/): Unit test suite.
- [`AGENTS.md`](AGENTS.md): Development rules and coding standards.
- [`SECURITY.md`](SECURITY.md): Security scope and vulnerability reporting policies.
