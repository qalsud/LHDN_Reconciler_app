"""Suite hygiene: provider credentials never leak into tests.

`src/api/app.py` and `src/config/settings.py` load the real project
`.env` on import, which would otherwise let live keys (and model
overrides) bleed across tests and cause network calls or order-dependent
failures. Tests that need a key set it explicitly via monkeypatch.
"""

import os

import pytest

ISOLATED_VARS = (
    "OPENAI_API_KEY",
    "XAI_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AI_MODEL",
    "AI_VISION_MODEL",
    "AI_VISION_BASE_URL",
    "TENANT_SEED",
    "ADMIN_KEY",
    "DATABASE_URL",
)


@pytest.fixture(autouse=True)
def _isolate_provider_env(monkeypatch):
    for var in ISOLATED_VARS:
        monkeypatch.delenv(var, raising=False)
