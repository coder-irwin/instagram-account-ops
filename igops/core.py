"""Shared plumbing: clock, redaction, and the redacted run log.

Everything that prints or writes goes through `redact()`. Secret values loaded from
the vault are registered with the redactor at the moment they are read, so even a
value we never anticipated is masked if it reaches a log line.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------- clock
# A single source of time so the demo can fast-forward to show reminders and escalation.
_offset = timedelta(0)


def now() -> datetime:
    return datetime.now(timezone.utc) + _offset


def advance(hours: float) -> None:
    global _offset
    _offset += timedelta(hours=hours)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- redaction
_known_secrets: set[str] = set()
_lock = threading.Lock()

_PATTERNS = [
    (re.compile(r"(?i)(https?://)([^:/@\s]+):([^@\s]+)@"), r"\1[REDACTED]:[REDACTED]@"),      # creds in URLs
    (re.compile(r"(?i)\b(proxy-authorization|authorization)\s*[:=]\s*\S+(\s+\S+)?"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)\b(access_token|token|password|passwd|secret|api_key|apikey)=([^&\s]+)"), r"\1=[REDACTED]"),
    (re.compile(r"\bEAA[A-Za-z0-9]{20,}\b"), "[REDACTED_META_TOKEN]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "[REDACTED_KEY]"),
]


def register_secret(value: str) -> None:
    """Mask this exact value everywhere from now on."""
    if value and len(value) >= 4:
        with _lock:
            _known_secrets.add(value)


def redact(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    with _lock:
        for s in sorted(_known_secrets, key=len, reverse=True):
            text = text.replace(s, "[REDACTED]")
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


def mask_contact(value: str | None) -> str:
    """Partially mask an email or phone for reports: keeps enough to recognise it."""
    if not value:
        return ""
    if "@" in value:
        user, _, domain = value.partition("@")
        return f"{user[:2]}***@{domain}"
    digits = re.sub(r"\D", "", value)
    return f"***{digits[-3:]}" if len(digits) >= 3 else "***"


# ---------------------------------------------------------------- run log
class RunLog:
    """Append-only JSON Lines log. Every line is redacted before it touches disk or screen."""

    def __init__(self, path: Path, echo: bool = True):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.echo = echo

    def write(self, event: str, **fields) -> None:
        record = {"at": iso(), "event": event, **fields}
        line = redact(json.dumps(record, ensure_ascii=False, default=str))
        with self.path.open("a") as f:
            f.write(line + "\n")
        if self.echo:
            msg = fields.get("msg") or ""
            print(redact(f"[{record['at'][11:19]}] {event:<22} {msg}"))
