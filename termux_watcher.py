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
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import time

BRIDGE = os.path.expanduser("~/quietos-bridge")
OUTBOX = os.path.join(BRIDGE, "outbox")
INBOX = os.path.join(BRIDGE, "inbox")


def load_or_create_secret(bridge_root: str = None) -> bytes:
    """Device-local reply secret (Finding 1, 2026-10-19).

    Generated once, 0600. The governor's pump_in() verifies every reply
    against this secret — an unsigned reply file is quarantined, never
    processed. Never expose this file to the agent runner.
    """
    root = bridge_root or os.path.dirname(INBOX)
    path = os.path.join(root, ".reply_secret")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    secret = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, secret)
    finally:
        os.close(fd)
    return secret


def sign_reply(secret: bytes, notification_id: str, verb: str) -> str:
    msg = f"{notification_id}:{verb}".encode("utf-8")
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()


def write_reply(notification_id: str, verb: str):
    os.makedirs(INBOX, exist_ok=True)
    secret = load_or_create_secret()
    path = os.path.join(INBOX, f"{notification_id}-{verb}.json")
    with open(path, "w") as f:
        json.dump({"notification_id": notification_id, "verb": verb,
                   "sig": sign_reply(secret, notification_id, verb)}, f)


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


def answer_ack(notification_id: str, verb: str):
    """Replace the answered card with a confirmation (same --id).

    Jon's sound-mind standard (2026-10-08): he needs to know the tap
    went back AND what QuietOS did about it. This is the first half
    (answer received); the report card follows once the action ran.
    """
    subprocess.run(["termux-notification", "--id", notification_id,
                    "--title", "QuietOS",
                    "--content", f"Answer sent: {verb.capitalize()} — "
                                 "QuietOS will report what happened."],
                   check=False)


def defang_stale():
    """Kill tappable cards left over from dead runs (zombie cards).

    Any outbox file present at watcher start belongs to a governor
    process that no longer exists. Its buttons still work, but the
    replies land as channel_reply_unknown — on 2026-10-08 a card from
    the first-ever test kept eating Jon's (fast, correct) taps across
    three runs. Replace each tappable card with a dead one — same id,
    no buttons — and remove the file. FYI cards (no buttons) pass.
    """
    os.makedirs(OUTBOX, exist_ok=True)
    for name in sorted(os.listdir(OUTBOX)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(OUTBOX, name)
        try:
            with open(path) as f:
                note = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if note.get("actions"):
            subprocess.run(
                ["termux-notification", "--id",
                 note.get("id", name[:-5]),
                 "--title", "QuietOS — dead card",
                 "--content",
                 "That request is over (earlier run). Swipe me away."],
                check=False)
            os.remove(path)
            print(f"watcher: defanged stale card {note.get('id', name)}")


def process_file(name, seen):
    """Post one outbox file. A poison file (malformed JSON, bad shape)
    is quarantined to outbox/dead/ instead of being retried every 5s
    forever — one unreadable file must never starve the files behind
    it or kill the loop."""
    path = os.path.join(OUTBOX, name)
    if name[:-5] in seen:
        return
    try:
        with open(path) as f:
            post(json.load(f), seen)
    except Exception:
        dead = os.path.join(OUTBOX, "dead")
        os.makedirs(dead, exist_ok=True)
        try:
            os.replace(path, os.path.join(dead, name))
        except OSError:
            pass
        print(f"watcher: quarantined bad outbox file {name}", flush=True)


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--reply":
        write_reply(sys.argv[2], sys.argv[3])
        try:
            answer_ack(sys.argv[2], sys.argv[3])
        except Exception:
            pass  # the reply file is the record; ack is best-effort
        return
    os.makedirs(OUTBOX, exist_ok=True)
    os.makedirs(INBOX, exist_ok=True)
    defang_stale()
    seen = set()
    print("watcher up — polling", OUTBOX)
    while True:
        try:
            for name in sorted(os.listdir(OUTBOX)):
                if name.endswith(".json"):
                    process_file(name, seen)
        except Exception as e:  # keep roaming; a bad file shouldn't kill us
            print("watcher:", e, file=sys.stderr)
        time.sleep(5)


if __name__ == "__main__":
    main()
