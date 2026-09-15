"""Remove configured credentials and recognizable tokens from diagnostic text."""
from __future__ import annotations

import importlib.util
import json
import os
import re
from functools import lru_cache
from pathlib import Path

TOKEN = re.compile(r"\b(?:sk-(?:proj-)?[A-Za-z0-9_*-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})")


def redact_text(text: str, secrets: tuple[str, ...] = ()) -> str:
    for secret in sorted(set(filter(None, secrets)), key=len, reverse=True):
        if len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
            text = text.replace(json.dumps(secret)[1:-1], "[REDACTED]")
    return TOKEN.sub("[REDACTED]", text)


@lru_cache(maxsize=8)
def configured_secrets(root: Path) -> tuple[str, ...]:
    values: list[str] = []

    def collect(mapping: dict) -> None:
        for name, value in mapping.items():
            if isinstance(value, dict):
                collect(value)
            elif isinstance(value, str) and any(word in str(name).lower() for word in ("api_key", "password", "secret", "token")):
                values.append(value)

    collect(dict(os.environ))
    path = root / "private.py"
    if path.is_file():
        spec = importlib.util.spec_from_file_location("govcontrol_redaction_private", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not load private configuration for log redaction.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        collect(vars(module))
    return tuple(filter(None, values))
