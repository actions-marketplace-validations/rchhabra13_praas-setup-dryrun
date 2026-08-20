# Builds from the vendored praas/ source in this repo (IkkaLabs' own fork,
# already carrying its own branding/config) instead of pulling any upstream
# Docker Hub image.
FROM python:3.12.13-slim AS base

RUN apt-get update && apt-get install --no-install-recommends -y git curl && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY praas/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY praas/pyproject.toml .
COPY praas/praas praas
RUN pip install --no-cache-dir --no-deps .

ENV PYTHONPATH=/app
ENTRYPOINT ["praas"]
