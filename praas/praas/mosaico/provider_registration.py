"""Idempotent registration of DiffInputProvider into praas's provider registry.

Importing this module inserts the "mosaico_diff" provider via setdefault (never
clobbers existing keys). Only the MOSAICO server imports it, so the registry is
untouched on every other code path."""
from praas.git_providers import _GIT_PROVIDERS
from praas.mosaico.diff_provider import DiffInputProvider

_GIT_PROVIDERS.setdefault("mosaico_diff", DiffInputProvider)
