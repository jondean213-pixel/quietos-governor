"""Brain lifecycle governance (governor-rules-v1.md §2-3).

One brain resident at a time on the 4GB Motorola. QuietOS owns load /
unload / health / kill. Includes the dead-man's switch: brains check the
governor's heartbeat, and suspend themselves if it's been silent too long.
"""
import time

MIN_FREE_RAM_MB = 1200          # below this, refuse the load — no silent crash
GOVERNOR_HEARTBEAT_TTL = 60     # brain suspends if governor silent this long
MAX_RESTARTS = 3


def ram_check(free_mb: float, required_mb: float = MIN_FREE_RAM_MB) -> tuple[bool, str]:
    if free_mb < required_mb:
        return False, (f"load refused: {free_mb:.0f}MB free, need {required_mb:.0f}MB "
                       f"— refusing instead of crashing mid-load")
    return True, f"ok: {free_mb:.0f}MB free"


class GovernorHeartbeat:
    """The governor stamps this; brains watch it."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.last_beat = clock()
        self.alive = True

    def beat(self):
        self.last_beat = self.clock()

    def kill(self):
        """Simulate governor death (proving ground)."""
        self.alive = False

    def check(self, brain_clock=None) -> tuple[bool, str]:
        now = (brain_clock or self.clock)()
        if not self.alive:
            return False, "governor dead"
        if now - self.last_beat > GOVERNOR_HEARTBEAT_TTL:
            return False, f"governor silent {now - self.last_beat:.0f}s"
        return True, "governor alive"


class BrainSupervisor:
    """Tracks one resident brain: restarts on crash, then escalates."""

    def __init__(self):
        self.resident: str | None = None
        self.restarts = 0
        self.killed = False

    def load(self, brain_id: str, free_mb: float) -> tuple[bool, str]:
        ok, reason = ram_check(free_mb)
        if not ok:
            return False, reason
        if self.resident and self.resident != brain_id:
            self.unload(self.resident)
        self.resident = brain_id
        self.restarts = 0
        self.killed = False
        return True, f"{brain_id} resident"

    def unload(self, brain_id: str):
        if self.resident == brain_id:
            self.resident = None

    def on_crash(self) -> tuple[str, str]:
        """Returns (action, reason): restart | escalate."""
        self.restarts += 1
        if self.restarts <= MAX_RESTARTS:
            return "restart", f"attempt {self.restarts}/{MAX_RESTARTS}"
        return "escalate", f"{MAX_RESTARTS} restarts failed — telling Jon"

    def kill_switch(self) -> str:
        self.killed = True
        self.resident = None
        return "halted immediately"
