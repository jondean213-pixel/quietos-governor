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
    def __init__(self, audit_path: str | None = None, clock=None):
        import time
        import os
        self.clock = clock or time.time
        self.roam_enabled = False  # Jon's harness flips this; off by default
        if audit_path is None:
            # Zero-arg quick-start: audit to the user's home dir.
            audit_path = os.path.join(os.path.expanduser("~"), ".quietos", "audit.log")
        self.audit = AuditLog(audit_path)
        self.approvals = ApprovalQueue(clock=self.clock)
        self.limiter = RateLimiter(clock=self.clock)
        self.batcher = EscalationBatcher(clock=self.clock)
        self.heartbeat = GovernorHeartbeat(clock=self.clock)
        self.jon_notifications: list = []  # (kind, payload) — phone delivers these

    # Command substrings that read as destructive intent. The brain never
    # runs these — they classify Tier 3 outright (cf. the runner's
    # ALLOW_DESTRUCTIVE gate).
    _DESTRUCTIVE_HINTS = ("rm -rf", "rm -r ", "del ", "delete", "format",
                          "mkfs", "dd if=")

    def evaluate(self, command: str) -> Decision:
        """Quick-start convenience: propose a raw shell command string and
        get back a Decision. Destructive-looking commands land in Tier 3
        (blocked); everything else flows through propose() honestly."""
        lowered = command.lower()
        if any(h in lowered for h in self._DESTRUCTIVE_HINTS):
            action = Action(actor="brain", kind="destructive",
                            detail=command, target=command)
        else:
            action = Action(actor="brain", kind="tool_call", tool="run_shell",
                            detail=command, target=command)
        return self.propose(action)

    # -- the gate ------------------------------------------------------
    def _targets_audit_trail(self, action: Action) -> bool:
        """Delete of the audit log — or of ANY folder containing it.

        classify() blocks targets naming 'audit'; this closes the walk-
        around: deleting the audit's parent directory destroys the
        trail just as dead without ever naming it. That's tampering.
        """
        import os
        if action.kind != "delete" or not action.target:
            return False
        audit = os.path.normpath(os.path.expanduser(self.audit.path))
        target = os.path.normpath(os.path.expanduser(action.target))
        return audit == target or audit.startswith(target.rstrip(os.sep) + os.sep)

    def propose(self, action: Action) -> Decision:
        if self._targets_audit_trail(action):
            d = Decision("blocked", Tier.BLOCK,
                         "tier 3: deleting the audit trail or its home "
                         "is tampering", flags=["audit_path_delete_attempt"])
            self._log(action, d)
            self.jon_notifications.append(
                ("blocked", {"detail": action.detail, "flags": d.flags}))
            return d
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
            self._flush_batch()
            ap = self.approvals.hold(action)
            mode, ids = self.batcher.add(ap.id)
            batched = mode != "single"
            if mode == "batch":
                self.jon_notifications.append(("approval_batch", {"ids": ids, "count": len(ids)}))
            elif mode == "single":
                self.jon_notifications.append(("approval_needed",
                                               {"id": ap.id, "detail": action.detail,
                                                "flags": flags}))
            # "folded": silent — surfaces in the one window summary.
            d = Decision("held", tier, f"tier 2: held for Jon (approval {ap.id})",
                         approval=ap, flags=flags, batched=batched)
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

    def _flush_batch(self):
        """Release folded Tier-2 ids as one summary once their window
        closes, so Jon sees every ask — batched, never drip-fed.

        Only still-held ids are named: an ask Jon already decided one
        by one must not come back at him in the summary as if it were
        still his to answer. If every folded id is already decided,
        nothing is sent and the dropped ids are audited instead."""
        ids = self.batcher.flush_expired()
        if not ids:
            return
        live = [aid for aid in ids
                if (ap := self.approvals.lookup(aid)) is not None
                and ap.status == "held"]
        dropped = [aid for aid in ids if aid not in live]
        if dropped:
            self.audit.append("approval_batch_filtered",
                              flushed=len(ids), dropped=dropped)
        if live:
            self.jon_notifications.append(
                ("approval_batch", {"ids": live, "count": len(live)}))

    def sweep(self):
        for ap in self.approvals.sweep_expired():
            self.audit.append("approval_decision", approval_id=ap.id,
                              decision="expired", by="system")
        self._flush_batch()
