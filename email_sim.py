"""Simulated email layer for the Jobsite Jackpot Portfolio Demo.

Drop-in replacement for the production SendGrid client. NO REAL EMAIL IS EVER SENT.
Every notification the game would normally email is instead recorded to
data/outbox.json (capped, newest kept) so the demo can show that a notification
*would* have gone out — clearly flagged as simulated.

Each function mirrors the production signature loosely via *args/**kwargs, so the
game's call sites work unchanged. Recipient is the first positional arg.
"""
import json
import os
import datetime

_DATA_DIR = os.environ.get(
    "PB_DATA_DIR", os.path.join(os.path.dirname(__file__), "data")
)
_OUTBOX = os.path.join(_DATA_DIR, "outbox.json")


def _record(kind, args, kwargs):
    """Append a simulated send to the outbox. Best-effort, never raises."""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        try:
            with open(_OUTBOX) as f:
                box = json.load(f)
        except Exception:
            box = []
        to = (args[0] if args else kwargs.get("email", "")) or ""
        box.append({
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "type": kind,
            "to": to,
            "simulated": True,
        })
        with open(_OUTBOX, "w") as f:
            json.dump(box[-200:], f, indent=2)
    except Exception:
        pass
    return True  # mimic a successful send so game flow continues identically


def _make(kind):
    def _fn(*args, **kwargs):
        return _record(kind, args, kwargs)
    _fn.__name__ = f"send_{kind}"
    return _fn


# Mirrors every function the game imports from the production email client.
send_submission_confirmation   = _make("submission_confirmation")
send_approval_notification     = _make("approval_notification")
send_verification_notification = _make("verification_notification")
send_cashout_received          = _make("cashout_received")
send_wildcard_confirmation     = _make("wildcard_confirmation")
send_overtime_confirmation     = _make("overtime_confirmation")
send_mower_win                 = _make("mower_win")
send_bumped_keep_playing       = _make("bumped_keep_playing")
send_locked_offer              = _make("locked_offer")


def read_outbox():
    """Return the simulated outbox (newest last)."""
    try:
        with open(_OUTBOX) as f:
            return json.load(f)
    except Exception:
        return []
