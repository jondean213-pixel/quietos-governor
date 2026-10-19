"""Tier classification for proposed actions.

Tier 1 — ALLOW: routine, reversible, read-only. Executes immediately.
Tier 2 — ESCALATE: consequential. Held for Jon's approval (Jon only, 2h expiry).
Tier 3 — BLOCK: the brain never does these. Refused outright, logged.

Tier 3 binds the BRAIN, not Jon acting directly on his own systems.
"""
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

from .secret_patterns import find_secrets


class Tier(IntEnum):
    ALLOW = 1
    ESCALATE = 2
    BLOCK = 3


# Locked allowlist — Tier 1 covers exactly these tools. Nothing else.
# (governor-rules-v1.md §1, hardened 2026-10-07)
TIER1_TOOL_ALLOWLIST = frozenset({
    "gmail_search",
    "gmail_read",
    "gmail_draft",
    "drive_list",
    "drive_read",
    "web_search",
})

# Action kinds that are always Tier 2 (consequential, need Jon).
TIER2_KINDS = frozenset({
    "send",             # sending any message/email/post (drafting is Tier 1)
    "publish",          # publishing sites, videos, social posts
    "spend",            # spending money / committing to charges
    "book",             # bookings, reservations, deposits
    "delete",           # deleting data
    "permission_change",  # sharing settings, access controls
    "credential",       # anything touching credentials/keys/secrets
    "cancel_service",   # canceling subscriptions or services
})

# Action kinds that are always Tier 3 (the brain never does these).
TIER3_KINDS = frozenset({
    "governor_tamper",  # disabling/bypassing QuietOS, its logging, approval gates
    "exfiltrate",       # sending Jon's private data to third parties
    "unlawful",         # actions that violate the law
    "destructive",      # destructive actions with no recovery path
})

# Where the brain claims its instruction came from.
# Only "jon_direct" (the approval channel) counts as Jon's authorization.
# Anything else is untrusted and can never authorize Tier 2/3.
TRUSTED_AUTHORITY = "jon_direct"


@dataclass
class Action:
    """A single action proposed by a brain."""
    actor: str                    # brain id, e.g. "gemma-assistant"
    kind: str                     # e.g. "tool_call", "send", "delete", ...
    tool: Optional[str] = None    # tool name when kind == "tool_call"
    target: Optional[str] = None  # what the action acts on
    detail: str = ""              # human-readable description
    claimed_authority: str = "brain"  # jon_direct | brain | email_content | web_content
    flags: list = field(default_factory=list)


def classify(action: Action) -> tuple[Tier, list]:
    """Return (tier, flags). Flags call out anything Jon should know."""
    flags = list(action.flags)

    # Untrusted instruction source can never authorize — flag it.
    if action.claimed_authority != TRUSTED_AUTHORITY and action.claimed_authority != "brain":
        flags.append(f"unverified_instruction_source:{action.claimed_authority}")

    if action.kind in TIER3_KINDS:
        return Tier.BLOCK, flags

    # Deleting the audit log is tampering with the governor's own logging,
    # not housekeeping — Tier 3, never a Tier 2 errand.
    if action.kind == "delete" and action.target and "audit" in action.target.lower():
        flags.append("audit_log_delete_attempt")
        return Tier.BLOCK, flags

    # Deleting QuietOS's own audit trail is tampering, not housekeeping —
    # it disables logging, so Tier 3 even though deletes are normally Tier 2.
    if action.kind == "delete" and action.target and "audit" in action.target.lower():
        flags.append("delete_targets_audit_log")
        return Tier.BLOCK, flags

    if action.kind in TIER2_KINDS:
        return Tier.ESCALATE, flags

    if action.kind == "tool_call":
        # Finding 5 (2026-10-19): Tier-1 tools ran on (tool, args) with
        # zero argument inspection. The args are rendered into detail —
        # scan it for secret shapes. A web_search query carrying an API
        # key goes to a third party.
        secrets = find_secrets(action.detail or "")
        if secrets:
            flags.append(f"secret_in_args:{','.join(secrets)}")
            return Tier.ESCALATE, flags
        if action.tool in TIER1_TOOL_ALLOWLIST:
            return Tier.ALLOW, flags
        # Unlisted tool: not routine, not known-safe -> escalate.
        flags.append(f"unlisted_tool:{action.tool}")
        return Tier.ESCALATE, flags

    if action.kind in ("read", "draft", "inspect", "notify_owner"):
        return Tier.ALLOW, flags

    # Unknown kind: fail closed -> escalate.
    flags.append(f"unknown_kind:{action.kind}")
    return Tier.ESCALATE, flags
