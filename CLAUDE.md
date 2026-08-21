# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

`praas` is an AI-powered pull request review agent, distributed as a GitHub Marketplace Docker container action (`action.yml`). A consumer workflow adds one `uses:` step; the action builds the agent image and posts a multi-lens review (correctness, security, testing, documentation) back to the PR. This build targets the Google Gemini backend only.

## Layout

```
.
├── action.yml           # Marketplace action definition (docker: Dockerfile)
├── Dockerfile           # Builds the praas agent image from praas/
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
- Runtime config uses Dynaconf with `envvar_prefix=False`; env vars passed via `action.yml` are section-scoped double-underscore names (`CONFIG__MODEL`, `PR_REVIEWER__...`, `GOOGLE_AI_STUDIO__...`), not a `PRAAS_` prefix.
- The reviewed project's local config is read from `[tool.praas]` in its `pyproject.toml` (see `praas/praas/config_loader.py`).

## Deployment

Consumer setup (secret, workflow snippet, label) is documented in the root [`README.md`](README.md).
