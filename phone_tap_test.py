"""On-phone tap test — the governor asks, Jon answers on his own phone.

Run in Termux (with the watcher running):

    python3 -u sim/phone_tap_test.py

It holds a sandbox Tier-2 delete, writes the notification to the bridge
outbox (the watcher posts it to the shade with Approve/Deny buttons),
then waits for Jon's tap and prints the verdict + audit verification.

Paths default to the phone's ~/quietos-bridge; override with
QUIETOS_BRIDGE_DIR (tests), QUIETOS_PKG_PARENT (test package location),
QUIETOS_WAIT_S (wait seconds, default 120).

Written 2026-10-08 from the real module signatures after a hand-typed
heredoc used the wrong BridgePump call — this file is the tap test now;
nobody re-derives it.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_PARENT = os.environ.get(
    "QUIETOS_PKG_PARENT", os.path.dirname(HERE))  # phone: ~
BRIDGE = os.environ.get(
    "QUIETOS_BRIDGE_DIR", os.path.expanduser("~/quietos-bridge"))
WAIT_S = int(os.environ.get("QUIETOS_WAIT_S", "600"))

sys.path.insert(0, PKG_PARENT)

from quietos import Action, BridgePump, Governor, JonChannel  # noqa: E402


def main():
    os.makedirs(BRIDGE, exist_ok=True)
    gov = Governor(os.path.join(BRIDGE, "audit.jsonl"))
    channel = JonChannel(gov)
    pump = BridgePump(channel, os.path.join(BRIDGE, "outbox"),
                      os.path.join(BRIDGE, "inbox"))

    # Drain the inbox: a reply left over from a dead run must not be
    # consumed by this one (it would log unknown and close the wait).
    for name in os.listdir(pump.inbox):
        if name.endswith(".json"):
            os.remove(os.path.join(pump.inbox, name))

    decision = gov.propose(Action(
        actor="gemma", kind="delete", tool="fs_delete",
        detail="TEST: purge old temp cache files (on-phone test)"))
    written = pump.pump_out()
    nid = os.path.basename(written[0])[:-5] if written else "?"
    print(f"verdict: {decision.verdict} | notification file: {nid}",
          flush=True)
    print("Check your shade, expand the QuietOS card, tap a button. "
          f"Waiting up to {WAIT_S} seconds...", flush=True)

    # Listen until a DECISIVE answer (approved/denied/unpaused). A
    # stray tap on a stale card logs "rejected: unknown" — noted, but
    # it must not close the wait; on 2026-10-08 that bug ended two of
    # Jon's runs before he ever reached the live card.
    results = []
    for _ in range(max(1, WAIT_S // 5)):
        batch = pump.pump_in()
        if batch:
            results.extend(batch)
            print("reply:", batch, flush=True)
            if any(r in ("approved", "denied", "unpaused") for r in batch):
                break
            print("(stray tap ignored — still listening)", flush=True)
        time.sleep(5)

    if results:
        print("reply results:", results, flush=True)
    print("YOU ANSWERED:", decision.approval.status
          if decision.approval and decision.approval.status != "held"
          else "no tap yet — request still held", flush=True)
    ok, detail = gov.audit.verify()
    print("audit:", "OK" if ok else "BROKEN", f"({detail})", flush=True)

    # Jon's sound-mind standard: close the loop with a report card that
    # says what actually happened — built from the audit, not a bare ack.
    if decision.approval and decision.approval.status in ("approved", "denied"):
        gov.report_to_jon(
            "QuietOS report — test complete",
            f"You answered: {decision.approval.status}.\n"
            "What happened: nothing was deleted — this was a test, no "
            "files were touched.\n"
            f"Audit: {'OK' if ok else 'BROKEN'} ({detail}).")
    else:
        gov.report_to_jon(
            "QuietOS report — no answer",
            "No tap arrived in time, so the test request died untouched. "
            "Nothing was changed.\n"
            f"Audit: {'OK' if ok else 'BROKEN'} ({detail}).")
    pump.pump_out()
    print("Report card sent — check your shade.", flush=True)


if __name__ == "__main__":
    main()
