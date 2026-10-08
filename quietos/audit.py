"""Append-only, tamper-evident audit log.

Every QuietOS decision is recorded: timestamp, requesting brain, proposed
action, tier, decision, and Jon's response for escalations. Entries are
hash-chained — any modification breaks the chain and is detectable.
Brains cannot write to this log; only the governor holds the writer.
"""
import hashlib
import json
import os
import time


class AuditLog:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w") as f:
                genesis = {"seq": 0, "event": "log_initialized", "ts": time.time(),
                           "prev_hash": "0" * 64}
                genesis["hash"] = self._hash(genesis)
                f.write(json.dumps(genesis) + "\n")

    @staticmethod
    def _hash(entry: dict) -> str:
        payload = json.dumps(entry, sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()

    def _last(self) -> dict:
        with open(self.path) as f:
            lines = f.read().strip().split("\n")
        return json.loads(lines[-1])

    def append(self, event: str, **fields) -> dict:
        prev = self._last()
        entry = {"seq": prev["seq"] + 1, "event": event, "ts": time.time(),
                 "prev_hash": prev["hash"]}
        entry.update(fields)
        entry["hash"] = self._hash({k: v for k, v in entry.items() if k != "hash"})
        with open(self.path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def verify(self) -> tuple[bool, str]:
        """Walk the chain. Returns (ok, message)."""
        with open(self.path) as f:
            lines = [l for l in f.read().strip().split("\n") if l]
        prev_hash = "0" * 64
        for i, line in enumerate(lines):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                return False, f"unparseable entry at line {i}"
            for key in ("prev_hash", "hash", "seq"):
                if key not in entry:
                    return False, f"missing field '{key}' at line {i}"
            if entry["prev_hash"] != prev_hash:
                return False, f"chain broken at seq {entry.get('seq')} (line {i})"
            check = self._hash({k: v for k, v in entry.items() if k != "hash"})
            if check != entry["hash"]:
                return False, f"entry hash mismatch at seq {entry.get('seq')}"
            if entry["seq"] != i:
                return False, f"sequence gap at line {i}"
            prev_hash = entry["hash"]
        return True, f"chain intact, {len(lines)} entries"

    def read_all(self) -> list:
        with open(self.path) as f:
            return [json.loads(l) for l in f.read().strip().split("\n") if l]
