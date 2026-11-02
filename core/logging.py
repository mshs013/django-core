"""Structured (JSON lines) logging with a redaction filter: secrets, tokens, hashes and file contents never reach the log."""
from __future__ import annotations

import json
import logging
import re
import time

from core.audit.redaction import MASK

_PATTERNS = [re.compile(r"(?i)(password|passwd|secret|token|authorization|api[_-]?key|cookie|csrf\w*|sessionid)(\s*[=:]\s*)([^\s,;&]+)"),
             re.compile(r"(?i)(postgres(?:ql)?://[^:/\s]+:)([^@\s]+)(@)"),
             re.compile(r"pbkdf2_sha256\$\S+|argon2\$\S+|\$2[aby]\$\S+")]


def scrub(text: str) -> str:
    text = _PATTERNS[0].sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", text)
    text = _PATTERNS[1].sub(lambda m: f"{m.group(1)}{MASK}{m.group(3)}", text)
    return _PATTERNS[2].sub(MASK, text)


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = scrub(record.getMessage())
            record.args = ()
        except Exception:  # noqa: BLE001
            record.msg, record.args = "[unprintable log record]", ()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + "Z", "level": record.levelname,
                   "logger": record.name, "message": scrub(record.getMessage())}
        if record.exc_info:
            payload["exception"] = scrub(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)
