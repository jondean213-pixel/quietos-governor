"""The two-way Jon notification channel (sandbox).

The governor is mute inside the runtime: it queues (kind, payload) pairs
in gov.jon_notifications and the phone delivers them. This module is that
delivery, plus the road back:

  outbox — poll_outbox() renders queued notes into Notifications: what Jon
           sees on his phone, with Approve / Deny buttons on approval
           requests and the expiry time printed on the card.
  inbox  — receive_reply() accepts Jon's answer. A reply counts ONLY from
           the source "jon_device" — Jon's own phone. Anything else is a
           forgery attempt: audited, ignored. Replies reference a rendered
           notification, never a raw approval id, so a brain can't answer
           a request it never saw delivered.

Silence still kills at the 2-hour TTL: a late reply is logged as stale
and the action stays dead. Every send and every reply (accepted or not)
is written to the audit log.
"""
import time
import uuid
from datetime import datetime, timezone

from .approvals import APPROVER  # "jon" — nobody else decides


class Notification:
    """One rendered note for Jon's phone."""

    def __init__(self, kind, title, body, actions=None, approval_id=None,
                 approval_ids=None, brain=None, expires_at=None):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.title = title
        self.body = body
        self.actions = actions or []        # ["approve", "deny"] | ["unpause"] | []
        self.approval_id = approval_id
        self.approval_ids = approval_ids or []
        self.brain = brain
        self.expires_at = expires_at


class JonChannel:
    def __init__(self, governor, clock=None):
        self.gov = governor
        self.clock = clock or governor.clock
        self._sent: dict[str, Notification] = {}
        self._answered: set[str] = set()

    # -- outbox ----------------------------------------------------------
    def poll_outbox(self) -> list:
        """Drain the governor's queue into rendered notifications."""
        out = []
        while self.gov.jon_notifications:
            kind, payload = self.gov.jon_notifications.pop(0)
            n = self._render(kind, payload)
            self._sent[n.id] = n
            out.append(n)
            self.gov.audit.append("notification_sent", notification_id=n.id,
                                  kind=kind, approval_id=n.approval_id)
        return out

    def _render(self, kind, payload) -> Notification:
        if kind == "approval_needed":
            ap = self.gov.approvals.lookup(payload["id"])
            expires = ap.expires_at if ap else None
            body = payload["detail"]
            if expires:
                body += f"\nExpires {self._fmt(expires)} if unanswered."
            return Notification(kind, "QuietOS needs your call",
                                body, actions=["approve", "deny"],
                                approval_id=payload["id"], expires_at=expires)
        if kind == "approval_batch":
            count = payload.get("count", len(payload.get("ids", [])))
            return Notification(kind, "QuietOS: approvals waiting",
                                f"{count} Tier 2 requests are held. "
                                "Tap to review each one.",
                                approval_ids=payload.get("ids", []))
        if kind == "blocked":
            return Notification(kind, "QuietOS blocked an action",
                                payload["detail"])
        if kind == "brain_paused":
            return Notification(kind, "QuietOS paused a brain",
                                f"{payload['brain']}: {payload['reason']}",
                                actions=["unpause"], brain=payload["brain"])
        return Notification(kind, f"QuietOS: {kind}", str(payload))

    @staticmethod
    def _fmt(ts: float) -> str:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def pending(self) -> list:
        """Notifications still waiting on Jon."""
        return [n for n in self._sent.values() if n.id not in self._answered]

    # -- inbox -------------------------------------------------------------
    def receive_reply(self, notification_id: str, verb: str, source: str) -> str:
        """Jon's answer. Returns a result string; every outcome is audited."""
        if source != APPROVER + "_device":  # only Jon's phone counts
            self.gov.audit.append("channel_reply_forgery",
                                  notification_id=notification_id,
                                  verb=verb, source=source)
            return "rejected: unauthenticated source"
        n = self._sent.get(notification_id)
        if n is None:
            self.gov.audit.append("channel_reply_unknown",
                                  notification_id=notification_id, verb=verb)
            return "rejected: unknown notification"
        if n.id in self._answered:
            self.gov.audit.append("channel_reply_duplicate",
                                  notification_id=notification_id, verb=verb)
            return "ignored: already answered"
        if verb not in n.actions:
            self.gov.audit.append("channel_reply_not_actionable",
                                  notification_id=notification_id, verb=verb,
                                  kind=n.kind)
            return "ignored: not actionable"
        self._answered.add(n.id)
        if n.approval_id and verb in ("approve", "deny"):
            approve = verb == "approve"
            try:
                if approve:
                    self.gov.jon_approve(n.approval_id)
                else:
                    self.gov.jon_deny(n.approval_id)
            except TimeoutError:
                self.gov.audit.append("channel_reply_stale",
                                      notification_id=notification_id, verb=verb,
                                      approval_id=n.approval_id)
                return "stale: expired before your reply"
            self.gov.audit.append("channel_reply", notification_id=notification_id,
                                  verb=verb, approval_id=n.approval_id)
            return "approved" if approve else "denied"
        if n.brain and verb == "unpause":
            self.gov.jon_unpause(n.brain)
            self.gov.audit.append("channel_reply", notification_id=notification_id,
                                  verb=verb, brain=n.brain)
            return "unpaused"
        self.gov.audit.append("channel_reply_not_actionable",
                              notification_id=notification_id, verb=verb,
                              kind=n.kind)
        return "ignored: not actionable"
