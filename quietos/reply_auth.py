"""Reply authentication for the notification bridge.

Finding 1 (2026-10-19 red-team): pump_in() fed every inbox/*.json file to
receive_reply() with a hardcoded JON_DEVICE source. Any process that could
write a JSON file into the inbox directory *was* Jon as far as the channel
was concerned — a compromised brain with inbox write access could approve
its own Tier-2 actions.

Fix: the phone-side watcher signs each reply with HMAC-SHA256 using a
device-local secret. pump_in() verifies the signature before the reply
ever reaches receive_reply(). No valid signature → quarantined, audited,
never processed.

The secret lives at <bridge_root>/.reply_secret (0600). It is generated
once on first use. The agent runner must never see it — defense in depth
is to keep the bridge directory off any path the runner can write to,
but the HMAC is the real gate: write access to the inbox alone is no
longer enough to forge an approval.
"""
import hashlib
import hmac
import os
import secrets

SECRET_FILENAME = ".reply_secret"
SECRET_BYTES = 32


def secret_path_for(bridge_root: str) -> str:
    return os.path.join(bridge_root, SECRET_FILENAME)


def load_or_create_secret(bridge_root: str) -> bytes:
    """Read the reply secret, generating it (0600) on first use."""
    path = secret_path_for(bridge_root)
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    secret = secrets.token_bytes(SECRET_BYTES)
    # Write atomically-ish: create with restrictive mode from the start.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, secret)
    finally:
        os.close(fd)
    return secret


def _message(notification_id: str, verb: str) -> bytes:
    return f"{notification_id}:{verb}".encode("utf-8")


def sign_reply(secret: bytes, notification_id: str, verb: str) -> str:
    """HMAC-SHA256 hex signature for a reply. Phone side calls this."""
    return hmac.new(secret, _message(notification_id, verb),
                    hashlib.sha256).hexdigest()


def verify_reply(secret: bytes, notification_id: str, verb: str,
                 signature: str) -> bool:
    """Constant-time signature check. Governor side calls this."""
    if not isinstance(signature, str):
        return False
    expected = sign_reply(secret, notification_id, verb)
    return hmac.compare_digest(expected, signature)
