"""The brain harness — the joint between a brain and the governor.

A brain is any callable brain(context) -> str that returns ONE JSON
object per turn: {"tool": "<name>", "args": {...}} to act, or
{"final": "<answer>"} to finish. The harness is the ONLY path from
brain output to action:

  * It STAMPS actor identity itself. Whatever the brain's JSON claims
    ("actor": "jon", "claimed_authority": "jon_direct"), the Action
    that reaches the governor carries the harness's actor and the
    default claimed_authority "brain". Claims are dropped, not
    forwarded. (Boundary recorded in the brain-matchup wave, QBM-009:
    identity cannot be self-claimed at the governor API, so the
    harness owns it.)
  * Every tool call goes through Governor.propose(). Only an
    "allowed" verdict executes; "held"/"blocked"/"rejected" stop the
    run and surface to the caller. Nothing reaches the brain except
    results of executed, allowed calls.
  * Malformed brain output is an error, never a governor call.

ToolBackend below is a BENCH FAKE: canned deterministic Gmail/Drive/
web data so harness behavior can be proven against the real governor
without touching Jon's real accounts. The real Gmail/Drive wiring is a
separate phone-side adapter that will expose the same method names.
"""
import json

from .tiers import Action

_SNIPPET = 200  # brain-output snippet kept in audit/log on bad output


def _render_detail(tool: str, args: dict) -> str:
    """Compact, card-readable rendering of a proposed tool call."""
    try:
        blob = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = str(args)
    return f"{tool}({blob})"


class ToolBackend:
    """Bench-fake tool implementations with canned deterministic data.

    NOT wired to real Gmail/Drive — a stand-in so the harness and
    governor can be exercised end-to-end on the bench. Every executed
    call is recorded in `calls` as (tool_name, args_dict); drafts land
    in `drafts`.
    """

    _INBOX = [
        {"id": "m1", "from": "Noah Dean <noah@example.com>",
         "subject": "Cruise deposit question",
         "body": "Hi Jon — when is the deposit due for our June cruise? "
                 "Also, can we add the drink package?"},
        {"id": "m2", "from": "Vanessa Brzeski <vanessa@example.com>",
         "subject": "Itinerary draft looks great",
         "body": "The Eastern Caribbean itinerary looks great. "
                 "One question about the Port Canaveral parking."},
        {"id": "m3", "from": "Cabins Team <groups@example.com>",
         "subject": "Star of the Seas group block",
         "body": "Your 8-cabin block request is in the queue. "
                 "Rates follow by email."},
    ]

    _DRIVE = {
        "Client Manifest Q3.txt": "Client manifest Q3: 14 travelers, "
                                  "2 groups, deposits tracked.",
        "Itinerary-2026.pdf": "[pdf bytes] Eastern Caribbean itinerary, "
                              "Star of the Seas, June 13 2027.",
    }

    def __init__(self):
        self.calls: list = []    # (tool_name, args) for every executed call
        self.drafts: list = []   # recorded gmail_draft payloads

    # -- Tier 1 allowlist tools (names fixed by tiers.py) ------------
    def gmail_search(self, query="", **_):
        self.calls.append(("gmail_search", {"query": query}))
        q = str(query).lower()
        hits = [m for m in self._INBOX
                if q in (m["from"] + m["subject"] + m["body"]).lower()]
        if not hits:
            return f"gmail_search: 0 results for '{query}'"
        lines = [f"[{m['id']}] {m['from']} — {m['subject']}" for m in hits]
        return f"gmail_search: {len(hits)} result(s) for '{query}':\n" + \
            "\n".join(lines)

    def gmail_read(self, id="", **_):
        self.calls.append(("gmail_read", {"id": id}))
        for m in self._INBOX:
            if m["id"] == id:
                return (f"From: {m['from']}\nSubject: {m['subject']}\n\n"
                        f"{m['body']}")
        return f"gmail_read: no email with id '{id}'"

    def gmail_draft(self, to="", subject="", body="", **_):
        args = {"to": to, "subject": subject, "body": body}
        self.calls.append(("gmail_draft", dict(args)))
        draft = dict(args)
        self.drafts.append(draft)
        return f"gmail_draft: draft #{len(self.drafts)} recorded " \
               f"(to={to!r}, subject={subject!r})"

    def drive_list(self, folder="", **_):
        self.calls.append(("drive_list", {"folder": folder}))
        names = ", ".join(sorted(self._DRIVE))
        return f"drive_list: {len(self._DRIVE)} file(s): {names}"

    def drive_read(self, path="", **_):
        self.calls.append(("drive_read", {"path": path}))
        if path in self._DRIVE:
            return f"drive_read '{path}': {self._DRIVE[path]}"
        return f"drive_read: no file named '{path}'"

    def web_search(self, query="", **_):
        self.calls.append(("web_search", {"query": query}))
        return (f"web_search results for '{query}': "
                "1) Star of the Seas sailing dates (example result); "
                "2) Port Canaveral parking rates (example result)")


class BrainHarness:
    """Feeds one brain's output through the governor, turn by turn."""

    def __init__(self, governor, backend, actor="gemma", max_turns=12):
        self.governor = governor
        self.backend = backend
        self.actor = actor          # stamped on EVERY Action; non-negotiable
        self.max_turns = max_turns

    def _bad_output(self, raw, why, log, turn):
        snippet = raw[:_SNIPPET] if isinstance(raw, str) else repr(raw)[:_SNIPPET]
        self.governor.audit.append("harness_bad_brain_output",
                                   actor=self.actor, reason=why,
                                   output=snippet)
        log.append({"turn": turn, "error": why, "output": snippet})
        return {"status": "error", "error": why, "turns": turn, "log": log}

    def run(self, task: str, brain) -> dict:
        """Run one task. Returns {status, turns, log, ...}:
        answered (answer), held (approval_id), blocked, rejected
        (reason), error (error), turn_cap."""
        context = [{"task": task}]
        log: list = []
        turn = 0
        while turn < self.max_turns:
            turn += 1
            try:
                raw = brain(context)
            except Exception as exc:  # a crashing brain is a bad output
                return self._bad_output(f"<brain raised {exc!r}>",
                                        f"brain raised: {exc}", log, turn)
            try:
                msg = json.loads(raw)
            except (TypeError, ValueError):
                return self._bad_output(raw, "unparseable JSON", log, turn)
            if not isinstance(msg, dict):
                return self._bad_output(raw, "output is not a JSON object",
                                        log, turn)

            if isinstance(msg.get("final"), str):
                log.append({"turn": turn, "final": msg["final"]})
                return {"status": "answered", "answer": msg["final"],
                        "turns": turn, "log": log}

            tool = msg.get("tool")
            if not isinstance(tool, str) or not tool:
                return self._bad_output(raw,
                                        "object has neither 'tool' nor 'final'",
                                        log, turn)
            args = msg.get("args") or {}
            if not isinstance(args, dict):
                return self._bad_output(raw, "'args' is not an object",
                                        log, turn)

            # Identity is stamped HERE. Any "actor"/"claimed_authority"
            # in the brain's JSON is ignored — dropped, not forwarded.
            action = Action(actor=self.actor, kind="tool_call", tool=tool,
                            detail=_render_detail(tool, args))
            decision = self.governor.propose(action)

            if decision.verdict == "allowed":
                method = getattr(self.backend, tool, None)
                if method is None:
                    self.governor.audit.append(
                        "harness_backend_missing", actor=self.actor, tool=tool)
                    log.append({"turn": turn, "tool": tool,
                                "error": f"backend has no tool '{tool}'"})
                    return {"status": "error",
                            "error": f"backend has no tool '{tool}'",
                            "turns": turn, "log": log}
                result = str(method(**args))
                context.append({"tool_result": result})
                log.append({"turn": turn, "tool": tool, "verdict": "allowed",
                            "result_preview": result[:120]})
                continue

            if decision.verdict == "held":
                log.append({"turn": turn, "tool": tool, "verdict": "held",
                            "approval_id": decision.approval.id
                            if decision.approval else None})
                return {"status": "held", "turns": turn, "log": log,
                        "approval_id": decision.approval.id
                        if decision.approval else None,
                        "reason": decision.reason}

            if decision.verdict == "blocked":
                log.append({"turn": turn, "tool": tool, "verdict": "blocked",
                            "flags": decision.flags or []})
                return {"status": "blocked", "turns": turn, "log": log,
                        "flags": decision.flags or [],
                        "reason": decision.reason}

            # rejected (rate limit / paused) or anything unexpected:
            # surface it verbatim and stop. Nothing executed.
            log.append({"turn": turn, "tool": tool,
                        "verdict": decision.verdict,
                        "reason": decision.reason})
            return {"status": decision.verdict, "turns": turn, "log": log,
                    "reason": decision.reason}

        log.append({"turn": turn, "error": "turn cap reached"})
        return {"status": "turn_cap", "turns": turn, "log": log}
