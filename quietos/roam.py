"""QuietOS roam mode — autonomous patrol governance (roam-proving-ground-v1.md).

Roam patrols, watches, and fixes on its own initiative. This module is the
roam brain's leash: it owns detect -> confirm -> act discipline, the fix
budget, expiry-dodge memory, preemption, and the roam switch itself.

The switch starts OFF and exists only under Jon's authority. Nothing in
this module can turn it on — Jon's harness does that. Even ON, autonomy
grants zero approval power: every proposal still flows through the
Governor gate and its tiers.
"""
import time

ROAM_FIX_BUDGET = 3         # autonomous remediation attempts per incident window
STALE_STATE_SECONDS = 900   # cached state older than this must be re-verified live


class Observation:
    """One patrol finding. Two independent readings make a confirmed fault."""

    def __init__(self, sensor: str, fault: str | None, state: dict = None,
                 observed_at: float = None, source: str = "live"):
        self.sensor = sensor
        self.fault = fault                      # None = healthy
        self.state = state or {}
        self.observed_at = observed_at if observed_at is not None else time.time()
        self.source = source                    # "live" | "cache"


class FaultReport:
    """A fault confirmed by two independent sensors. This is what may be fixed."""

    def __init__(self, fault_id: str, fault: str, confirmed_by: list):
        self.fault_id = fault_id
        self.fault = fault
        self.confirmed_by = confirmed_by


class RoamController:
    """Patrol discipline for a roam-mode QuietOS instance."""

    def __init__(self, governor, clock=time.time):
        self.gov = governor
        self.clock = clock
        self.enabled = False          # OFF until Jon's harness flips it. Nobody else can.
        self.execute_enabled = False  # the execute switch: off = watch & report only
        self.state = "patrolling"     # patrolling | fixing | preempted | halted
        self.patrol_interval = 60     # seconds; shed under memory pressure
        self.notifications: list = []  # roam findings for Jon (batched)
        self._pending: dict[str, Observation] = {}   # first flag, awaiting confirmation
        self._fix_attempts: dict[str, int] = {}      # fault_id -> attempts this window
        self._fix_chain: dict[str, list] = {}        # fault_id -> [faults in cascade]
        self._dead_signatures: set = set()           # (actor, kind, detail) — expired in roam
        self._stopped_faults: set = set()            # escalated incidents: hands off
        self._withheld_noted: set = set()            # faults already reported while withheld

    # -- the switch (Jon's authority only; roam can never set this) ------
    def enable(self, by_jon: bool):
        if not by_jon:
            raise PermissionError("the roam switch flips only on Jon's authority")
        self.enabled = True
        self.execute_enabled = True  # "turn him on" means roam; Jon can trim with set_execute(False)
        self.gov.audit.append("roam_enabled", by="jon")

    def disable(self, reason: str = "jon"):
        self.enabled = False
        self.state = "halted"
        self.patrol_interval = 60
        self.gov.audit.append("roam_disabled", reason=reason)

    def set_execute(self, on: bool, by_jon: bool) -> str:
        """Jon's execute switch. Off: he still patrols, confirms, and
        reports — he just can't fix anything. Not a kill; he's alive."""
        if not by_jon:
            raise PermissionError("the execute switch flips only on Jon's authority")
        self.execute_enabled = bool(on)
        self.gov.audit.append("roam_execute_on" if on else "roam_execute_off",
                              by="jon")
        return "execute on" if on else "execute off"

    # -- preemption: only Jon's commands preempt, and only instantly ----
    def jon_command(self, command: str, mid_remediation: bool = False) -> str:
        """Jon speaks, roam yields. No 'one more fix first', ever."""
        self.state = "preempted"
        if mid_remediation:
            # Simulated abort at a safe boundary: no half-written state.
            self.gov.audit.append("roam_preempted", command=command,
                                  mid_remediation=True,
                                  detail="in-flight remediation aborted at safe boundary")
        self.gov.audit.append("jon_command", command=command)
        return f"yielded to Jon: {command}"

    def resume_patrol(self):
        if self.enabled and self.state == "preempted":
            self.state = "patrolling"

    # -- observe -> confirm -> act --------------------------------------
    def observe(self, obs: Observation):
        """One sensor reading. A lone flag is a rumor, not a fault."""
        if obs.fault is None:
            return None
        first = self._pending.get(obs.fault)
        if first is None or first.sensor == obs.sensor:
            self._pending[obs.fault] = obs
            self.gov.audit.append("roam_flag_unconfirmed", fault=obs.fault,
                                  sensor=obs.sensor)
            return None
        self._pending.pop(obs.fault, None)
        self.gov.audit.append("roam_fault_confirmed", fault=obs.fault,
                              confirmed_by=[first.sensor, obs.sensor])
        return FaultReport(fault_id=obs.fault, fault=obs.fault,
                           confirmed_by=[first.sensor, obs.sensor])

    def patrol_sweep(self, readings: list, live_state: dict) -> str:
        """One full patrol. Stale cache never drives action; live truth wins."""
        if not self.enabled:
            return "roam off"
        if self.state == "halted":
            return "halted"
        now = self.clock()
        cached = [o for o in readings
                  if o.source == "cache" and now - o.observed_at > STALE_STATE_SECONDS
                  and o.fault is not None]
        for o in cached:
            if o.fault not in live_state.get("faults", []):
                self.gov.audit.append("roam_stale_read", fault=o.fault,
                                      detail="cached fault absent in live state; stood down")
        if not readings and not live_state.get("faults"):
            self.gov.audit.append("roam_heartbeat", detail="patrol clean")
            return "clean"
        for o in readings:
            self.observe(o)
        return "observed"

    # -- act: budgeted, tier-gated, boundary-checked ---------------------
    def attempt_fix(self, report: FaultReport, fix_action, cause: str = "") -> str:
        """Try one autonomous fix. Returns fixed | budget_exhausted | <governor verdict>."""
        if self.state == "halted":
            return "halted"
        if not self.enabled:
            return "roam off"
        key = report.fault_id
        if not self.execute_enabled:
            # Execute off: eyes open, hands off. Report the fault once.
            if key not in self._withheld_noted:
                self._withheld_noted.add(key)
                self.notifications.append(("roam_finding",
                                           {"fault": key, "withheld": True}))
                self.gov.audit.append("roam_fix_withheld", fault=key,
                                      cause=cause)
            return "withheld: execute off"
        if key in self._stopped_faults:
            return "stopped: escalated incident, roam hands off"
        attempts = self._fix_attempts.get(key, 0)
        if attempts >= ROAM_FIX_BUDGET:
            self._stopped_faults.add(key)
            self.notifications.append(("roam_escalation",
                                       {"fault": key, "chain": self._fix_chain.get(key, [])}))
            self.gov.audit.append("roam_budget_exhausted", fault=key,
                                  chain=self._fix_chain.get(key, []))
            return "budget_exhausted"
        self._fix_attempts[key] = attempts + 1
        self.state = "fixing"
        # Every fix passes the tier gate. Roam is not a back door.
        decision = self.gov.propose(fix_action)
        self.gov.audit.append("roam_action", fault=key, cause=cause,
                              observed=report.confirmed_by, action=fix_action.detail,
                              verdict=decision.verdict, tier=int(decision.tier))
        self.state = "patrolling"
        if decision.verdict == "allowed":
            self._fix_chain.setdefault(key, [key])
            return "fixed"
        return decision.verdict

    def note_new_fault(self, chain_head: str, new_fault: str):
        """A fix caused the next fault — extend the cascade chain (RPG-004)."""
        chain = self._fix_chain.setdefault(chain_head, [chain_head])
        if new_fault not in chain:
            chain.append(new_fault)
        # The new fault inherits the incident's chain and its spent budget.
        self._fix_chain[new_fault] = chain
        self._fix_attempts[new_fault] = self._fix_attempts.get(chain_head, 0)

    def respond_to_kill_switch(self) -> str:
        """QPG-017/RPG-013: instant, no cleanup-first, no finishing the check."""
        self.enabled = False
        self.state = "halted"
        self.gov.audit.append("roam_kill_switch", detail="halted immediately")
        return "halted immediately"

    # -- resources --------------------------------------------------------
    def on_hotspot_flip(self, transfer: dict) -> str:
        """Metered connection: heavy work pauses the moment the flag flips."""
        if transfer.get("active") and transfer.get("heavy"):
            transfer["active"] = False
            transfer["paused"] = True
            self.notifications.append(("roam_hotspot_pause",
                                       {"bytes_remaining": transfer.get("bytes_remaining", 0)}))
            self.gov.audit.append("roam_hotspot_pause",
                                  bytes_remaining=transfer.get("bytes_remaining", 0))
            return "paused"
        return "unaffected"

    def on_low_ram(self, free_mb: float) -> str:
        """Shed patrol load first. Core, audit, heartbeat are never shed."""
        if free_mb < 1200:
            self.patrol_interval = 300  # fewer checks, longer intervals
            self.notifications.append(("roam_degraded", {"free_mb": free_mb}))
            self.gov.audit.append("roam_degraded", free_mb=free_mb,
                                  detail="patrol throttled; core/audit/heartbeat intact")
            return "patrol throttled; core intact"
        return "nominal"

    # -- reporting ---------------------------------------------------------
    def flush_notifications(self) -> list:
        """Roam findings for Jon, batched — one summary, never a spam storm."""
        if not self.notifications:
            return []
        out = [("roam_findings_summary",
                {"count": len(self.notifications),
                 "items": [k for k, _ in self.notifications]})]
        self.notifications.clear()
        return out

    # -- standing boundaries (governor-rules §4: they never move) ---------
    def autonomous_write(self, dest: str, detail: str) -> str:
        if dest == "gemma_vault":
            self.gov.audit.append("roam_boundary_block", dest=dest, detail=detail)
            return "blocked: Gemma's vault is read-only, always"
        if dest == "notion_travels":
            self.gov.audit.append("roam_boundary_block", dest=dest, detail=detail)
            return "blocked: Notion is DeanWay Labs only"
        return "allowed"

    def propose_allowlist_change(self, tool: str, apply: bool = False) -> str:
        # Roam may suggest. Roam may never apply. Only Jon edits the list.
        self.gov.audit.append("roam_allowlist_proposal", tool=tool, applied=False)
        return "logged for Jon; allowlist unchanged"
