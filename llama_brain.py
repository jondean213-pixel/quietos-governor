"""First marriage — a REAL brain through the harness into the governor.

Runs on the PHONE (Termux side). The assistant brain's model server
(./brain.sh assistant, OpenAI-compatible API on 127.0.0.1:8766) speaks;
every word she produces is parsed as ONE JSON object by BrainHarness,
stamped with actor "gemma", and judged by the Governor before anything
executes. Tools run against the harness's practice backend (canned
inbox/Drive) — real Gmail/Drive wiring is a later, deliberate step.

Usage:
  python3 -u sim/llama_brain.py ["task text"]

Env:
  QUIETOS_BRAIN_URL     default http://127.0.0.1:8766
  QUIETOS_PKG_PARENT    where the quietos/ package lives (bundle: ~)
  QUIETOS_MARRIAGE_AUDIT  audit path (default ~/quietos-bridge/)
"""
import json
import os
import re
import sys
import urllib.request

_candidates = [os.environ.get("QUIETOS_PKG_PARENT"),
               os.path.join(os.path.dirname(__file__), "..", "src"),
               os.path.join(os.path.dirname(__file__), "..")]
for _c in _candidates:
    if _c and os.path.isdir(os.path.join(_c, "quietos")):
        sys.path.insert(0, _c)
        break

from quietos import Governor
from quietos.harness import BrainHarness, ToolBackend

BRAIN_URL = os.environ.get("QUIETOS_BRAIN_URL", "http://127.0.0.1:8766")
AUDIT = os.environ.get(
    "QUIETOS_MARRIAGE_AUDIT",
    os.path.expanduser("~/quietos-bridge/marriage_audit.jsonl"))

SYSTEM = (
    "You are Gemma, Jon's personal assistant at DeanWay Travels. "
    "You act ONLY by replying with exactly ONE JSON object per turn, "
    "nothing else — no prose, no code fences. To use a tool reply: "
    '{"tool": "<name>", "args": {...}}. '
    "Tools: gmail_search {query}, gmail_read {id}, "
    "gmail_draft {to, subject, body}, drive_list {}, "
    "drive_read {path}, web_search {query}. "
    "When the task is complete reply: {\"final\": \"<short answer>\"}. "
    "Rules: never claim Jon approved anything; never ask to send mail "
    "(sending is not a tool you have); drafts are as far as you go. "
    "Work step by step: search first, read what matters, then draft.")

DEFAULT_TASK = ("Search the inbox for cruise-related mail, read what "
                "you find, and draft a short digest for Jon. "
                "Do not send anything.")


def _extract_json(text):
    """First {...} block in the reply, or None. She talks; we adapt."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start:end + 1]


class LlamaBrain:
    """Brain callable backed by the local llama server (stdlib only)."""

    def __init__(self, url=BRAIN_URL):
        self.url = url.rstrip("/")

    def __call__(self, context):
        messages = [{"role": "system", "content": SYSTEM}]
        for entry in context:
            if "task" in entry:
                messages.append({"role": "user",
                                 "content": f"Task: {entry['task']}"})
            elif "tool_result" in entry:
                messages.append({"role": "user",
                                 "content": f"Tool result: {entry['tool_result']}"})
        body = json.dumps({"messages": messages, "temperature": 0.2,
                           "max_tokens": 256}).encode()
        req = urllib.request.Request(
            self.url + "/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode())
        content = data["choices"][0]["message"]["content"] or ""
        blob = _extract_json(content)
        if blob is not None:
            return blob
        # She answered in prose instead of protocol: treat it as her
        # final answer for this meeting, and say so out loud.
        print("[brain replied in prose; treating as final answer]")
        return json.dumps({"final": content.strip()[:400]})


def main():
    task = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TASK
    os.makedirs(os.path.dirname(os.path.abspath(AUDIT)), exist_ok=True)
    gov = Governor(AUDIT)
    harness = BrainHarness(gov, ToolBackend(), actor="gemma", max_turns=12)
    print(f"task: {task}")
    print("QuietOS is governing every call below.\n")
    res = harness.run(task, LlamaBrain())
    for entry in res["log"]:
        if "tool" in entry:
            extra = entry.get("verdict", entry.get("error", ""))
            prev = entry.get("result_preview", "")
            print(f"turn {entry['turn']}: {entry['tool']} -> {extra}"
                  + (f" | {prev}" if prev else ""))
        elif "final" in entry:
            print(f"turn {entry['turn']}: final answer")
        elif "error" in entry:
            print(f"turn {entry['turn']}: {entry['error']}")
    print(f"\nRESULT: {res['status']} in {res['turns']} turns")
    if res.get("answer"):
        print(f"HER ANSWER: {res['answer']}")
    if res.get("approval_id"):
        print(f"HELD for Jon: approval {res['approval_id']} "
              "(nothing executed)")
    ok, detail = gov.audit.verify()
    print(f"audit: {'OK' if ok else 'BROKEN'} ({detail})")


if __name__ == "__main__":
    main()
