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
  * An exact repeat of a call already executed this run is answered
    from the harness's cache after the governor allows it — the
    backend is not re-run (v2.0 no-repeat guard, harness_repeat_call).
  * Backend results speak the brain's trained dialect (v2.1 bilingual
    room): JSON objects with labeled "id" fields, tokenized multi-word
    search, and read errors that name the valid ids. Her classroom
    (DW-TOOL) graded full-phrase queries and fed her structured
    returns with labeled ids; the old flattened-prose results left
    her unable to bind an id at all (phone evidence, 2026-10-08).
    The room meets her dialect while the drills teach her the room.

ToolBackend below is a BENCH FAKE: canned deterministic Gmail/Drive/
web data so harness behavior can be proven against the real governor
without touching Jon's real accounts. The real Gmail/Drive wiring is a
separate phone-side adapter that will expose the same method names.
"""
import json
import re

from .tiers import Action

_SNIPPET = 200  # brain-output snippet kept in audit/log on bad output


# ---------------------------------------------------------------- grounding
# The grounding guard (v1.9): a MECHANICAL check on the brain's final
# answer. Governance judges authority, not truth — so the harness, which
# sees both the results the brain was shown and the answer it gives,
# flags factual claims (numbers, dates) that appear in the answer but
# NOWHERE in the evidence. It flags; it does not censor: the answer
# still completes the run, the flags ride along in the result, and an
# audit event records them. Born of evidence (Jon's phone, 2026-10-08):
# shown an email that only ASKED "when is the deposit due", the real
# brain answered "due by the end of this week" — an invented deadline
# no one-shot instruction stopped her from inventing. Invented OFFERS
# ("we can add the drink package") carry no checkable token and are
# deliberately out of scope — drills, not mechanics, carry those.

_MONTH_NUM = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7,
    "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12,
    "december": 12,
}
_MONTH_RE = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
             r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
             r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_WEEKDAYS = {
    "monday": "monday", "mon": "monday", "tuesday": "tuesday",
    "tue": "tuesday", "tues": "tuesday", "wednesday": "wednesday",
    "wed": "wednesday", "thursday": "thursday", "thu": "thursday",
    "thur": "thursday", "thurs": "thursday", "friday": "friday",
    "fri": "friday", "saturday": "saturday", "sat": "saturday",
    "sunday": "sunday", "sun": "sunday",
}
_WEEKDAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tues?|wed|thu(?:rs?)?|fri|sat|sun)\b")

# Relative-date phrases -> coarse class. A claim is grounded when the
# same phrase (or a same-class phrase, or an explicit date) is in the
# evidence; token mechanics only, no calendar math.
_REL_PHRASES = {
    "today": "day", "tomorrow": "day", "tonight": "day",
    "this morning": "day", "this afternoon": "day", "this evening": "day",
    "this week": "week", "next week": "week", "last week": "week",
    "end of this week": "week", "end of the week": "week",
    "beginning of the week": "week", "start of the week": "week",
    "this weekend": "week", "next weekend": "week",
    "this month": "month", "next month": "month",
    "end of the month": "month", "end of this month": "month",
    "this year": "year", "next year": "year",
}
_REL_RE = re.compile(
    r"\b(" + "|".join(re.escape(p) for p in
                      sorted(_REL_PHRASES, key=len, reverse=True)) + r")\b")

_NUMBER_RE = re.compile(
    r"\$?\d[\d,]*(?:\.\d+)?\s*(?:%|percent|dollars?|usd)?", re.IGNORECASE)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_MONTH_DAY_RE = re.compile(
    r"\b(" + _MONTH_RE + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?"
    r"(?:\s*,?\s*(\d{4}))?\b", re.IGNORECASE)
_DAY_MONTH_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(" + _MONTH_RE + r")\.?"
    r"(?:\s*,?\s*(\d{4}))?\b", re.IGNORECASE)


def _num_value(raw: str) -> str:
    """Normalize a numeric token: '$1,200.00' / '50%' / '50 dollars' -> '1200'/'50'."""
    core = re.sub(r"[^\d.]", "", raw)
    try:
        val = float(core)
    except ValueError:
        return raw.strip().lower()
    return str(int(val)) if val == int(val) else str(val)


def extract_claims(text: str) -> dict:
    """Pull checkable claim tokens out of text, three conservative classes.

    Returns {"numbers": [(display, value)], "dates": [(display, (y,m,d))],
    "weekdays": [(display, name)], "rel": [(display, (phrase, class))]}.
    Month names count only inside explicit dates (June 13) — a bare
    "may" is a verb, not a claim. Date claims also surface their day
    number as a number claim (harmless: evidence dates produce the
    same token when grounded)."""
    low = (text or "").lower()
    claims = {"numbers": [], "dates": [], "weekdays": [], "rel": []}
    seen = set()

    def add(kind, item):
        if item not in seen:
            seen.add(item)
            claims[kind].append(item)

    for m in _ISO_DATE_RE.finditer(low):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        add("dates", (m.group(0), (y, mo, d)))
    for m in _SLASH_DATE_RE.finditer(low):
        y = int(m.group(3)) if m.group(3) else 0
        if y and y < 100:
            y += 2000
        add("dates", (m.group(0), (y, int(m.group(1)), int(m.group(2)))))
    for m in _MONTH_DAY_RE.finditer(low):
        mo = _MONTH_NUM[m.group(1).lower().rstrip(".")]
        y = int(m.group(3)) if m.group(3) else 0
        add("dates", (m.group(0), (y, mo, int(m.group(2)))))
    for m in _DAY_MONTH_RE.finditer(low):
        mo = _MONTH_NUM[m.group(2).lower().rstrip(".")]
        y = int(m.group(3)) if m.group(3) else 0
        add("dates", (m.group(0), (y, mo, int(m.group(1)))))
    for m in _WEEKDAY_RE.finditer(low):
        add("weekdays", (m.group(0), _WEEKDAYS[m.group(1)]))
    for m in _REL_RE.finditer(low):
        add("rel", (m.group(0), (m.group(1), _REL_PHRASES[m.group(1)])))
    for m in _NUMBER_RE.finditer(low):
        raw = m.group(0).strip()
        if raw:
            add("numbers", (raw, _num_value(raw)))
    return claims


def check_grounding(answer: str, context: list) -> list:
    """Flag final-answer claims with no source in the run's evidence.

    Evidence = the task text plus every tool_result the brain was shown
    this run. Returns [{"claim": <as written>, "kind": number|date|
    weekday|date_phrase}]. Empty list = nothing ungrounded found."""
    evidence = "\n".join(
        str(e.get("tool_result") or e.get("task") or "") for e in context)
    ev = extract_claims(evidence)
    ev_numbers = {v for _, v in ev["numbers"]}
    ev_dates = {d for _, d in ev["dates"]}
    ev_weekdays = {w for _, w in ev["weekdays"]}
    ev_rel = {p for _, (p, _c) in ev["rel"]}
    ev_rel_classes = {c for _, (_p, c) in ev["rel"]}

    flags = []

    def flag(claim, kind):
        flags.append({"claim": claim, "kind": kind})

    mine = extract_claims(answer)
    for display, value in mine["numbers"]:
        if value not in ev_numbers:
            flag(display, "number")
    for display, ymd in mine["dates"]:
        if ymd not in ev_dates and not (
                ymd[0] == 0
                and any((m, d) == (ymd[1], ymd[2]) for _y, m, d in ev_dates)):
            flag(display, "date")
    for display, name in mine["weekdays"]:
        if name not in ev_weekdays and not ev_dates:
            flag(display, "weekday")
    for display, (phrase, cls) in mine["rel"]:
        if (phrase not in ev_rel and cls not in ev_rel_classes
                and not (cls in ("week", "month", "year") and ev_dates)
                and not (cls == "week" and ev_weekdays)):
            flag(display, "date_phrase")
    return flags


def _render_detail(tool: str, args: dict) -> str:
    """Compact, card-readable rendering of a proposed tool call."""
    try:
        blob = json.dumps(args, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = str(args)
    return f"{tool}({blob})"


# ---------------------------------------------------------------- no-repeat
# The no-repeat guard (v2.0): phone evidence (2026-10-08, five live
# runs) showed the real brain re-proposing BYTE-IDENTICAL calls she had
# already made — same search, same email read up to 3x in one run —
# ignoring results already sitting in her context, while the harness
# dutifully re-executed every repeat. The governor still judges every
# proposal exactly as before (verdicts, rate limits, audit untouched);
# only after an "allowed" verdict does the harness check its per-run
# cache of EXECUTED (tool, canonical args) -> result. A repeat is
# answered from cache: no backend call, and the context entry carries
# the cached result prefixed with a marker saying plainly that this was
# already done. Held/blocked/rejected proposals never touch the cache —
# she cannot earn a result through it that the governor didn't allow.

_REPEAT_MARKER = "[already done — same call as before; result unchanged]"


def _canon_args(args: dict) -> str:
    """Canonical args key: key order and string whitespace normalized,
    so the same call spelled two equivalent ways counts as one call."""
    def norm(value):
        if isinstance(value, str):
            return " ".join(value.split())
        if isinstance(value, dict):
            return {k: norm(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [norm(v) for v in value]
        return value
    try:
        return json.dumps(norm(args), sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(args)


class ToolBackend:
    """Bench-fake tool implementations with canned deterministic data.

    NOT wired to real Gmail/Drive — a stand-in so the harness and
    governor can be exercised end-to-end on the bench. Every executed
    call is recorded in `calls` as (tool_name, args_dict); drafts land
    in `drafts`.

    Results are returned in her trained dialect (v2.1): JSON objects
    with labeled "id" fields (the shape DW-TOOL fed her), tokenized
    multi-word search, and read errors that name the valid ids so a
    bad argument is recoverable instead of terminal. Human-readable
    summary prefixes keep the run logs legible.
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
    _SEARCH_STOPWORDS = {"the", "and", "for", "with", "mail", "email",
                         "about", "from", "that", "this"}

    @classmethod
    def _search_hits(cls, query):
        """v2.1 tokenized search. Her classroom graded full-phrase
        queries PASS, so a phrase like "deposit mail" must work here:
        split the query into words (length >= 3, minus a small
        stopword set) and hit when ANY token substring-matches
        from/subject/body. A single-word query reduces to exactly the
        old literal-substring behavior; when no usable token remains,
        fall back to the literal full-string match."""
        q = str(query)

        def haystack(m):
            return (m["from"] + " " + m["subject"] + " "
                    + m["body"]).lower()

        tokens = [t for t in re.findall(r"[a-z0-9]+", q.lower())
                  if len(t) >= 3 and t not in cls._SEARCH_STOPWORDS]
        if tokens:
            return [m for m in cls._INBOX
                    if any(t in haystack(m) for t in tokens)]
        literal = q.lower()
        return [m for m in cls._INBOX if literal in haystack(m)]

    def gmail_search(self, query="", **_):
        self.calls.append(("gmail_search", {"query": query}))
        hits = self._search_hits(query)
        if not hits:
            return f"gmail_search: 0 results for '{query}'"
        objs = [{"id": m["id"], "from": m["from"], "subject": m["subject"]}
                for m in hits]
        return f"gmail_search: {len(hits)} result(s) for '{query}':\n" \
            + json.dumps(objs)

    def gmail_read(self, id="", **_):
        self.calls.append(("gmail_read", {"id": id}))
        for m in self._INBOX:
            if m["id"] == id:
                return "gmail_read: " + json.dumps(
                    {"id": m["id"], "from": m["from"],
                     "subject": m["subject"], "body": m["body"]})
        valid = ", ".join(m["id"] for m in self._INBOX)
        return (f'no email with id "{id}" — valid ids from search '
                f'results look like: {valid}')

    def gmail_draft(self, to="", subject="", body="", **_):
        args = {"to": to, "subject": subject, "body": body}
        self.calls.append(("gmail_draft", dict(args)))
        draft = dict(args)
        self.drafts.append(draft)
        return f"gmail_draft: draft #{len(self.drafts)} recorded " \
               f"(to={to!r}, subject={subject!r})"

    def _drive_ids(self):
        # Stable labeled ids, in drive_list order: d1, d2, ...
        return {name: f"d{i + 1}" for i, name in enumerate(self._DRIVE)}

    def drive_list(self, folder="", **_):
        self.calls.append(("drive_list", {"folder": folder}))
        ids = self._drive_ids()
        objs = [{"id": ids[name], "name": name} for name in self._DRIVE]
        return f"drive_list: {len(objs)} file(s):\n" + json.dumps(objs)

    def drive_read(self, path="", **kw):
        # Keyed by file name (path); a drive_list id (d1, d2) in the
        # `id` arg resolves too — she may bind whichever labeled
        # handle the room showed her.
        file_id = kw.get("id", "")
        self.calls.append(("drive_read", {"path": path or file_id}))
        ids = self._drive_ids()
        name = path
        if name not in self._DRIVE and file_id:
            name = next((n for n, i in ids.items() if i == file_id),
                        name)
        if name in self._DRIVE:
            return "drive_read: " + json.dumps(
                {"id": ids[name], "name": name,
                 "content": self._DRIVE[name]})
        valid = ", ".join(f"{i} ({n})" for n, i in ids.items())
        return (f'drive_read: no file named "{path or file_id}" — '
                f'valid ids from drive_list look like: {valid}')

    def web_search(self, query="", **_):
        self.calls.append(("web_search", {"query": query}))
        objs = [
            {"id": "w1",
             "title": "Star of the Seas sailing dates (example result)"},
            {"id": "w2",
             "title": "Port Canaveral parking rates (example result)"},
        ]
        return f"web_search: {len(objs)} result(s) for '{query}':\n" \
            + json.dumps(objs)


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
        executed: dict = {}   # no-repeat cache: (tool, canon args) -> result
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
                answer = msg["final"]
                flags = check_grounding(answer, context)
                log.append({"turn": turn, "final": answer})
                if flags:
                    self.governor.audit.append(
                        "harness_grounding_flag", actor=self.actor,
                        claims=[f["claim"] for f in flags])
                    log.append({"turn": turn, "grounding_flags": flags})
                return {"status": "answered", "answer": answer,
                        "turns": turn, "log": log,
                        "grounding_flags": flags}

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
                cache_key = (tool, _canon_args(args))
                if cache_key in executed:
                    # No-repeat guard: the governor allowed it, but this
                    # exact call already executed this run. Answer from
                    # cache; the backend is NOT called again.
                    self.governor.audit.append(
                        "harness_repeat_call", actor=self.actor, tool=tool,
                        args=_render_detail(tool, args))
                    cached = f"{_REPEAT_MARKER}\n{executed[cache_key]}"
                    context.append({"tool_result": cached})
                    log.append({"turn": turn, "tool": tool,
                                "verdict": "allowed (repeat — cached)",
                                "result_preview": cached[:120]})
                    continue
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
                executed[cache_key] = result
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
