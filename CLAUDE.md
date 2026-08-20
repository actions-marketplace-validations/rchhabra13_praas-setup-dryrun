# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`praas` is an AI-powered pull request review agent that runs in a GitHub repository. A PR label triggers a GitHub Actions workflow that builds the agent as a container and posts a multi-lens review (correctness, security, testing, documentation) back to the PR. Reviews can run against Google Gemini, Amazon Bedrock models, or any OpenAI-compatible local endpoint.

## Layout

```
.
├── .github/workflows/   # Label-triggered review workflows (_praas-review.yml is the reusable core)
├── Dockerfile           # Builds the praas agent image from praas/
├── infra/               # Terraform: AWS OIDC trust + Bedrock IAM + optional GitHub secret/labels
└── praas/               # Agent source (Python package `praas` lives at praas/praas/)
    ├── praas/           # The importable Python package
    ├── pyproject.toml   # name = "praas", entry point praas = "praas.cli:run"
    └── tests/
```

## Build & run

The image is built at repo root with the top-level `Dockerfile`; it copies `praas/` and installs the `praas` package (console command: `praas`).

```bash
docker build -t praas -f Dockerfile .
```

Run the package tests from the source dir:

```bash
cd praas && PYTHONPATH=. python -m pytest tests/unittest -q
```

Compile-check the package without installing deps:

```bash
python -m compileall -q praas/praas
```

## Conventions

- The importable package is `praas` (never `pr_agent`). Imports read `from praas...`.
- Runtime config uses Dynaconf with `envvar_prefix=False`; workflow env vars are section-scoped double-underscore names (`CONFIG__MODEL`, `PR_REVIEWER__...`, `AWS__...`), not a `PRAAS_` prefix.
- The reviewed project's local config is read from `[tool.praas]` in its `pyproject.toml` (see `praas/praas/config_loader.py`).
- Review labels (`praas-gemini`, `praas-bedrock-*`, `praas-local-*`, `praas-all`) map to model backends; each has a caller workflow that invokes `_praas-review.yml`.

## Infra

`infra/` provisions AWS OIDC + Bedrock IAM permissions and, when `manage_github = true`, writes the `AWS_ROLE_ARN` secret and creates the review labels. See `setup-praas.md` (repo root). Terraform state must never be committed; `.gitignore` covers it.

## Deployment

End-user setup (prerequisites, credentials, variable lookups, Terraform apply, verification) is documented in the root [`README.md`](README.md).
