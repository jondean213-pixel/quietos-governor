"""Blast-radius limits (governor-rules-v1.md §7).

Tier 1 allows routine work — not unlimited routine work. A brain gone
sideways trips these breakers: it gets paused and Jon gets told.
Normal work never trips them.
"""
import time
from collections import deque

CALLS_PER_MINUTE = 30       # v1 default, tunable
DRAFT_CAP = 10              # max unapproved drafts before pause
ESCALATION_BATCH_WINDOW = 600   # seconds
ESCALATION_BATCH_COUNT = 5      # >5 Tier-2 asks in 10 min -> one batched ping


class RateLimiter:
    def __init__(self, clock=time.time):
        self.clock = clock
        self._calls: dict[str, deque] = {}   # brain -> timestamps
        self._drafts: dict[str, int] = {}    # brain -> draft count
        self._paused: set[str] = set()

    def _window(self, brain: str) -> deque:
        now = self.clock()
        dq = self._calls.setdefault(brain, deque())
        while dq and dq[0] <= now - 60:
            dq.popleft()
        return dq

    def check_call(self, brain: str) -> tuple[bool, str]:
        """Returns (ok, reason). False means the breaker tripped."""
        if brain in self._paused:
            return False, "brain paused by governor"
        dq = self._window(brain)
        if len(dq) >= CALLS_PER_MINUTE:
            self._paused.add(brain)
            return False, f"call flood: {len(dq)} calls in 60s (cap {CALLS_PER_MINUTE})"
        dq.append(self.clock())
        return True, "ok"

    def check_draft(self, brain: str) -> tuple[bool, str]:
        if brain in self._paused:
            return False, "brain paused by governor"
        n = self._drafts.get(brain, 0)
        if n >= DRAFT_CAP:
            self._paused.add(brain)
            return False, f"draft flood: {n} drafts (cap {DRAFT_CAP})"
        self._drafts[brain] = n + 1
        return True, "ok"

    def is_paused(self, brain: str) -> bool:
        return brain in self._paused

    def unpause(self, brain: str):
        self._paused.discard(brain)
        self._calls.pop(brain, None)
        self._drafts.pop(brain, None)


class EscalationBatcher:
    """Batches rapid Tier-2 requests into one summary ping for Jon."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self._recent: deque = deque()  # (timestamp, approval_id)

    def add(self, approval_id: str) -> tuple[bool, list]:
        """Returns (should_batch, batched_ids)."""
        now = self.clock()
        while self._recent and self._recent[0][0] <= now - ESCALATION_BATCH_WINDOW:
            self._recent.popleft()
        self._recent.append((now, approval_id))
        if len(self._recent) > ESCALATION_BATCH_COUNT:
            ids = [aid for _, aid in self._recent]
            self._recent.clear()
            return True, ids
        return False, []
