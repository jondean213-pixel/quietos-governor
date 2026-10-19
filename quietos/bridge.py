"""File-based notification bridge — the missing wire (2026-10-08).

The governor renders notifications; JonChannel answers replies. Neither
touches a phone. This pump is the transport between them and the phone:

  outbox/<notification_id>.json  rendered notification (governor -> phone)
  inbox/<reply>.json             {"notification_id": ..., "verb": ...}

The phone side (sim/termux_watcher.py) posts each outbox file as a real
Android notification with Approve / Deny buttons; a tap writes the reply
file, pump_in() feeds it to the channel as source "jon_device" — the only
source a reply counts from. Reply files are consumed exactly once; junk
files are quarantined to inbox/dead/ and audited, never retried forever.
"""
import json
import os

from .reply_auth import load_or_create_secret, verify_reply

JON_DEVICE = "jon_device"


class BridgePump:
    def __init__(self, channel, outbox_dir, inbox_dir):
        self.channel = channel
        self.outbox = outbox_dir
        self.inbox = inbox_dir
        os.makedirs(outbox_dir, exist_ok=True)
        os.makedirs(inbox_dir, exist_ok=True)
        os.makedirs(os.path.join(inbox_dir, "dead"), exist_ok=True)
        # Reply-auth secret (Finding 1, 2026-10-19): the bridge root is the
        # common parent of outbox/ and inbox/ — the same layout the phone
        # watcher uses (~/quietos-bridge/.reply_secret).
        bridge_root = os.path.dirname(os.path.abspath(outbox_dir))
        self._reply_secret = load_or_create_secret(bridge_root)

    def pump_out(self) -> list:
        """Render queued governor notifications into outbox files."""
        written = []
        for n in self.channel.poll_outbox():
            path = os.path.join(self.outbox, f"{n.id}.json")
            with open(path, "w") as f:
                json.dump({"id": n.id, "kind": n.kind, "title": n.title,
                           "body": n.body, "actions": n.actions,
                           "approval_id": n.approval_id,
                           "expires_at": n.expires_at}, f)
            written.append(path)
        return written

    def pump_in(self) -> list:
        """Deliver inbox replies to the channel. Returns result strings.

        Finding 1 (2026-10-19): every reply must carry a valid HMAC
        signature from the phone-side watcher. An unsigned or
        mis-signed reply is quarantined and audited as a forgery
        attempt — it never reaches receive_reply().
        """
        results = []
        gov = self.channel.gov
        for name in sorted(os.listdir(self.inbox)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.inbox, name)
            try:
                with open(path) as f:
                    reply = json.load(f)
                nid = reply["notification_id"]
                verb = reply["verb"]
                sig = reply.get("sig", "")
            except (json.JSONDecodeError, KeyError, TypeError):
                gov.audit.append("bridge_bad_reply", file=name)
                os.replace(path, os.path.join(self.inbox, "dead", name))
                results.append(f"{name}: quarantined (malformed)")
                continue
            if not verify_reply(self._reply_secret, nid, verb, sig):
                gov.audit.append("bridge_forgery_attempt", file=name,
                                 notification_id=nid, verb=verb)
                os.replace(path, os.path.join(self.inbox, "dead", name))
                results.append(f"{name}: quarantined (bad signature)")
                continue
            results.append(self.channel.receive_reply(nid, verb, JON_DEVICE))
            os.remove(path)
        return results

    def run_once(self) -> tuple:
        """One full cycle: out to the phone, answers back in."""
        return self.pump_out(), self.pump_in()
