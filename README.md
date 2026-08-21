# praas

AI-powered pull request review agent for GitHub, backed by Google Gemini. Multi-lens feedback — correctness, security, testing, documentation — posted directly as a PR comment.

## Usage

Add `GEMINI_API_KEY` under **Settings → Secrets and variables → Actions**, then add this workflow:

```yaml
name: praas review

on:
  pull_request:
    types: [labeled]

jobs:
  review:
    if: github.event.label.name == 'praas-gemini'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
      issues: write
    steps:
      - uses: actions/checkout@v4
      - uses: rchhabra13/praas-setup-dryrun@v1
        with:
          pr_url: ${{ github.event.pull_request.html_url }}
          gemini-api-key: ${{ secrets.GEMINI_API_KEY }}
```

Create a `praas-gemini` label in your repo, apply it to any PR, and the review posts as a comment.

## Inputs

See [`action.yml`](action.yml). `pr_url` and `gemini-api-key` are required; `model` defaults to `gemini/gemini-2.5-flash`.

## Source

The reviewer itself lives in [`praas/`](praas/) — see [`praas/README.md`](praas/README.md) for CLI options and configuration.
