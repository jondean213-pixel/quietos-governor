#!/usr/bin/env python3
"""termux_watcher.py — the phone end of the notification bridge.

DEPLOYMENT-TIME piece (Motorola, Termux). Not run in the sandbox.
Requires Termux:API (termux-notification). Governor outbox/inbox live at
~/quietos-bridge/ on the phone; BridgePump on the governor side and this
watcher on the phone side share those two folders.

Loop: every 5s, post any new outbox JSON as an Android notification.
Approve / Deny (or Unpause) buttons write a reply JSON into inbox — that
tap is the "jon_device" source the governor trusts. Read-only otherwise:
this script never answers anything itself.
"""
import json
import os
import subprocess
import sys
import time

BRIDGE = os.path.expanduser("~/quietos-bridge")
OUTBOX = os.path.join(BRIDGE, "outbox")
INBOX = os.path.join(BRIDGE, "inbox")


def write_reply(notification_id: str, verb: str):
    os.makedirs(INBOX, exist_ok=True)
    path = os.path.join(INBOX, f"{notification_id}-{verb}.json")
    with open(path, "w") as f:
        json.dump({"notification_id": notification_id, "verb": verb}, f)


def reply_cmd(notification_id: str, verb: str) -> str:
    return (f"python3 {os.path.abspath(__file__)} --reply "
            f"{notification_id} {verb}")


def post(n: dict, seen: set):
    cmd = ["termux-notification", "--id", n["id"],
           "--title", n["title"], "--content", n["body"]]
    for i, verb in enumerate(n.get("actions", [])[:3], start=1):
        label = verb.capitalize()
        cmd += [f"--button{i}", label,
                f"--button{i}-action", reply_cmd(n["id"], verb)]
    subprocess.run(cmd, check=False)
    seen.add(n["id"])


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--reply":
        write_reply(sys.argv[2], sys.argv[3])
        return
    os.makedirs(OUTBOX, exist_ok=True)
    os.makedirs(INBOX, exist_ok=True)
    seen = set()
    print("watcher up — polling", OUTBOX)
    while True:
        try:
            for name in sorted(os.listdir(OUTBOX)):
                if not name.endswith(".json"):
                    continue
                nid = name[:-5]
                if nid in seen:
                    continue
                with open(os.path.join(OUTBOX, name)) as f:
                    post(json.load(f), seen)
        except Exception as e:  # keep roaming; a bad file shouldn't kill us
            print("watcher:", e, file=sys.stderr)
        time.sleep(5)


if __name__ == "__main__":
    main()
