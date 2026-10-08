"""The QuietOS governor gate.

Every proposed action flows through Governor.propose():

  1. Dead-man check — a suspended/paused brain proposes nothing.
  2. Rate-limit check — floods trip the breaker.
  3. Tier classification — allow / escalate / block.
  4. Tier 1 -> execute (simulated here; the harness executes on-device).
     Tier 2 -> hold in the approval queue, notify Jon (batched if storming).
     Tier 3 -> refuse, log, alert Jon.
  5. Every decision is written to the append-only audit log.

Decisions are values, not side effects: this module never sends, deletes,
or publishes anything itself. On the phone, the harness performs the
Tier 1 execution after the governor allows it.
"""
from dataclasses import dataclass
from typing import Optional

from .tiers import Tier, Action, classify
from .audit import AuditLog
from .approvals import ApprovalQueue, Approval
from .ratelimit import RateLimiter, EscalationBatcher
from .lifecycle import GovernorHeartbeat


@dataclass
class Decision:
    verdict: str              # "allowed" | "held" | "blocked" | "rejected"
    tier: Tier
    reason: str
    approval: Optional[Approval] = None
    flags: list = None
    batched: bool = False


class Governor:
    def __init__(self, audit_path: str, clock=None):
        import time
        self.clock = clock or time.time
        self.roam_enabled = False  # Jon's harness flips this; off by default
        self.audit = AuditLog(audit_path)
        self.approvals = ApprovalQueue(clock=self.clock)
        self.limiter = RateLimiter(clock=self.clock)
        self.batcher = EscalationBatcher(clock=self.clock)
        self.heartbeat = GovernorHeartbeat(clock=self.clock)
        self.jon_notifications: list = []  # (kind, payload) — phone delivers these

    # -- the gate ------------------------------------------------------
    def propose(self, action: Action) -> Decision:
        tier, flags = classify(action)

        # 1. paused brains propose nothing
        if self.limiter.is_paused(action.actor):
            d = Decision("rejected", tier, "brain paused by governor", flags=flags)
            self._log(action, d)
            return d

        # 2. rate limits (Tier 1 tool calls and drafts)
        if action.kind == "tool_call":
            ok, why = self.limiter.check_call(action.actor)
            if not ok:
                d = Decision("rejected", tier, why, flags=flags)
                self._log(action, d)
                self.jon_notifications.append(("brain_paused", {"brain": action.actor, "reason": why}))
                return d
        # A draft is a draft however it arrives — gmail_draft tool calls count
        # toward the same cap as explicit draft actions.
        if action.kind == "draft" or (action.kind == "tool_call" and action.tool == "gmail_draft"):
            ok, why = self.limiter.check_draft(action.actor)
            if not ok:
                d = Decision("rejected", tier, why, flags=flags)
                self._log(action, d)
                self.jon_notifications.append(("brain_paused", {"brain": action.actor, "reason": why}))
                return d

        # 3-4. tier handling
        if tier == Tier.ALLOW:
            d = Decision("allowed", tier, "tier 1: routine, executing", flags=flags)
            self._log(action, d)
            return d

        if tier == Tier.ESCALATE:
            # RPG-007: in roam mode, re-submitting an action that already
            # expired is a dodge, not a fresh request. It stays dead.
            if self.roam_enabled:
                dead = self.approvals.find_dead(action)
                if dead is not None:
                    self.audit.append("expiry_dodge", actor=action.actor,
                                      kind=action.kind, detail=action.detail,
                                      dead_approval=dead.id)
                    d = Decision("held", tier,
                                 "roam expiry dodge: identical request already "
                                 "expired; stays dead until Jon acts",
                                 approval=dead, flags=flags + ["expiry_dodge"])
                    self._log(action, d, approval_id=dead.id)
                    return d
            ap = self.approvals.hold(action)
            batch, ids = self.batcher.add(ap.id)
            if batch:
                self.jon_notifications.append(("approval_batch", {"ids": ids, "count": len(ids)}))
            else:
                self.jon_notifications.append(("approval_needed",
                                               {"id": ap.id, "detail": action.detail,
                                                "flags": flags}))
            d = Decision("held", tier, f"tier 2: held for Jon (approval {ap.id})",
                         approval=ap, flags=flags, batched=batch)
            self._log(action, d, approval_id=ap.id)
            return d

        # Tier.BLOCK
        d = Decision("blocked", tier, "tier 3: refused outright", flags=flags)
        self._log(action, d)
        self.jon_notifications.append(("blocked",
                                       {"detail": action.detail, "flags": flags}))
        return d

    def _log(self, action: Action, decision: Decision, **extra):
        self.audit.append(
            "decision",
            actor=action.actor, kind=action.kind, tool=action.tool,
            target=action.target, detail=action.detail,
            claimed_authority=action.claimed_authority,
            tier=int(decision.tier), verdict=decision.verdict,
            reason=decision.reason, flags=decision.flags or [],
            **extra)

    # -- Jon's approval channel (the ONLY authority that counts) --------
    def report_to_jon(self, title: str, body: str):
        """Queue a result report for Jon's phone.

        Evidence, not acknowledgment: the caller builds `body` from the
        audit (what was done, how it verified). Jon's standard
        (2026-10-08): "I need to know if it was fixed not just a got it
        and what it did to fix it."
        """
        self.jon_notifications.append(("report", {"title": title, "body": body}))

    def jon_approve(self, approval_id: str) -> Approval:
        ap = self.approvals.approve(approval_id, "jon")
        self.audit.append("approval_decision", approval_id=approval_id,
                          decision="approved", by="jon")
        return ap

    def jon_deny(self, approval_id: str) -> Approval:
        ap = self.approvals.deny(approval_id, "jon")
        self.audit.append("approval_decision", approval_id=approval_id,
                          decision="denied", by="jon")
        return ap

    def jon_unpause(self, brain: str):
        """Jon releases a governor-paused brain after reviewing the trip."""
        self.limiter.unpause(brain)
        self.audit.append("brain_unpaused", brain=brain, by="jon")

    def sweep(self):
        for ap in self.approvals.sweep_expired():
            self.audit.append("approval_decision", approval_id=ap.id,
                              decision="expired", by="system")
