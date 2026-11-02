"""Fictional demonstration data for the Jobsite Jackpot Portfolio Demo.

Seeds a lively, realistic board: full and open tiers, confirmed payouts, a
challenger waiting on a full tier, an overtime player with a mini-game bonus, and
a couple of entries pending review. ALL DATA IS FICTIONAL.

Usage:
    python reset_demo.py          # wipe + reseed (CLI)
    seed_if_empty()               # seed only if the board is empty (called on boot)
    reset_demo()                  # wipe submissions/settings/outbox + reseed
"""
import os
import json
import pb_db
from payout_board import _DATA_DIR, PB_SETTINGS_FILE

# Tier ids: 1 Entry Level ($150), 2 Day Rate ($400), 3 Double Shift ($600), 4 Top Earner ($1500)
_TIER_NAME = {1: "Entry Level", 2: "Day Rate", 3: "Double Shift", 4: "Top Earner"}

# (name, alias, email, platform, views, tier, state, extra)
# state: "cashed" (approved payout), "pending_cashout" (awaiting admin), "active"
#        (qualified, not cashed), "overtime" (riding + mini-game bonus), "review"
#        (new submission awaiting verification)
_ROSTER = [
    # ── Top Earner (cap 2) — FULL, so a high-view newcomer becomes a Challenger ──
    ("Marcus Reed",     "Diesel Hauler",   "m.reed@creators.example",     "YouTube",   16400, 4, "cashed"),
    ("Priya Shah",      "Copper Bolter",   "priya@creators.example",      "TikTok",     7001, 4, "pending_cashout"),
    ("Devon Blake",     "Slate Rivet",     "devon.b@creators.example",    "Instagram",  9300, 4, "active"),   # qualifies Top Earner (FULL) -> Challenger

    # ── Double Shift (cap 3) — FULL ──
    ("Tanya Flores",    "Grease Sparky",   "tanya.f@creators.example",    "YouTube",    4576, 3, "cashed"),
    ("Jordan Mills",    "Iron Digger",     "jordan.m@creators.example",   "TikTok",     4120, 3, "cashed"),
    ("Sam Okafor",      "Ember Cutter",    "sam.o@creators.example",      "Instagram",  4005, 3, "pending_cashout"),

    # ── Day Rate (cap 5) — partially open (3 claimed) ──
    ("Riley Nguyen",    "Flint Welder",    "riley.n@creators.example",    "TikTok",     3700, 2, "cashed"),
    ("Casey Monroe",    "Brass Grinder",   "casey.m@creators.example",    "YouTube",    2900, 2, "cashed"),
    ("Alex Turner",     "Onyx Trencher",   "alex.t@creators.example",     "Instagram",  2100, 2, "pending_cashout"),
    ("Nina Castillo",   "Ash Roofer",      "nina.c@creators.example",     "TikTok",     2650, 2, "overtime"),   # went overtime + won mini-game bonus

    # ── Entry Level (cap 10) — open ──
    ("Omar Haddad",     "Rust Paver",      "omar.h@creators.example",     "Instagram",   980, 1, "cashed"),
    ("Bea Lindqvist",   "Chalk Mason",     "bea.l@creators.example",      "YouTube",     760, 1, "cashed"),
    ("Theo Park",       "Soot Plumber",    "theo.p@creators.example",     "TikTok",      640, 1, "cashed"),
    ("Mika Johansson",  "Clay Fitter",     "mika.j@creators.example",     "Instagram",   520, 1, "pending_cashout"),

    # ── Active & qualified (not cashed out) — healthy leaderboard ──
    ("Harper Quinn",    "Granite Layer",   "harper.q@creators.example",   "YouTube",    3200, 2, "active"),
    ("Luca Moretti",    "Cedar Framer",    "luca.m@creators.example",     "TikTok",     1850, 1, "active"),
    ("Dani Rose",       "Amber Tiler",     "dani.r@creators.example",     "Instagram",   820, 1, "active"),

    # ── New submissions awaiting admin verification (audit queue) ──
    ("Felix Wu",        "Maple Bricker",   "felix.w@creators.example",    "TikTok",     1400, 1, "review"),
    ("Sara Abboud",     "Quartz Hauler",   "sara.a@creators.example",     "YouTube",    6100, 3, "review"),
    ("Kwame Mensah",    "Basalt Digger",   "kwame.m@creators.example",    "Instagram",  2400, 2, "review"),

    # ── Low-view entrants still climbing ──
    ("Elena Popov",     "Shale Striper",   "elena.p@creators.example",    "TikTok",      310, 0, "active"),
    ("Noah Bergman",    "Cobalt Mower",    "noah.b@creators.example",     "YouTube",     150, 0, "active"),
]


def _code(i):
    import string
    base = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    # deterministic, readable, unique-per-seed codes
    return "JSJ-" + "".join(base[(i * 7 + k * 13) % len(base)] for k in range(7))


def _row(i, name, alias, email, platform, views, tier, state):
    niche = {"YouTube": "Home Reno", "TikTok": "Trades", "Instagram": "HVAC"}.get(platform, "Trades")
    d = {
        "name": name, "email": email, "alias": alias, "niche": niche,
        "platform": platform, "post_url": f"https://{platform.lower()}.example/p/{alias.replace(' ', '').lower()}",
        "screenshot_url": "", "impressions": views, "co_impressions": views,
        "tier_qualified": 0, "cashed_out": "No", "cash_out_tier": "None",
        "spot_claimed": "No", "status": "Pending", "payout": "Unpaid",
        "wc_eligible": "No", "wc_entry": "No", "wc_won": "No",
        "sub_date": f"2026-07-{(i % 20) + 1:02d}T10:00:00Z", "co_date": "",
        "notes": "", "mower_bonus": 0, "plus_code": _code(i), "has_played_minigame": 0,
        "overtime_round": 0, "bonus_awarded_round": 0,
    }
    if state in ("cashed", "pending_cashout"):
        d.update(tier_qualified=tier, cashed_out="Yes", cash_out_tier=_TIER_NAME[tier],
                 spot_claimed="Yes", co_date=f"2026-07-{(i % 20) + 2:02d}T12:00:00Z")
        d["status"] = "Verified" if state == "cashed" else "Pending"
        d["payout"] = "Unpaid"
    elif state == "overtime":
        d.update(tier_qualified=tier, status="Overtime", spot_claimed="No",
                 mower_bonus=100, has_played_minigame=1, overtime_round=1, bonus_awarded_round=1)
    elif state == "active":
        d.update(tier_qualified=tier, status="Pending", spot_claimed="No")
    elif state == "review":
        d.update(tier_qualified=0, status="Pending", spot_claimed="No")
    return d


def _set_live_window():
    """Keep the demo's countdown live: start ~a week ago, close ~3 weeks out, open to play.
    Computed relative to today so the demo never goes stale."""
    import datetime
    from payout_board import _load_settings, _save_settings
    today = datetime.date.today()
    s = _load_settings()
    s["campaign_start_date"] = (today - datetime.timedelta(days=7)).isoformat()
    s["campaign_close_date"] = (today + datetime.timedelta(days=21)).isoformat()
    s["game_locked"] = False
    s["board_visible"] = True
    _save_settings(s)


def seed(reset=False):
    pb_db.init_db()
    if reset:
        pb_db.delete_all()
    for i, (name, alias, email, platform, views, tier, state) in enumerate(_ROSTER):
        pb_db.append_row(_row(i, name, alias, email, platform, views, tier, state))
    _set_live_window()
    return len(_ROSTER)


def seed_if_empty():
    pb_db.init_db()
    if not pb_db.read_all():
        return seed(reset=False)
    return 0


def reset_demo():
    """Full reset to the fictional demo state: wipe submissions, settings and outbox."""
    pb_db.init_db()
    pb_db.delete_all()
    for path in (PB_SETTINGS_FILE, os.path.join(_DATA_DIR, "outbox.json")):
        try:
            os.remove(path)
        except OSError:
            pass
    n = seed(reset=False)
    return n
