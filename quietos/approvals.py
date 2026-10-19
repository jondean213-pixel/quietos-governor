"""Tier 2 approval queue.

Held actions wait for Jon — and only Jon. Approvals expire 2 hours after
the hold is placed; expiry kills the action (it does not queue forever and
does not execute). Denial kills it too. Expired actions never resurrect:
a re-submitted action is a brand-new request.
"""
import time
import uuid

APPROVAL_TTL_SECONDS = 2 * 3600  # Jon's decision, 2026-10-07
APPROVER = "jon"                 # nobody but Jon. No delegates.


class Approval:
    def __init__(self, action, now: float, flags=None):
        self.id = uuid.uuid4().hex[:12]
        self.action = action
        self.held_at = now
        self.expires_at = now + APPROVAL_TTL_SECONDS
        self.status = "held"  # held | approved | denied | expired
        self.decided_by = None
        self.decided_at = None
        # Finding 4 (2026-10-19): flags ride along so batch summaries
        # can surface the riskiest items, not just a count.
        self.flags = list(flags or [])


class ApprovalQueue:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._pending: dict[str, Approval] = {}

    def hold(self, action, flags=None) -> Approval:
        ap = Approval(action, self.clock(), flags=flags)
        self._pending[ap.id] = ap
        return ap

    def _get_live(self, approval_id: str) -> Approval:
        ap = self._pending.get(approval_id)
        if ap is None:
            raise KeyError(f"unknown approval {approval_id}")
        if ap.status != "held":
            raise ValueError(f"approval {approval_id} already {ap.status}")
        if self.clock() >= ap.expires_at:
            ap.status = "expired"
            ap.decided_at = self.clock()
            raise TimeoutError(f"approval {approval_id} expired")
        return ap

    def approve(self, approval_id: str, approver: str) -> Approval:
        if approver != APPROVER:
            raise PermissionError(f"only '{APPROVER}' can approve; got '{approver}'")
        ap = self._get_live(approval_id)
        ap.status = "approved"
        ap.decided_by = approver
        ap.decided_at = self.clock()
        return ap

    def deny(self, approval_id: str, approver: str) -> Approval:
        if approver != APPROVER:
            raise PermissionError(f"only '{APPROVER}' can deny; got '{approver}'")
        ap = self._get_live(approval_id)
        ap.status = "denied"
        ap.decided_by = approver
        ap.decided_at = self.clock()
        return ap

    def find_dead(self, action):
        """The expired twin of this exact request, if one exists.

        Roam's dodge memory (RPG-007): an identical re-submission of an
        action that already expired is not a fresh request.

        Finding 7 (2026-10-19): the signature was byte-exact — one
        trailing space made it a "brand-new request." Now normalized
        (case/whitespace) before comparing.
        """
        import re
        def _norm(s):
            s = str(s or "")
            s = re.sub(r"\s+", " ", s).strip().lower()
            return s
        sig = (_norm(action.actor), _norm(action.kind), _norm(action.detail))
        for ap in self._pending.values():
            if ap.status == "expired" and \
                    (_norm(ap.action.actor), _norm(ap.action.kind),
                     _norm(ap.action.detail)) == sig:
                return ap
        return None

    def lookup(self, approval_id: str):
        """The Approval object, decided or not — for rendering, not deciding."""
        return self._pending.get(approval_id)

    def sweep_expired(self) -> list[Approval]:
        """Expire everything past its TTL. Returns the newly-expired."""
        now = self.clock()
        expired = []
        for ap in self._pending.values():
            if ap.status == "held" and now >= ap.expires_at:
                ap.status = "expired"
                ap.decided_at = now
                expired.append(ap)
        return expired

    def pending(self) -> list[Approval]:
        self.sweep_expired()
        return [ap for ap in self._pending.values() if ap.status == "held"]
