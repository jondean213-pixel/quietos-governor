"""Secret and PII patterns for Tier-1 argument inspection.

Finding 5 (2026-10-19 red-team): Tier-1 tools executed on (tool, args)
with zero argument inspection. A brain could exfiltrate secrets through
a web_search query (sent to a third-party API) or read a secret file and
have its contents flow straight into context. These patterns catch the
obvious shapes; they're a backstop, not a guarantee.
"""
import re

# (name, compiled regex) — matched against tool arguments and results.
PATTERNS = [
    ("private_key", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("generic_api_key", re.compile(
        r"\b(?:api[_-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}['\"]?",
        re.IGNORECASE)),
    ("password_assign", re.compile(
        r"\bpassword\s*[:=]\s*['\"]?[^'\"\s]{4,}['\"]?", re.IGNORECASE)),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9_\-\.~\+/]+=*\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,16}\b")),
]


def find_secrets(text: str) -> list:
    """Return names of secret patterns found in text."""
    if not text:
        return []
    return [name for name, pat in PATTERNS if pat.search(text)]


def redact_secrets(text: str) -> str:
    """Replace secret matches with [REDACTED:<name>]."""
    if not text:
        return text
    for name, pat in PATTERNS:
        text = pat.sub(f"[REDACTED:{name}]", text)
    return text
