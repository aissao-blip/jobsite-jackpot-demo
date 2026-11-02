"""Jobsite Jackpot — Sportsbook-style creator competition."""

import csv
import hmac
import io
import json
import logging
import os
import random
import string
import uuid
from datetime import datetime, timezone, timedelta, date as _date

from flask import (
    Blueprint,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

logger = logging.getLogger(__name__)
import pb_db

payout_board_bp = Blueprint("payout_board", __name__)

# ── Persistent-disk-aware paths ───────────────────────────────────────────────
# On Render, PB_DATA_DIR=/pb_data and PB_UPLOADS_DIR=/pb_data/uploads are set
# via env vars pointing to the mounted persistent disk.  Locally these fall
# back to the repo-relative directories.
_DATA_DIR = os.environ.get(
    "PB_DATA_DIR",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "data")),
)
_UPLOADS_DIR = os.environ.get(
    "PB_UPLOADS_DIR",
    os.path.normpath(os.path.join(os.path.dirname(__file__), "static", "uploads")),
)

PB_SETTINGS_FILE = os.path.join(_DATA_DIR, "pb_settings.json")

TIER_CONFIG = {
    1: {"name": "Entry Level", "threshold": 500,  "prize": 150,  "cap": 10},
    2: {"name": "Day Rate",    "threshold": 2000, "prize": 400,  "cap": 5},
    3: {"name": "Double Shift", "threshold": 4000, "prize": 600,  "cap": 3},
    4: {"name": "Top Earner",  "threshold": 7000, "prize": 1500, "cap": 2},
}
# TIER_CONFIG is the single source of truth for prizes, thresholds and default caps.
# Prize amounts and thresholds are FIXED for the campaign (never admin-editable), so the
# public board display and the payout math can never diverge.  Only spot caps can be
# adjusted by the admin, and those are stored as an override in settings["tier_caps"].
TOTAL_BUDGET = 8300  # 150*10 + 400*5 + 600*3 + 1500*2

# ── Admin-only payout overrides ──────────────────────────────────────────────
# One-off manual payout corrections, keyed by Player Code. These are surfaced ONLY
# in the admin panel and CSV export — they are NEVER included in any public payload
# (public board/api render nothing from this map). Use for making a creator whole
# when a system issue paid them the wrong tier. `amount` is the dollar figure to pay
# via the payout provider; the public board keeps showing whatever tier they cashed at.
PAYOUT_OVERRIDES = {}

# ── TEMPORARY public-board tier corrections (display-only) ────────────────────
# Records the earlier approve/verify bug flipped to the wrong tier are corrected AT
# RENDER TIME for the PUBLIC board only (keyed by board alias), so spot counts, Locked
# In and Remaining read correctly WITHOUT any database write. This does NOT touch the
# stored rows and is NOT applied to the admin panel or CSV export — the backend still
# shows the raw (uncorrected) data until you fix it via the admin "fix tier" control.
# Remove entries here once the underlying records are corrected.
BOARD_TIER_CORRECTIONS = {}

def _apply_board_tier_corrections(subs):
    """Overlay BOARD_TIER_CORRECTIONS onto in-memory rows (never the DB) so the PUBLIC
    board counts/prizes reflect the corrected tier. Idempotent; only touches cashed-out
    rows whose alias is listed above."""
    for s in subs:
        t = BOARD_TIER_CORRECTIONS.get(s.get("alias"))
        if t is not None and s.get("cashed_out") == "Yes":
            s["tier_qualified"] = t
            s["cash_out_tier"] = TIER_CONFIG.get(t, {}).get("name", "None")
    return subs


# TEMPORARY display-only: aliases suppressed from the public "Challenger" badge. Their
# leaderboard row renders as a plain active entry (no "Next for Top Earner $X" badge) —
# used when a creator has been offered a step-down/consolation instead of waiting on a
# full tier. Does not change the DB. Remove once the creator cashes out or is resolved.
BOARD_CHALLENGER_SUPPRESS = set()

# Perfect Stripes mini-game bonus: $100 per winner, capped at $1,000 total (≤10 winners).
# Per the Official Contest Rules §7. Awarded first-come until the pool is exhausted.
MINIGAME_BONUS      = 100
MINIGAME_BONUS_POOL = 1000

# ── Campaign date defaults (BCR launch) ───────────────────────────────────────
# Admin can override both via the Board Controls (settings campaign_start_date /
# campaign_close_date).  Kept here so a missing/blank setting never breaks the board.
GAME_START_DEFAULT = "2026-07-21"
GAME_END_DEFAULT   = "2026-08-11"  # Entry Period end per the Official Contest Rules

# ── Admin authentication ──────────────────────────────────────────────────────
# Every admin/mutating/export endpoint is gated by HTTP Basic Auth.  Credentials come
# from env vars; on Render (RENDER=true) a missing password FAILS CLOSED so the admin
# surface is never exposed unauthenticated.  Locally a dev fallback keeps testing easy.
_IS_PROD        = bool(os.environ.get("RENDER"))
_ADMIN_USER     = os.environ.get("ADMIN_USER", "admin")
_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
_ADMIN_DEV_PASSWORD = "jackpot-dev"  # used ONLY when not on Render and no env password set

# Endpoint function names (request.endpoint minus the "payout_board." prefix) that
# require admin auth.  Everything else on the blueprint is public game surface.
_ADMIN_ENDPOINTS = {
    "admin_view",
    "api_verify_submission", "api_unverify", "api_approve_cashout", "api_disqualify",
    "api_award_bonus", "api_set_tier",
    "api_notify_keep_playing", "api_reopen_and_nudge",
    "api_mark_paid", "api_approve_wildcard", "api_close_tier", "api_reopen_tier",
    "api_toggle_board", "api_toggle_locked_banner", "api_toggle_game_lock",
    "api_notify_locked_offer",
    "api_open_wildcard", "api_close_wildcard",
    "api_set_close_date", "api_export_csv", "api_reset_campaign",
    "repair_paid_records",
    "sandbox_seed_creators", "sandbox_seed_tier_caps", "sandbox_inject_fraud",
    "prize_config_page", "prize_config_update",
}
# Sandbox seeders are blocked outright on production so a live campaign can never be
# contaminated with fake players (reset-campaign stays available to purge test data).
_PROD_BLOCKED_ENDPOINTS = {
    "sandbox_seed_creators", "sandbox_seed_tier_caps", "sandbox_inject_fraud",
}

# Allowed screenshot upload types.  Extension whitelist + magic-byte sniff blocks
# HTML/SVG/script uploads that could run in the site origin when an admin opens them.
_ALLOWED_IMG_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif"}
_IMG_MAGIC = (
    b"\xff\xd8\xff",              # JPEG
    b"\x89PNG\r\n\x1a\n",         # PNG
    b"GIF87a", b"GIF89a",         # GIF
)


def _admin_auth_ok(auth) -> bool:
    if not auth:
        return False
    expected_pw = _ADMIN_PASSWORD or ("" if _IS_PROD else _ADMIN_DEV_PASSWORD)
    if not expected_pw:
        return False  # fail closed
    return (
        hmac.compare_digest(auth.username or "", _ADMIN_USER)
        and hmac.compare_digest(auth.password or "", expected_pw)
    )


@payout_board_bp.before_request
def _guard_admin_routes():
    """Require HTTP Basic Auth for every admin/mutating endpoint. Public game
    routes (live board, submit, check-status, cashout, mini-game) are untouched."""
    ep = (request.endpoint or "").split(".")[-1]
    if ep not in _ADMIN_ENDPOINTS:
        return None
    if _IS_PROD and ep in _PROD_BLOCKED_ENDPOINTS:
        return jsonify({"error": "Sandbox seeding is disabled on production."}), 403
    if _IS_PROD and not _ADMIN_PASSWORD:
        return Response(
            "Admin access is not configured. Set ADMIN_PASSWORD in the environment.",
            503,
        )
    if not _admin_auth_ok(request.authorization):
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="Jobsite Jackpot Admin"'},
        )
    return None


def _valid_image_upload(file) -> bool:
    """True if the uploaded file is an allowed image type (extension + magic bytes)."""
    if not file or not file.filename:
        return False
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in _ALLOWED_IMG_EXT:
        return False
    try:
        head = file.stream.read(16)
        file.stream.seek(0)
    except Exception:
        return False
    # WEBP: RIFF container whose bytes 8-12 are 'WEBP' (bare 'RIFF' also matches AVI/WAV).
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    # HEIC/HEIF: ISO-BMFF file — bytes 4-8 are the 'ftyp' box marker.
    if ext in (".heic", ".heif"):
        return head[4:8] == b"ftyp"
    return any(head.startswith(sig) for sig in _IMG_MAGIC)


def _save_image_upload(file):
    """Validate + save an uploaded screenshot. Returns (web_path, error_message)."""
    if not _valid_image_upload(file):
        return "", "Screenshot must be a valid image (JPG, PNG, GIF, WEBP or HEIC)."
    os.makedirs(_UPLOADS_DIR, exist_ok=True)
    ext = os.path.splitext(file.filename)[1].lower()
    fname = f"{uuid.uuid4().hex}{ext}"
    file.save(os.path.join(_UPLOADS_DIR, fname))
    return f"/pb-uploads/{fname}", ""


WILDCARD_CAP = 4
WILDCARD_PRIZE = 50

COLORS = [
    "Iron", "Steel", "Copper", "Rust", "Gravel", "Diesel",
    "Cinder", "Asphalt", "Timber", "Grease", "Ember", "Slate",
    "Soot", "Cobalt", "Flint", "Crude",
]
ANIMALS = [
    "Wrench", "Rivet", "Hauler", "Driller", "Welder", "Foreman",
    "Grinder", "Roofer", "Digger", "Rigger", "Cutter", "Hauler",
    "Plumber", "Sparky", "Framer", "Trencher", "Bolter", "Laborer",
]

NICHES = [
    "Landscaping", "HVAC", "Cleaning", "Plumbing",
    "Pest Control", "Electrical", "Roofing", "Painting", "Other",
]
PLATFORMS = ["Instagram", "TikTok"]


# ── Settings ──────────────────────────────────────────────────────────────────

_SETTINGS_DEFAULTS = {
    "board_visible": True,
    "campaign_close_date": None,
    "wildcard_window_open": False,
    "wildcard_window_opened_at": None,
    "manually_closed_tiers": [],
    "cashouts_locked_banner": False,
    # Campaign has ended: locked to NEW entries by default. The admin "Game" toggle writes
    # this to the settings file, so re-opening (game_locked=False) persists and wins here.
    "game_locked": False,
    "total_budget": TOTAL_BUDGET,
}


def _load_settings():
    # Start from defaults and overlay the saved file, so a newly-added setting key always
    # has a value even for settings files written before it existed.
    s = dict(_SETTINGS_DEFAULTS)
    if os.path.exists(PB_SETTINGS_FILE):
        try:
            with open(PB_SETTINGS_FILE) as f:
                s.update(json.load(f) or {})
        except (ValueError, OSError):
            pass
    return s


def _save_settings(s):
    with open(PB_SETTINGS_FILE, "w") as f:
        json.dump(s, f, indent=2)


# ── Business logic ────────────────────────────────────────────────────────────

def _int(v):
    try:
        return int(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0


def _calc_tier(impressions):
    if impressions >= 7000: return 4
    if impressions >= 4000: return 3
    if impressions >= 2000: return 2
    if impressions >= 500:  return 1
    return 0


def _tier_letter(col_idx):
    return chr(ord("A") + col_idx)


def _effective_cap(tier, settings=None):
    """Admin-adjustable cap: settings override if present, else the fixed default."""
    default = TIER_CONFIG[tier]["cap"]
    if settings is None:
        return default
    try:
        return int(settings.get("tier_caps", {}).get(str(tier), default))
    except (ValueError, TypeError):
        return default


def _claimed_spots(subs, tier):
    # A spot is "committed" (holds a dot on the live board) if the BP is:
    #   - Verified and holding a claimed spot, OR
    #   - cashed out (pending admin review counts — reserves the spot immediately so a
    #     tier can never be oversold in the window before admin approval), OR
    #   - already marked Paid (legacy records).
    return sum(
        1 for s in subs
        if _int(s["tier_qualified"]) == tier
        and (
            (s["spot_claimed"] == "Yes" and s["status"] == "Verified")
            or s.get("cashed_out") == "Yes"
            or s.get("payout") == "Paid"
        )
    )


def _tier_is_full(subs, tier, settings):
    if tier == 0:
        return False
    if tier in settings.get("manually_closed_tiers", []):
        return True
    return _claimed_spots(subs, tier) >= _effective_cap(tier, settings)


def _tier_stats(subs, settings):
    out = {}
    for t, cfg in TIER_CONFIG.items():
        cap = _effective_cap(t, settings)
        claimed = _claimed_spots(subs, t)
        manual = t in settings.get("manually_closed_tiers", [])
        full = claimed >= cap or manual
        out[t] = {
            "name":      cfg["name"],
            "threshold": cfg["threshold"],
            "prize":     cfg["prize"],
            "cap":       cap,
            "claimed":   claimed,
            "remaining": max(0, cap - claimed),
            "full":      full,
            "manual":    manual,
        }
    return out


def _cashout_options(subs, settings, sub, views=None):
    """Work out what a player can cash out RIGHT NOW.

    - Step-down: if their qualified (by-views) tier is full, offer the best currently
      OPEN tier below it.
    - Top Earner priority: a freed Top Earner spot is reserved for the highest-views
      waiter (tiebreak: earliest submission). Lower-ranked qualifiers step down instead.

    Returns a dict with the tier they can cash at now (0 = nothing open → waitlisted),
    plus their queue position for their qualified tier.
    """
    v = views if views is not None else _int(sub.get("co_impressions") or sub.get("impressions", 0))
    qtier = _calc_tier(v)
    ts = _tier_stats(subs, settings)
    out = {
        "qualified_tier": qtier,
        "qualified_tier_name": TIER_CONFIG.get(qtier, {}).get("name", ""),
        "cashout_tier": 0, "cashout_tier_name": "", "cashout_prize": 0,
        "is_step_down": False, "waitlist_rank": 0, "waitlist_size": 0,
        "top_spot_open": False,
    }
    if qtier <= 0:
        return out

    def _v(s):
        return _int(s.get("co_impressions") or s.get("impressions", 0))

    def _holds(s, t):
        # Already occupies a spot in tier t (verified+claimed, cashed out, or paid)
        return _int(s.get("tier_qualified", 0)) == t and (
            (s.get("spot_claimed") == "Yes" and s.get("status") == "Verified")
            or s.get("cashed_out") == "Yes" or s.get("payout") == "Paid"
        )

    # Ranked waiters for this tier: active, not cashed out, whose NATURAL (by-views) tier
    # is exactly this tier (so a Top Earner creator doesn't crowd out Day Rate hopefuls),
    # and who don't already hold a spot here. Highest views first, then earliest submit.
    waiters = [
        s for s in subs
        if s.get("status") != "Disqualified" and s.get("cashed_out") != "Yes"
        and _calc_tier(_v(s)) == qtier and not _holds(s, qtier)
    ]
    waiters.sort(key=lambda s: (-_v(s), s.get("sub_date") or "", s.get("_row", 0)))
    rank = next((i + 1 for i, s in enumerate(waiters) if s["_row"] == sub["_row"]), 0)
    out["waitlist_rank"] = rank
    out["waitlist_size"] = len(waiters)

    open_q = 0 if ts[qtier]["full"] else ts[qtier]["remaining"]
    out["top_spot_open"] = open_q > 0

    # Can they take their qualified tier now?
    # An OPEN spot is first-come: any qualified creator can lock it in immediately.
    # We never "reserve" an open spot for a higher-view waiter who isn't cashing out —
    # doing so blocked ready creators and mislabeled an open tier as "full". Priority
    # (waitlist_rank) only describes who's next once a tier is genuinely full (0 open).
    can_claim = open_q > 0

    ct = 0
    if can_claim:
        ct = qtier
    else:
        for t in range(qtier - 1, 0, -1):               # step down to best open lower tier
            if not ts[t]["full"]:
                ct = t
                out["is_step_down"] = True
                break

    if ct:
        out["cashout_tier"] = ct
        out["cashout_tier_name"] = TIER_CONFIG[ct]["name"]
        out["cashout_prize"] = TIER_CONFIG[ct]["prize"]
    return out


def _top_earner_waitlist(subs, settings):
    """Ranked list of active creators waiting for the top (Top Earner) tier — for the
    admin to see who's next when a spot frees. Highest views first, then earliest submit."""
    top = max(TIER_CONFIG)
    thr = TIER_CONFIG[top]["threshold"]

    def _v(s):
        return _int(s.get("co_impressions") or s.get("impressions", 0))

    def _holds(s):
        return _int(s.get("tier_qualified", 0)) == top and (
            (s.get("spot_claimed") == "Yes" and s.get("status") == "Verified")
            or s.get("cashed_out") == "Yes" or s.get("payout") == "Paid"
        )

    waiters = [
        s for s in subs
        if s.get("status") != "Disqualified" and s.get("cashed_out") != "Yes"
        and _v(s) >= thr and not _holds(s)
    ]
    waiters.sort(key=lambda s: (-_v(s), s.get("sub_date") or "", s.get("_row", 0)))
    ts = _tier_stats(subs, settings)
    open_spots = 0 if ts[top]["full"] else ts[top]["remaining"]
    return [
        {"rank": i + 1, "alias": s.get("alias", ""), "views": _v(s),
         "sub_date": (s.get("sub_date") or "")[:10],
         "eligible_now": (i + 1) <= open_spots}
        for i, s in enumerate(waiters)
    ], open_spots


def _compute_countdown(settings):
    """Return (game_start_iso, game_end_iso, label, state) for the live-board countdown.

    Dates come from settings (admin-configurable) with safe fallbacks — a missing or
    malformed value can never raise and 500 the public board.
    """
    def _parse(value, default):
        try:
            return _date.fromisoformat(str(value))
        except (ValueError, TypeError):
            return _date.fromisoformat(default)

    start = _parse(settings.get("campaign_start_date") or GAME_START_DEFAULT, GAME_START_DEFAULT)
    end   = _parse(settings.get("campaign_close_date") or GAME_END_DEFAULT,   GAME_END_DEFAULT)
    today = _date.today()
    days_left = (end - today).days

    # The close date is INCLUSIVE: the game runs through the END of that day. Emit the end
    # target as 23:59:59 (not bare date, which JS reads as midnight-at-the-START of the day
    # and would cut the whole last day off the countdown).
    end_target = f"{end.isoformat()}T23:59:59"

    if today < start:
        sd = (start - today).days
        label = f"⏰ Game starts in {sd} day{'s' if sd != 1 else ''}"
        state = "upcoming"
    elif days_left < 0:
        label = "🏁 Game Closed"
        state = "closed"
    elif days_left == 0:
        label = "⚠️ Last Chance — Final hours!"
        state = "last_chance"
    elif days_left <= 3:
        label = f"⚠️ Last Chance — {days_left} day{'s' if days_left != 1 else ''} left"
        state = "last_chance"
    elif days_left <= 7:
        label = f"🔥 Final Week — {days_left} days left"
        state = "final_week"
    else:
        label = f"⏰ {days_left} days remaining"
        state = "active"
    return start.isoformat(), end_target, label, state


def _build_leaderboard(subs, settings):
    """Public leaderboard rows — alias / platform / LATEST views / tier only, no private
    data. Shared by the initial server render (public_live) AND the 30s API refresh
    (api_board_data) so the board can never 'flip' between the two. Uses the latest
    submitted view count (co_impressions when present, else the original).

    Each row also carries a "challenger" standing: if the creator qualifies (by views) for
    a tier that is currently FULL, isn't cashed out, and holds a waitlist rank, they're
    shown as next in line for that tier's prize (highest views / earliest submit wins a
    freed spot). challenger_* is derived from _cashout_options — no PII, just rank + prize."""
    lb = []
    for s in subs:
        if s["status"] == "Disqualified":
            continue
        o = _cashout_options(subs, settings, s)
        qt = o["qualified_tier"]
        cashed = (s["cashed_out"] == "Yes" or s.get("payout") == "Paid")
        # Display tier: for a cashed-out creator, the tier they actually cashed at; for an
        # active creator, the tier they can ACTUALLY claim right now (step-down aware) — never
        # a full tier they can't win (e.g. don't label a challenger "Top Earner").
        display_tier = s.get("cash_out_tier", "") if cashed else o.get("cashout_tier_name", "")
        row = {
            "alias":       s["alias"],
            "platform":    s.get("platform", ""),
            "impressions": _int(s.get("co_impressions") or s.get("impressions", 0)),
            "tier_name":   display_tier,
            "status":      s["status"],
            "cashed_out":  s["cashed_out"],
            "mower_bonus": _int(s.get("mower_bonus", 0)),
            "has_played_minigame": _int(s.get("has_played_minigame", 0)),
            "tier_prize":  TIER_CONFIG.get(_int(s.get("tier_qualified", 0)), {}).get("prize", 0),
            "challenger_tier_name": "",
            "challenger_prize": 0,
            "challenger_rank": 0,
        }
        # Challenger = qualifies for a FULL tier, not cashed out, holds a waitlist rank.
        # (Suppressed aliases render as a plain active row — see BOARD_CHALLENGER_SUPPRESS.)
        if (s.get("alias") not in BOARD_CHALLENGER_SUPPRESS
                and qt > 0 and not o["top_spot_open"] and s.get("cashed_out") != "Yes" and o["waitlist_rank"] > 0):
            row["challenger_tier_name"] = o["qualified_tier_name"]
            row["challenger_prize"] = TIER_CONFIG[qt]["prize"]
            row["challenger_rank"] = o["waitlist_rank"]
        lb.append(row)
    lb.sort(key=lambda x: x["impressions"], reverse=True)
    return lb


def _build_activity_feed(subs, settings):
    """Most-recent-first activity items. EVERY active player surfaces exactly one item,
    so the feed reflects the whole board (not just players in a cashout/bonus/played
    state). Shared by the initial render and the 30s refresh."""
    active = [s for s in subs if s["status"] != "Disqualified"]
    ordered = sorted(active, key=lambda x: x.get("co_date") or x.get("sub_date") or "", reverse=True)
    feed = []
    for s in ordered:
        bonus    = _int(s.get("mower_bonus", 0))
        tier     = (s.get("cash_out_tier") or "").replace("None", "").strip()
        unlocked = TIER_CONFIG.get(_int(s.get("tier_qualified", 0)), {}).get("name", "")
        if s["cashed_out"] == "Yes" or s.get("payout") == "Paid":
            sfx = f" + ${bonus} Bonus" if bonus > 0 else ""
            feed.append({"text": f"🏆 {s['alias']} locked in {tier or unlocked}{sfx}", "type": "cashout"})
        elif bonus > 0:
            feed.append({"text": f"🔧 {s['alias']} earned +${bonus} Bonus Bank", "type": "bonus"})
        elif s["status"] == "Overtime":
            feed.append({"text": f"⚡ {s['alias']} is in Overtime", "type": "overtime"})
        elif unlocked:
            # Show the tier they can ACTUALLY claim right now (step-down aware), not their
            # views-qualified tier — otherwise the feed overstates a full tier they can't win.
            avail = _cashout_options(subs, settings, s).get("cashout_tier_name", "")
            if avail:
                feed.append({"text": f"📈 {s['alias']} unlocked {avail}", "type": "qualified"})
            else:
                feed.append({"text": f"🎬 {s['alias']} is in the game", "type": "joined"})
        else:
            feed.append({"text": f"🎬 {s['alias']} joined the game", "type": "joined"})
        if len(feed) >= 8:
            break
    return feed


def _budget_stats(subs):
    tier_spent = sum(
        TIER_CONFIG[s["tier_qualified"]]["prize"]
        for s in subs
        if s["status"] == "Verified"
        and s["spot_claimed"] == "Yes"
        and _int(s["tier_qualified"]) in TIER_CONFIG
    )
    # Only count BPs who have actually been marked Paid as "cashed out securely"
    # Treat payout='Paid' as equivalent to cashed_out='Yes' for legacy records
    cashed_out_spent = sum(
        TIER_CONFIG[_int(s["tier_qualified"])]["prize"] + _int(s.get("mower_bonus", 0))
        for s in subs
        if (s["cashed_out"] == "Yes" or s.get("payout") == "Paid")
        and _int(s["tier_qualified"]) in TIER_CONFIG
    )
    wc_won = sum(1 for s in subs if s["wc_won"] == "Yes")
    wc_spent = wc_won * WILDCARD_PRIZE
    total = tier_spent + wc_spent
    return {
        "tier_spent":       tier_spent,
        "cashed_out_spent": cashed_out_spent,
        "wc_won":           wc_won,
        "wc_spent":         wc_spent,
        "total_spent":      total,
        "remaining":        max(0, TOTAL_BUDGET - total),
        "wc_remaining":     max(0, WILDCARD_CAP * WILDCARD_PRIZE - wc_spent),
    }


def _generate_alias(existing):
    colors = COLORS[:]
    animals = ANIMALS[:]
    random.shuffle(colors)
    random.shuffle(animals)
    for c in colors:
        for a in animals:
            alias = f"{c} {a}"
            if alias not in existing:
                return alias
    base = f"{COLORS[0]} {ANIMALS[0]}"
    i = 2
    while f"{base} {i}" in existing:
        i += 1
    return f"{base} {i}"


def _generate_code(existing_codes):
    """Generate a unique JSJ-XXXXXXX player code."""
    import string as _string
    while True:
        letters = ''.join(random.choices(_string.ascii_uppercase, k=3))
        digits  = ''.join(random.choices(_string.digits, k=4))
        code = f"JSJ-{letters}{digits}"
        if code not in existing_codes:
            return code


def _find_sub_by_code_or_email(subs, code="", email=""):
    """Look up a submission by player code (preferred) or email."""
    if code:
        return next((s for s in subs if s["plus_code"].upper() == code.upper()), None)
    if email:
        return next((s for s in subs if s["email"].lower() == email.lower()), None)
    return None


def _wildcard_seconds_remaining(settings):
    opened_at = settings.get("wildcard_window_opened_at")
    if not opened_at:
        return 0
    try:
        opened = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        closes = opened + timedelta(hours=24)
        remaining = (closes - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(remaining))
    except Exception:
        return 0


# ── Admin routes ──────────────────────────────────────────────────────────────

@payout_board_bp.route("/payout-board")
def admin_view():
    settings = _load_settings()
    try:
        subs = pb_db.read_all()
    except Exception as e:
        logger.error("PB admin read error: %s", e)
        subs = []

    tier_stats = _tier_stats(subs, settings)
    budget = _budget_stats(subs)

    total       = len(subs)
    cashed_out  = sum(1 for s in subs if s["cashed_out"] == "Yes")
    verified    = sum(1 for s in subs if s["status"] == "Verified")
    pending     = sum(1 for s in subs if s["status"] == "Pending")

    wc_eligible = [s for s in subs if s["wc_eligible"] == "Yes"]
    wc_entries  = [s for s in subs if s["wc_entry"] == "Yes"]
    wc_won_count = sum(1 for s in subs if s["wc_won"] == "Yes")

    all_full = all(t["full"] for t in tier_stats.values())
    budget_gone = budget["remaining"] <= 0
    if settings.get("wildcard_window_open"):
        campaign_status = "Wildcard Window"
    elif all_full or budget_gone:
        campaign_status = "Closed"
    else:
        campaign_status = "Active"

    top_waitlist, top_open = _top_earner_waitlist(subs, settings)

    return render_template(
        "payout_board_admin.html",
        subs=subs, settings=settings, tier_stats=tier_stats,
        budget=budget, total=total, cashed_out=cashed_out,
        verified=verified, pending=pending,
        wc_eligible=wc_eligible, wc_entries=wc_entries,
        wc_won_count=wc_won_count,
        campaign_status=campaign_status,
        tier_config=TIER_CONFIG,
        wildcard_cap=WILDCARD_CAP,
        wildcard_prize=WILDCARD_PRIZE,
        top_waitlist=top_waitlist,
        top_tier_name=TIER_CONFIG[max(TIER_CONFIG)]["name"],
        top_open_spots=top_open,
        payout_overrides=PAYOUT_OVERRIDES,
    )


@payout_board_bp.route("/payout-board/api/verify/<int:row>", methods=["POST"])
def api_verify_submission(row):
    """Verify a submission and assign tier — no cashout required (used by Screenshot Audit Queue)."""
    try:
        subs = pb_db.read_all()
        settings = _load_settings()
        sub = next((s for s in subs if s["_row"] == row), None)
        if not sub:
            return jsonify({"error": "Not found"}), 404

        impressions = _int(sub.get("co_impressions") or sub.get("impressions", 0))
        # If they've ALREADY cashed out, their tier is locked to the tier they cashed out
        # at (step-down/first-come result). Recomputing from views here would clobber that
        # and can oversell a full higher tier they qualify for by views but never took.
        if sub.get("cashed_out") == "Yes":
            tier = _int(sub.get("tier_qualified", 0)) or next(
                (k for k, v in TIER_CONFIG.items() if v["name"] == sub.get("cash_out_tier")), 0)
        else:
            tier = _calc_tier(impressions)
        tier_name = TIER_CONFIG.get(tier, {}).get("name", "None") if tier > 0 else "None"

        # Cap guard: only reserve a live-board spot if the tier still has room
        # (excluding this player, who may already hold a spot).
        others = [s for s in subs if s["_row"] != row]
        tier_full = tier > 0 and _tier_is_full(others, tier, settings)

        pb_db.set_field(row, "status", "Verified")
        spot_claimed = "No"
        if tier > 0:
            pb_db.set_field(row, "tier_qualified", tier)
            pb_db.set_field(row, "cash_out_tier", tier_name)
            spot_claimed = "No" if tier_full else "Yes"
            pb_db.set_field(row, "spot_claimed", spot_claimed)

            # Send verification notification email only on first verify (non-fatal)
            if sub["status"] != "Verified":
                try:
                    from email_sim import send_verification_notification
                    prize = TIER_CONFIG[tier]["prize"]
                    first = sub["name"].split()[0] if sub.get("name") else "there"
                    send_verification_notification(
                        sub["email"], first, sub["alias"], tier_name, prize, sub.get("plus_code", "")
                    )
                except Exception as _e:
                    logger.warning("Verify email failed (non-fatal): %s", _e)

        return jsonify({
            "ok": True, "tier": tier, "tier_name": tier_name,
            "spot_claimed": spot_claimed,
            "tier_full": tier_full,
            "warning": ("Tier is full — verified without claiming a spot." if tier_full else ""),
        })
    except Exception as e:
        logger.error("PB verify row %d: %s", row, e)
        return jsonify({"error": str(e)}), 500


@payout_board_bp.route("/payout-board/api/approve-cashout/<int:row>", methods=["POST"])
def api_approve_cashout(row):
    try:
        subs = pb_db.read_all()
        settings = _load_settings()
        sub = next((s for s in subs if s["_row"] == row), None)
        if not sub:
            return jsonify({"error": "Not found"}), 404

        # Approve confirms the tier the player ALREADY cashed out at (step-down/first-come
        # result recorded at cash-out). Never recompute from views — that would clobber a
        # step-down and oversell a full higher tier they qualify for but never cashed into.
        tier = _int(sub.get("tier_qualified", 0)) or next(
            (k for k, v in TIER_CONFIG.items() if v["name"] == sub.get("cash_out_tier")), 0)
        if tier <= 0:  # last-resort fallback for legacy records with no recorded tier
            tier = _calc_tier(sub["co_impressions"] or sub["impressions"])
        pb_db.set_cell(row, "K", tier)

        # Cap check must EXCLUDE this player — by cashout time they already hold a
        # reserved spot (cashed_out=Yes), so counting themselves would wrongly report
        # the tier full and strip the very spot they reserved.
        others = [s for s in subs if s["_row"] != row]
        spot = "No"
        if tier > 0 and not _tier_is_full(others, tier, settings):
            spot = "Yes"

        tier_name = TIER_CONFIG[tier]["name"] if tier > 0 else "None"
        pb_db.set_cell(row, "M", tier_name)
        pb_db.set_cell(row, "N", spot)
        pb_db.set_cell(row, "O", "Verified")

        # Only send the "you're approved for $X" email when a spot was actually
        # secured. If the tier is full (spot == "No") the payout is NOT confirmed —
        # emailing the full prize would promise money the campaign can't pay.
        warning = ""
        if tier > 0 and spot == "Yes":
            try:
                from email_sim import send_approval_notification
                prize = TIER_CONFIG[tier]["prize"] + _int(sub.get("mower_bonus", 0))
                first = sub["name"].split()[0] if sub.get("name") else "there"
                send_approval_notification(
                    sub["email"], first, sub["alias"],
                    tier_name, prize, sub.get("plus_code", ""),
                )
            except Exception as _e:
                logger.warning("Approval email failed (non-fatal): %s", _e)
        elif tier > 0 and spot == "No":
            warning = "Tier is full — spot NOT secured. Do not pay until a spot opens."

        return jsonify({"ok": True, "tier": tier, "spot": spot, "tier_name": tier_name, "warning": warning})
    except Exception as e:
        logger.error("PB approve cashout row %d: %s", row, e)
        return jsonify({"error": str(e)}), 500


@payout_board_bp.route("/payout-board/api/unverify/<int:row>", methods=["POST"])
def api_unverify(row):
    """Reopen the game for a player — undo an accidental Verify OR an unpaid cash out.
    Puts them back in as an active (Pending) player and releases the tier spot they
    were holding. Views, tier progress (tier_qualified), and any mower bonus are
    preserved, so they can keep growing and make their own decision again.

    Refuses if the player has already been PAID (a completed payout is not reversible
    here) or is Disqualified."""
    try:
        subs = pb_db.read_all()
        sub = next((s for s in subs if s["_row"] == row), None)
        if not sub:
            return jsonify({"error": "Not found"}), 404
        if sub.get("payout") == "Paid":
            return jsonify({"error": "This player has already been paid — their cash out can't be reopened."}), 400
        if sub.get("status") == "Disqualified":
            return jsonify({"error": "This player is disqualified. Use the game flow to reinstate."}), 400
        pb_db.set_field(row, "cashed_out", "No")         # undo any submitted (unpaid) cash out
        pb_db.set_field(row, "status", "Pending")         # back to normal active player
        pb_db.set_field(row, "spot_claimed", "No")        # release the tier spot on the board
        pb_db.set_field(row, "cash_out_tier", "None")      # not locked into a tier
        return jsonify({"ok": True})
    except Exception as e:
        logger.error("PB unverify row %d: %s", row, e)
        return jsonify({"error": str(e)}), 500


@payout_board_bp.route("/payout-board/api/notify-keep-playing/<int:row>", methods=["POST"])
def api_notify_keep_playing(row):
    """Admin nudge — email a creator that they haven't reached the next tier's view
    threshold yet (so they're placed at their current tier / were bumped down), and to
    keep growing their views. Targets the tier just above their current view-tier."""
    try:
        subs = pb_db.read_all()
        sub = next((s for s in subs if s["_row"] == row), None)
        if not sub:
            return jsonify({"error": "Not found"}), 404
        views  = _int(sub.get("co_impressions") or sub.get("impressions", 0))
        cur    = _calc_tier(views)
        top    = max(TIER_CONFIG)
        target = (cur + 1) if cur < top else 0
        if target == 0:
            return jsonify({"error": "This player already qualifies for the top tier — nothing higher to climb to."}), 400
        cur_name  = TIER_CONFIG[cur]["name"] if cur > 0 else ""
        cur_prize = TIER_CONFIG[cur]["prize"] if cur > 0 else 0
        tgt = TIER_CONFIG[target]
        views_needed = max(0, tgt["threshold"] - views)
        first = sub["name"].split()[0] if sub.get("name") else "there"
        sent = False
        try:
            from email_sim import send_bumped_keep_playing
            sent = send_bumped_keep_playing(
                sub["email"], first, sub["alias"], cur_name, cur_prize,
                tgt["name"], tgt["prize"], tgt["threshold"], views_needed, sub.get("plus_code", ""),
            )
        except Exception as _e:
            logger.warning("Keep-playing email failed (non-fatal): %s", _e)
        return jsonify({"ok": True, "sent": bool(sent), "target": tgt["name"], "views_needed": views_needed})
    except Exception as e:
        logger.error("PB notify-keep-playing row %d: %s", row, e)
        return jsonify({"error": str(e)}), 500


@payout_board_bp.route("/payout-board/api/notify-locked-offer/<int:row>", methods=["POST"])
def api_notify_locked_offer(row):
    """Admin action — email a challenger that the prize tiers are all locked in, but they can
    still take an open Entry Level spot and go Overtime to play Perfect Stripes for a chance at
    +$100 (up to $250). Read-only: does not change the record."""
    try:
        subs = pb_db.read_all()
        sub = next((s for s in subs if s["_row"] == row), None)
        if not sub:
            return jsonify({"error": "Not found"}), 404
        if sub.get("cashed_out") == "Yes":
            return jsonify({"error": "This player has already cashed out."}), 400
        if sub.get("status") == "Disqualified":
            return jsonify({"error": "This player is disqualified."}), 400
        first = sub["name"].split()[0] if sub.get("name") else "there"
        sent = False
        try:
            from email_sim import send_locked_offer
            sent = send_locked_offer(sub["email"], first, sub["alias"], sub.get("plus_code", ""))
        except Exception as _e:
            logger.warning("Locked-offer email failed (non-fatal): %s", _e)
        return jsonify({"ok": True, "sent": bool(sent)})
    except Exception as e:
        logger.error("PB notify-locked-offer row %d: %s", row, e)
        return jsonify({"error": str(e)}), 500


@payout_board_bp.route("/payout-board/api/reopen-and-nudge/<int:row>", methods=["POST"])
def api_reopen_and_nudge(row):
    """One click: put a bumped/short-of-tier creator back in the game (undo an unpaid
    cash out or verify, release their spot) AND email them a 'keep playing' nudge with
    how many more views they need for the next tier. Refuses if already Paid or DQ."""
    try:
        subs = pb_db.read_all()
        sub = next((s for s in subs if s["_row"] == row), None)
        if not sub:
            return jsonify({"error": "Not found"}), 404
        if sub.get("payout") == "Paid":
            return jsonify({"error": "This player has already been paid — their cash out can't be reopened."}), 400
        if sub.get("status") == "Disqualified":
            return jsonify({"error": "This player is disqualified. Use the game flow to reinstate."}), 400

        # 1) Reopen — back to active, spot released, not locked into a tier.
        pb_db.set_field(row, "cashed_out", "No")
        pb_db.set_field(row, "status", "Pending")
        pb_db.set_field(row, "spot_claimed", "No")
        pb_db.set_field(row, "cash_out_tier", "None")

        # 2) Keep-playing nudge email (targets the tier just above their current views).
        views  = _int(sub.get("co_impressions") or sub.get("impressions", 0))
        cur    = _calc_tier(views)
        top    = max(TIER_CONFIG)
        target = (cur + 1) if cur < top else 0
        sent = False
        views_needed = 0
        target_name = ""
        if target:
            tgt = TIER_CONFIG[target]
            target_name = tgt["name"]
            views_needed = max(0, tgt["threshold"] - views)
            first = sub["name"].split()[0] if sub.get("name") else "there"
            cur_name  = TIER_CONFIG[cur]["name"] if cur > 0 else ""
            cur_prize = TIER_CONFIG[cur]["prize"] if cur > 0 else 0
            try:
                from email_sim import send_bumped_keep_playing
                sent = send_bumped_keep_playing(
                    sub["email"], first, sub["alias"], cur_name, cur_prize,
                    tgt["name"], tgt["prize"], tgt["threshold"], views_needed, sub.get("plus_code", ""),
                )
            except Exception as _e:
                logger.warning("Reopen-and-nudge email failed (non-fatal): %s", _e)

        return jsonify({"ok": True, "reopened": True, "sent": bool(sent),
                        "target": target_name, "views_needed": views_needed})
    except Exception as e:
        logger.error("PB reopen-and-nudge row %d: %s", row, e)
        return jsonify({"error": str(e)}), 500


@payout_board_bp.route("/payout-board/api/disqualify/<int:row>", methods=["POST"])
def api_disqualify(row):
    try:
        pb_db.set_cell(row, "L", "No")
        pb_db.set_cell(row, "N", "No")
        pb_db.set_cell(row, "O", "Disqualified")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@payout_board_bp.route("/payout-board/api/set-tier/<int:row>", methods=["POST"])
def api_set_tier(row):
    """Admin-only repair: directly set the tier a player is recorded/paid at.
    Sets tier_qualified + cash_out_tier (and the payout amount follows from it). Used to
    correct records mis-tiered by an earlier bug. Warns (does not block) if the chosen
    tier is already at/over cap without this player, so a deliberate fix is never stopped."""
    data = request.get_json() or {}
    tier = _int(data.get("tier", 0))
    if tier != 0 and tier not in TIER_CONFIG:
        return jsonify({"error": "Invalid tier"}), 400
    try:
        subs = pb_db.read_all()
        settings = _load_settings()
        sub = next((s for s in subs if s["_row"] == row), None)
        if not sub:
            return jsonify({"error": "Not found"}), 404
        tier_name = TIER_CONFIG.get(tier, {}).get("name", "None") if tier > 0 else "None"
        pb_db.set_field(row, "tier_qualified", tier)
        pb_db.set_field(row, "cash_out_tier", tier_name)
        others = [s for s in subs if s["_row"] != row]
        warn = ""
        if tier > 0:
            claimed_others = _claimed_spots(others, tier)
            if claimed_others >= _effective_cap(tier, settings):
                warn = (f"Heads up: {tier_name} already has {claimed_others} claimed "
                        f"(cap {_effective_cap(tier, settings)}) before this player — it will read over cap.")
        return jsonify({"ok": True, "tier": tier, "tier_name": tier_name, "warning": warn})
    except Exception as e:
        logger.error("PB set-tier row %d: %s", row, e)
        return jsonify({"error": str(e)}), 500


@payout_board_bp.route("/payout-board/api/award-bonus/<int:row>", methods=["POST"])
def api_award_bonus(row):
    """Admin-only: manually grant the +$100 Perfect Stripes mower bonus to a player
    (goodwill / operator discretion). Stacks on top of their tier prize at cash-out and
    shows publicly as a mini-game win. Respects the $1,000 pool cap and won't double-grant.
    Sends the standard '+$100 Bonus Bank won' email."""
    try:
        subs = pb_db.read_all()
        sub = next((s for s in subs if s["_row"] == row), None)
        if not sub:
            return jsonify({"error": "Not found"}), 404
        if sub.get("status") == "Disqualified":
            return jsonify({"error": "Player is disqualified — reinstate before awarding a bonus."}), 400
        if _int(sub.get("mower_bonus", 0)) > 0:
            return jsonify({"error": "Player already has a mower bonus."}), 400
        if pb_db.total_mower_bonus() + MINIGAME_BONUS > MINIGAME_BONUS_POOL:
            return jsonify({"error": f"Mower bonus pool is fully claimed (${MINIGAME_BONUS_POOL} cap)."}), 400

        pb_db.set_field(row, "mower_bonus", MINIGAME_BONUS)
        pb_db.set_field(row, "has_played_minigame", 1)

        emailed = False
        try:
            from email_sim import send_mower_win
            first = sub.get("name", "").split()[0] if sub.get("name") else "there"
            emailed = bool(send_mower_win(sub["email"], first, sub["alias"], MINIGAME_BONUS, sub.get("plus_code", "")))
        except Exception as e:
            logger.warning("Award-bonus win email failed (non-fatal): %s", e)

        return jsonify({"ok": True, "bonus": MINIGAME_BONUS, "emailed": emailed})
    except Exception as e:
        logger.error("PB award-bonus: %s", e)
        return jsonify({"error": str(e)}), 500


@payout_board_bp.route("/payout-board/api/mark-paid/<int:row>", methods=["POST"])
def api_mark_paid(row):
    try:
        # Ensure tier_qualified is set from cash_out_tier name if missing
        subs = pb_db.read_all()
        sub = next((s for s in subs if s["_row"] == row), None)
        if sub and _int(sub.get("tier_qualified", 0)) == 0 and sub.get("cash_out_tier"):
            tier_num = next(
                (k for k, v in TIER_CONFIG.items() if v["name"] == sub["cash_out_tier"]),
                None
            )
            if tier_num:
                pb_db.set_field(row, "tier_qualified", tier_num)

        pb_db.set_cell(row, "P", "Paid")
        pb_db.set_field(row, "cashed_out", "Yes")   # complete the cashout cycle
        pb_db.set_field(row, "spot_claimed", "Yes")  # ensure spot stays claimed
        # status must be Verified for _claimed_spots() to count this BP in dot/tier calculations
        pb_db.set_field(row, "status", "Verified")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@payout_board_bp.route("/payout-board/api/approve-wildcard/<int:row>", methods=["POST"])
def api_approve_wildcard(row):
    try:
        subs = pb_db.read_all()
        wc_won_count = sum(1 for s in subs if s["wc_won"] == "Yes")
        if wc_won_count >= WILDCARD_CAP:
            return jsonify({"error": "Wildcard is full"}), 400
        pb_db.set_cell(row, "S", "Yes")
        pb_db.set_cell(row, "O", "Verified")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@payout_board_bp.route("/payout-board/api/close-tier/<int:tier>", methods=["POST"])
def api_close_tier(tier):
    if tier not in TIER_CONFIG:
        return jsonify({"error": "Invalid tier"}), 400
    s = _load_settings()
    closed = s.get("manually_closed_tiers", [])
    if tier not in closed:
        closed.append(tier)
    s["manually_closed_tiers"] = closed
    _save_settings(s)
    return jsonify({"ok": True})


@payout_board_bp.route("/payout-board/api/reopen-tier/<int:tier>", methods=["POST"])
def api_reopen_tier(tier):
    if tier not in TIER_CONFIG:
        return jsonify({"error": "Invalid tier"}), 400
    s = _load_settings()
    s["manually_closed_tiers"] = [t for t in s.get("manually_closed_tiers", []) if t != tier]
    _save_settings(s)
    return jsonify({"ok": True})


@payout_board_bp.route("/payout-board/api/toggle-board", methods=["POST"])
def api_toggle_board():
    s = _load_settings()
    s["board_visible"] = not s.get("board_visible", True)
    _save_settings(s)
    return jsonify({"ok": True, "visible": s["board_visible"]})


@payout_board_bp.route("/payout-board/api/toggle-locked-banner", methods=["POST"])
def api_toggle_locked_banner():
    """Toggle the public 'All cash prizes locked in' banner. Display-only — does not close
    any tier, so Entry Level (or any open tier) stays claimable underneath the banner."""
    s = _load_settings()
    s["cashouts_locked_banner"] = not s.get("cashouts_locked_banner", False)
    _save_settings(s)
    return jsonify({"ok": True, "locked": s["cashouts_locked_banner"]})


@payout_board_bp.route("/payout-board/api/toggle-game-lock", methods=["POST"])
def api_toggle_game_lock():
    """Lock/unlock the game to NEW entries. When locked, the public board shows a
    'game has ended' banner + message, the join form is hidden, and /payout-board/submit
    refuses new submissions. Existing players can still cash out / play (this only blocks
    joining), per the requested scope."""
    s = _load_settings()
    s["game_locked"] = not s.get("game_locked", False)
    _save_settings(s)
    return jsonify({"ok": True, "locked": s["game_locked"]})


@payout_board_bp.route("/payout-board/api/open-wildcard-window", methods=["POST"])
def api_open_wildcard():
    s = _load_settings()
    s["wildcard_window_open"] = True
    s["wildcard_window_opened_at"] = datetime.now(timezone.utc).isoformat()
    _save_settings(s)
    # Mark all non-cashed-out verified/pending BPs as wildcard eligible
    try:
        subs = pb_db.read_all()
        for sub in subs:
            if sub["cashed_out"] != "Yes" and sub["wc_eligible"] != "Yes":
                pb_db.set_cell(sub["_row"], "Q", "Yes")
    except Exception as e:
        logger.error("PB open wildcard: sheet update failed: %s", e)
    return jsonify({"ok": True})


@payout_board_bp.route("/payout-board/api/close-wildcard-window", methods=["POST"])
def api_close_wildcard():
    s = _load_settings()
    s["wildcard_window_open"] = False
    _save_settings(s)
    return jsonify({"ok": True})


@payout_board_bp.route("/payout-board/api/set-close-date", methods=["POST"])
def api_set_close_date():
    """Set the campaign start and/or close date. Dates are validated (YYYY-MM-DD) so a
    malformed value can never be stored and later crash the public board."""
    data = request.get_json() or {}
    s = _load_settings()

    def _validate(value):
        if value in (None, ""):
            return None, None  # clear
        try:
            return _date.fromisoformat(str(value)).isoformat(), None
        except (ValueError, TypeError):
            return None, f"Invalid date '{value}'. Use YYYY-MM-DD."

    if "start_date" in data:
        val, err = _validate(data.get("start_date"))
        if err:
            return jsonify({"error": err}), 400
        s["campaign_start_date"] = val

    if "close_date" in data:
        val, err = _validate(data.get("close_date"))
        if err:
            return jsonify({"error": err}), 400
        s["campaign_close_date"] = val

    # Sanity: end must not precede start
    try:
        st = s.get("campaign_start_date") or GAME_START_DEFAULT
        en = s.get("campaign_close_date") or GAME_END_DEFAULT
        if _date.fromisoformat(en) < _date.fromisoformat(st):
            return jsonify({"error": "Close date must be on or after the start date."}), 400
    except (ValueError, TypeError):
        pass

    _save_settings(s)
    return jsonify({
        "ok": True,
        "start_date": s.get("campaign_start_date"),
        "close_date": s.get("campaign_close_date"),
    })


@payout_board_bp.route("/payout-board/api/export-csv")
def api_export_csv():
    try:
        subs = pb_db.read_all()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([
        "Real Name", "Email", "Board Alias", "Niche", "Platform",
        "Post URL", "Screenshot URL", "Impressions at Submission",
        "Cash Out Impressions", "Cash Out Screenshot URL",
        "Tier Qualified", "Cashed Out", "Cash Out Tier",
        "Spot Claimed", "Submission Status", "Payout Status",
        "Wildcard Eligible", "Wildcard Entry", "Wildcard Won",
        "Submission Date", "Cash Out Date", "Notes", "Mower Bonus", "Player Code",
        "Payout Override $", "Override Reason",
    ])
    for s in subs:
        ov = PAYOUT_OVERRIDES.get(s.get("plus_code", ""), {})
        w.writerow([
            s["name"], s["email"], s["alias"], s["niche"], s["platform"],
            s["post_url"], s["screenshot_url"], s["impressions"],
            s["co_impressions"], s["co_screenshot"], s["tier_qualified"],
            s["cashed_out"], s["cash_out_tier"], s["spot_claimed"],
            s["status"], s["payout"], s["wc_eligible"], s["wc_entry"],
            s["wc_won"], s["sub_date"], s["co_date"], s["notes"],
            s.get("mower_bonus", 0), s.get("plus_code", ""),
            ov.get("amount", ""), ov.get("note", ""),
        ])
    out.seek(0)
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=payout_board.csv"},
    )


@payout_board_bp.route("/payout-board/api/reset-campaign", methods=["POST"])
def api_reset_campaign():
    if not (request.get_json() or {}).get("confirmed"):
        return jsonify({"error": "Confirmation required"}), 400
    try:
        pb_db.delete_all()
        _save_settings({
            "board_visible": True,
            "campaign_start_date": None, "campaign_close_date": None,
            "wildcard_window_open": False, "wildcard_window_opened_at": None,
            "manually_closed_tiers": [], "tier_caps": {}, "total_budget": TOTAL_BUDGET,
        })
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Public board data API ─────────────────────────────────────────────────────

@payout_board_bp.route("/payout-board/api/board-data")
def api_board_data():
    settings = _load_settings()
    if not settings.get("board_visible", True):
        return jsonify({"visible": False})

    try:
        subs = pb_db.read_all()
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    _apply_board_tier_corrections(subs)   # display-only overlay (see BOARD_TIER_CORRECTIONS)
    tier_stats = _tier_stats(subs, settings)

    # Public cashout board: cashed-out BPs, alias only
    # Include payout='Paid' so JS refresh matches server-rendered confirmed payouts
    cashouts = [
        {
            "alias":       s["alias"],
            "tier":        s["tier_qualified"],
            "tier_name":   s["cash_out_tier"] or TIER_CONFIG.get(_int(s["tier_qualified"]), {}).get("name", ""),
            "prize":       TIER_CONFIG.get(_int(s["tier_qualified"]), {}).get("prize", 0),
            "mower_bonus": _int(s.get("mower_bonus", 0)),
            "platform":    s.get("platform", ""),
            "co_date":     s.get("co_date") or s.get("sub_date") or "",
        }
        for s in subs
        if s["cashed_out"] == "Yes" or s.get("payout") == "Paid"
    ]
    cashouts.sort(key=lambda x: x["co_date"], reverse=True)

    _locked_by_name = {t["tier_name"]: int(t.get("locked", 0)) for t in pb_db.get_tiers()}
    _playing_names = {
        s.get("cash_out_tier")
        for s in subs
        if s.get("cash_out_tier") == "Double Shift"
        and s.get("cashed_out") == "No" and s.get("status") != "Disqualified"
    }
    tiers_pub = {
        str(t): {
            "name":        v["name"],
            "prize":       v["prize"],
            "threshold":   v["threshold"],
            "cap":         v["cap"],
            "claimed":     v["claimed"],
            "remaining":   v["remaining"],
            "full":        v["full"],
            "locked":      bool(_locked_by_name.get(v["name"], 0)),
            "playing_now": v["name"] in _playing_names,
        }
        for t, v in tier_stats.items()
    }

    wc_seconds = _wildcard_seconds_remaining(settings)

    # Leaderboard + activity feed — shared builders (same output as the initial render)
    leaderboard = _build_leaderboard(subs, settings)

    # ── Derived live stats ────────────────────────────────────────────────
    _active = [s for s in subs if s["status"] != "Disqualified"]
    overtime_count   = sum(1 for s in _active
                           if _int(s.get("has_played_minigame", 0)) and s["cashed_out"] == "No")
    games_played     = sum(1 for s in _active if _int(s.get("has_played_minigame", 0)))
    total_bonus_bank = sum(_int(s.get("mower_bonus", 0)) for s in _active)
    # Tier-pool "locked in" is TIER PRIZES ONLY. The mower Bonus Bank draws from its own
    # separate $1,000 pool (shown as bonus_awarded), so it must NOT be subtracted from the
    # tier pool — otherwise Remaining is understated by the awarded bonus amount.
    locked_in        = sum(
        TIER_CONFIG.get(_int(s["tier_qualified"]), {}).get("prize", 0)
        for s in _active if s["cashed_out"] == "Yes" or s.get("payout") == "Paid"
    )
    # Use the authoritative prizes + effective (admin-adjustable) caps, matching public_live.
    _api_total_pool = sum(
        cfg["prize"] * _effective_cap(t, settings)
        for t, cfg in TIER_CONFIG.items()
    )
    prize_pool_stats = {
        "total":         _api_total_pool,
        "locked_in":     locked_in,
        "remaining":     max(0, _api_total_pool - locked_in),
        "bonus_awarded": total_bonus_bank,
    }
    overtime_stats = {
        "players_in_overtime": overtime_count,
        "games_played":        games_played,
        "total_bonus":         total_bonus_bank,
    }
    activity_feed = _build_activity_feed(subs, settings)

    # Countdown — admin-configurable dates with safe fallbacks (never raises → never 500s)
    game_start, game_end, countdown_label, countdown_state = _compute_countdown(settings)
    if settings.get("game_locked", False):
        countdown_state, countdown_label = "closed", "🏁 Game Ended"

    return jsonify({
        "visible":           True,
        "tiers":             tiers_pub,
        "cashouts":          cashouts,
        "leaderboard":       leaderboard,
        "close_date":        settings.get("campaign_close_date"),
        "wildcard_open":     settings.get("wildcard_window_open", False),
        "cashouts_locked":   settings.get("cashouts_locked_banner", False),
        "game_locked":       settings.get("game_locked", False),
        "wildcard_seconds":  wc_seconds,
        "wildcard_spots_left": max(0, WILDCARD_CAP - sum(
            1 for s in subs if s["wc_entry"] == "Yes"
        )),
        "prize_pool_stats": prize_pool_stats,
        "overtime_stats":   overtime_stats,
        "activity_feed":    activity_feed,
        "game_start":       game_start,
        "game_end":         game_end,
        "countdown_label":  countdown_label,
        "countdown_state":  countdown_state,
    })


# ── Public live page ──────────────────────────────────────────────────────────

@payout_board_bp.route("/payout-board/live")
def public_live():
    settings = _load_settings()
    try:
        subs = pb_db.read_all()
    except Exception:
        subs = []

    _apply_board_tier_corrections(subs)   # display-only overlay (see BOARD_TIER_CORRECTIONS)
    tier_stats = _tier_stats(subs, settings)

    # Match the same filter as api_board_data so initial render ≡ 30s JS refresh
    cashouts = [
        s for s in subs
        if s["cashed_out"] == "Yes" or s.get("payout") == "Paid"
    ]
    for c in cashouts:
        c["prize"] = (
            TIER_CONFIG.get(_int(c["tier_qualified"]), {}).get("prize", 0)
            + _int(c.get("mower_bonus", 0))
        )
    cashouts.sort(key=lambda x: x["co_date"], reverse=True)

    wc_seconds = _wildcard_seconds_remaining(settings)
    wc_spots_left = max(0, WILDCARD_CAP - sum(
        1 for s in subs if s["wc_entry"] == "Yes"
    ))

    # Prizes/thresholds come from the authoritative TIER_CONFIG (never the DB) so the
    # displayed board can never diverge from the payout math. Caps honor the admin
    # override; the `locked` flag is the one thing still sourced from the DB.
    _locked_by_name = {t["tier_name"]: int(t.get("locked", 0)) for t in pb_db.get_tiers()}
    prize_tiers = [
        {
            "name":      cfg["name"],
            "prize":     cfg["prize"],
            "threshold": cfg["threshold"],
            "cap":       _effective_cap(t, settings),
            "locked":    _locked_by_name.get(cfg["name"], 0),
        }
        for t, cfg in TIER_CONFIG.items()
    ]
    # Use tier_stats (same as _claimed_spots / API / admin) so initial render
    # and 30s JS refresh always use the same spot count logic.
    spots_claimed = {v["name"]: v["claimed"] for v in tier_stats.values()}
    minigame_bonus = pb_db.get_minigame_bonus()
    total_prize_cap = sum(t["prize"] * t["cap"] for t in prize_tiers)

    # ── Derived live stats ────────────────────────────────────────────────
    _active = [s for s in subs if s["status"] != "Disqualified"]
    overtime_count   = sum(1 for s in _active
                           if _int(s.get("has_played_minigame", 0)) and s["cashed_out"] == "No")
    games_played     = sum(1 for s in _active if _int(s.get("has_played_minigame", 0)))
    total_bonus_bank = sum(_int(s.get("mower_bonus", 0)) for s in _active)
    # Tier-pool "locked in" is TIER PRIZES ONLY — the mower Bonus Bank is a separate pool
    # (shown as bonus_awarded) and must not reduce the tier pool's Remaining. Mirrors
    # api_board_data so the initial render and the 30s refresh always agree.
    locked_in        = sum(
        TIER_CONFIG.get(_int(s["tier_qualified"]), {}).get("prize", 0)
        for s in _active if s["cashed_out"] == "Yes" or s.get("payout") == "Paid"
    )
    prize_pool_stats = {
        "total":         total_prize_cap,
        "locked_in":     locked_in,
        "remaining":     max(0, total_prize_cap - locked_in),
        "bonus_awarded": total_bonus_bank,
    }
    overtime_stats = {
        "players_in_overtime": overtime_count,
        "games_played":        games_played,
        "total_bonus":         total_bonus_bank,
    }
    # Leaderboard + activity feed — shared builders (identical to the 30s API refresh,
    # so the initial paint never differs from what the refresh renders).
    leaderboard   = _build_leaderboard(subs, settings)
    activity_feed = _build_activity_feed(subs, settings)

    # Countdown — admin-configurable dates with safe fallbacks (never raises → never 500s)
    game_start, game_end, countdown_label, countdown_state = _compute_countdown(settings)
    if settings.get("game_locked", False):
        countdown_state, countdown_label = "closed", "🏁 Game Ended"

    _playing_names = {
        s.get("cash_out_tier")
        for s in subs
        if s.get("cash_out_tier") == "Double Shift"
        and s.get("cashed_out") == "No" and s.get("status") != "Disqualified"
    }
    board_data = {
        "tiers": {
            str(t): {
                "name":        v["name"],
                "prize":       v["prize"],
                "threshold":   v["threshold"],
                "cap":         v["cap"],
                "claimed":     v["claimed"],
                "remaining":   v["remaining"],
                "full":        v["full"],
                "locked":      bool(_locked_by_name.get(v["name"], 0)),
                "playing_now": v["name"] in _playing_names,
            }
            for t, v in tier_stats.items()
        },
        "cashouts": [
            {
                "alias":     c["alias"],
                "tier_name": c["cash_out_tier"],
                "prize":     c.get("prize", 0),
            }
            for c in cashouts
        ],
        "wildcard_open":    settings.get("wildcard_window_open", False),
        "wildcard_seconds": wc_seconds,
        "prize_pool_stats": prize_pool_stats,
        "overtime_stats":   overtime_stats,
        "activity_feed":    activity_feed,
    }

    return render_template(
        "payout_board_public.html",
        settings=settings,
        subs=subs,
        tier_stats=tier_stats,
        cashouts=cashouts,
        tier_config=TIER_CONFIG,
        wc_seconds=wc_seconds,
        wc_spots_left=wc_spots_left,
        errors={},
        form_data={},
        confirmed=False,
        alias="",
        plus_code="",
        prize_tiers=prize_tiers,
        spots_claimed=spots_claimed,
        minigame_bonus=minigame_bonus,
        total_prize_cap=total_prize_cap,
        board_data=board_data,
        leaderboard=leaderboard,
        prize_pool_stats=prize_pool_stats,
        overtime_stats=overtime_stats,
        activity_feed=activity_feed,
        game_start=game_start,
        game_end=game_end,
        countdown_label=countdown_label,
        countdown_state=countdown_state,
        cashouts_locked=settings.get("cashouts_locked_banner", False),
        game_locked=settings.get("game_locked", False),
    )


# ── Terms & Conditions (public) ───────────────────────────────────────────────

@payout_board_bp.route("/payout-board/terms")
def terms_page():
    """Public Official Contest Rules / Terms & Conditions page."""
    return render_template("payout_board_terms.html")


# ── Submission form ───────────────────────────────────────────────────────────

@payout_board_bp.route("/payout-board/submit", methods=["GET"])
def submit_form():
    settings = _load_settings()
    return render_template(
        "payout_board_submit.html",
        errors={}, form_data={}, settings=settings,
        confirmed=False, alias="", game_over=settings.get("game_locked", False),
    )


@payout_board_bp.route("/payout-board/submit", methods=["POST"])
def submit_post():
    settings = _load_settings()
    # Game locked → no new entries. Existing players' cash-outs/plays are unaffected.
    if settings.get("game_locked", False):
        return render_template(
            "payout_board_submit.html",
            errors={}, form_data={}, settings=settings,
            confirmed=False, alias="", game_over=True,
        )
    form = request.form
    files = request.files
    errors = {}

    first_name      = form.get("first_name", "").strip()
    last_name       = form.get("last_name", "").strip()
    email           = form.get("email", "").strip().lower()
    handle          = form.get("handle", "").strip()
    post_url        = form.get("post_url", "").strip()
    imp_raw         = form.get("impressions", "").strip()
    screenshot_file = files.get("screenshot") or files.get("screenshot_file")

    if not first_name:
        errors["first_name"] = "Required."
    if not last_name:
        errors["last_name"] = "Required."
    if not email or "@" not in email:
        errors["email"] = "Valid email required."
    if not post_url.startswith("https://"):
        errors["post_url"] = "Must be a valid https:// URL."
    impressions = _int(imp_raw)
    if impressions <= 0:
        errors["impressions"] = "Must be greater than 0."
    if not screenshot_file or not screenshot_file.filename:
        errors["screenshot"] = "Screenshot of view stats is required."
    elif not _valid_image_upload(screenshot_file):
        errors["screenshot"] = "Screenshot must be a valid image (JPG, PNG, GIF, WEBP or HEIC)."

    if errors:
        return render_template(
            "payout_board_submit.html",
            errors=errors, form_data=form, settings=settings,
            confirmed=False, alias="",
        )

    try:
        subs = pb_db.read_all()
    except Exception as e:
        return render_template(
            "payout_board_submit.html",
            errors={"general": "Service unavailable. Try again shortly."},
            form_data=form, settings=settings, confirmed=False, alias="",
        )

    if any(s["email"].lower() == email for s in subs):
        # Not an error — a returning creator tried to submit again. Show a friendly
        # "you're already in" screen (no alias/code, to avoid email enumeration).
        return render_template(
            "payout_board_submit.html",
            already_in=True, already_in_email=email,
            errors={}, form_data={}, settings=settings, confirmed=False, alias="",
        )

    # Screenshot upload — validated (image only) and saved under a uuid filename.
    final_screenshot, up_err = _save_image_upload(screenshot_file)
    if up_err:
        return render_template(
            "payout_board_submit.html",
            errors={"screenshot": up_err}, form_data=form, settings=settings,
            confirmed=False, alias="",
        )

    existing_aliases = {s["alias"] for s in subs if s["alias"]}
    existing_codes   = {s["plus_code"] for s in subs if s["plus_code"]}
    alias     = _generate_alias(existing_aliases)
    plus_code = _generate_code(existing_codes)
    full_name = f"{first_name} {last_name}".strip()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        pb_db.append_row({
            "name": full_name, "email": email, "alias": alias,
            "niche": "", "platform": form.get("platform", "").strip(),
            "post_url": post_url, "screenshot_url": final_screenshot,
            "impressions": impressions,
            "co_impressions": 0, "co_screenshot": "",
            "tier_qualified": 0,
            "cashed_out": "No", "cash_out_tier": "None", "spot_claimed": "No",
            "status": "Pending", "payout": "Unpaid",
            "wc_eligible": "No", "wc_entry": "No", "wc_won": "No",
            "sub_date": now, "co_date": "", "notes": "", "mower_bonus": 0,
            "plus_code": plus_code,
            "handle": handle,
            "cash_state": "CONDITIONAL",
            "submission_status": "Pending",
            "has_played_minigame": 0,
            "unlocked_tier": "",
            "claimed_tier": "",
        })
    except Exception as e:
        logger.error("PB submit append failed: %s", e)
        return render_template(
            "payout_board_submit.html",
            errors={"general": "Failed to save. Try again."},
            form_data=form, settings=settings, confirmed=False, alias="",
        )

    # Calculate tier up front — needed for both the email and the confirmation page.
    qualified_tier = _calc_tier(impressions)

    # Send welcome email — non-fatal, never blocks the confirmation page.
    try:
        from email_sim import send_submission_confirmation
        send_submission_confirmation(
            email, first_name, alias, plus_code, qualified_tier=qualified_tier
        )
    except Exception as e:
        logger.warning("WELCOME EMAIL FAILED to %s: %s", email, e)
    qualified_tier_name  = TIER_CONFIG[qualified_tier]["name"]  if qualified_tier > 0 else ""
    qualified_tier_prize = TIER_CONFIG[qualified_tier]["prize"] if qualified_tier > 0 else 0
    cashout_url = f"/payout-board/cashout?code={plus_code}"

    return render_template(
        "payout_board_submit.html",
        errors={}, form_data={}, settings=settings,
        confirmed=True, alias=alias, plus_code=plus_code,
        qualified_tier=qualified_tier,
        qualified_tier_name=qualified_tier_name,
        qualified_tier_prize=qualified_tier_prize,
        cashout_url=cashout_url,
    )


# ── Cash out status check (AJAX) ──────────────────────────────────────────────

@payout_board_bp.route("/payout-board/check-status", methods=["POST"])
def check_status():
    data  = request.get_json() or {}
    email = data.get("email", "").strip().lower()
    code  = data.get("code",  "").strip().upper()

    if not email and not code:
        return jsonify({"error": "Email or Player Code required"}), 400

    import re as _re
    if code:
        code_valid = bool(
            _re.match(r'^JSJ-[A-Z0-9]{7}$', code) or
            _re.match(r'^[A-Z0-9]{4}-[A-Z0-9]{4}$', code)
        )
        if not code_valid:
            return jsonify({"error": "Invalid Player Code format. Use JSJ-ABC1234 or XXXX-XXXX."}), 400

    try:
        subs = pb_db.read_all()
    except Exception as e:
        return jsonify({"error": "Service unavailable"}), 503

    sub = _find_sub_by_code_or_email(subs, code=code, email=email)
    if not sub:
        msg = "No submission found for this Player Code." if code else "No submission found for this email. Submit your post first."
        return jsonify({"found": False, "message": msg})

    # Only a request that presents the actual Player Code may see private fields
    # (code, real name, post URL, email). An email-only lookup can never harvest the
    # code — that would let anyone who knows a creator's email impersonate them.
    by_code = bool(code)

    if sub["cashed_out"] == "Yes":
        prize = TIER_CONFIG.get(_int(sub["tier_qualified"]), {}).get("prize", 0)
        resp = {
            "found": True, "already_cashed_out": True,
            "alias":      sub["alias"],
            "tier_name":  sub["cash_out_tier"],
            "prize":      prize,
            "message":    "You have already cashed out. Payout will be processed via the payout provider next pay period.",
        }
        if by_code:
            resp["plus_code"] = sub["plus_code"]
            resp["email"]     = sub["email"]
        return jsonify(resp)

    settings = _load_settings()
    tier_stats = _tier_stats(subs, settings)
    # Use updated views (co_impressions) if the BP has submitted an update, else original
    display_impressions = _int(sub.get("co_impressions") or sub.get("impressions", 0)) or _int(sub.get("impressions", 0))
    tier = _calc_tier(display_impressions)
    prize = TIER_CONFIG.get(tier, {}).get("prize", 0) if tier > 0 else 0
    tier_name = TIER_CONFIG.get(tier, {}).get("name", "") if tier > 0 else ""

    # Find next open tier above current
    open_tiers = [
        {"tier": t, "name": cfg["name"], "prize": cfg["prize"], "threshold": cfg["threshold"]}
        for t, cfg in TIER_CONFIG.items()
        if not tier_stats[t]["full"]
    ]

    tier_full     = tier > 0 and tier_stats[tier]["full"]
    bonus_bank    = _int(sub.get("mower_bonus", 0))
    overtime_active = sub.get("status") == "Overtime"
    can_play_game   = overtime_active and not _int(sub.get("has_played_minigame", 0))
    # Can they cash out *something* right now (their tier, or a step-down tier)?
    _co = _cashout_options(subs, settings, sub)

    resp = {
        "found":              True,
        "already_cashed_out": False,

        # ── Gamified / public-safe identity ──────────────────────────────────
        "alias":              sub["alias"],
        "tier":               tier,
        "tier_name":          tier_name,
        "prize":              prize,
        "tier_full":          tier_full,
        "spots_remaining":    tier_stats[tier]["remaining"] if tier > 0 else 0,
        "open_tiers":         open_tiers,
        "status":             sub["status"],
        "cashed_out":         sub.get("cashed_out", "No"),
        # qualified = they can cash out *some* open tier right now (their qualified tier
        # if a spot is theirs, else a step-down tier). No admin verification required —
        # admin reviews the screenshot at the cashout stage, not before.
        "qualified":          (
            _co["cashout_tier"] > 0
            and sub["status"] != "Disqualified"
        ),
        "cashout_tier":       _co["cashout_tier"],
        "cashout_tier_name":  _co["cashout_tier_name"],
        "cashout_prize":      _co["cashout_prize"],
        "is_step_down":       _co["is_step_down"],
        "waitlist_rank":      _co["waitlist_rank"],

        # ── Bonus & overtime history (non-identifying) ───────────────────────
        "bonus_bank":         bonus_bank,
        "has_played_minigame": _int(sub.get("has_played_minigame", 0)),
        "overtime_active":    overtime_active,
        "can_play_game":      can_play_game,
        "impressions":        display_impressions,
        "original_impressions": _int(sub.get("impressions", 0)),
        "co_impressions":     _int(sub.get("co_impressions", 0)),
    }

    # ── Private profile — only returned when the request presented the Player Code ──
    if by_code:
        resp.update({
            "plus_code":  sub["plus_code"],
            "email":      sub["email"],
            "name":       sub.get("name", ""),
            "platform":   sub.get("platform", ""),
            "post_url":   sub.get("post_url", ""),
            "row":        sub["_row"],
        })

    return jsonify(resp)


# ── Update views & screenshot (returning player re-submission) ────────────────

@payout_board_bp.route("/payout-board/update-views", methods=["POST"])
def update_views():
    """Allow a BP to upload a new screenshot with updated views using their player code.
    Updates co_impressions + co_screenshot. Tier is recalculated immediately from the new
    view count — no admin re-verification required. If the new tier is higher than the
    stored tier_qualified, resets has_played_minigame so the BP can go overtime again.
    Original submission record (alias, email, player code) is preserved.
    """
    code            = request.form.get("code", "").strip().upper()
    imp_raw         = request.form.get("impressions", "").strip()
    screenshot_file = request.files.get("screenshot_file")

    if not code:
        return jsonify({"error": "Player Code required"}), 400
    impressions = _int(imp_raw)
    if impressions <= 0:
        return jsonify({"error": "View count must be greater than 0"}), 400
    if not screenshot_file or not screenshot_file.filename:
        return jsonify({"error": "Screenshot is required"}), 400
    if not _valid_image_upload(screenshot_file):
        return jsonify({"error": "Screenshot must be a valid image (JPG, PNG, GIF, WEBP or HEIC)."}), 400

    try:
        subs = pb_db.read_all()
    except Exception as e:
        return jsonify({"error": "Service unavailable"}), 503

    sub = _find_sub_by_code_or_email(subs, code=code)
    if not sub:
        return jsonify({"error": "No submission found for this Player Code"}), 404
    if sub.get("cashed_out") == "Yes":
        return jsonify({"error": "You have already cashed out — no updates allowed"}), 400
    if sub.get("status") == "Disqualified":
        return jsonify({"error": "This account has been disqualified"}), 400

    # Save new screenshot (validated image only)
    new_screenshot, up_err = _save_image_upload(screenshot_file)
    if up_err:
        return jsonify({"error": up_err}), 400

    # Update DB — co_ fields preserve original submission untouched
    row_id = sub["_row"]
    now    = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pb_db.set_field(row_id, "co_impressions", impressions)
    pb_db.set_field(row_id, "co_screenshot",  new_screenshot)
    pb_db.set_field(row_id, "co_date",        now)
    # Fix 1: Do NOT gate on admin — tier eligibility is view-based, no admin re-verify.
    # Fix 4B: If the new tier is strictly higher, reset the mini-game flag so the BP can
    # go overtime again for the new tier. If they had already gone overtime (status=
    # 'Overtime'), clear that lock so a fresh Go Overtime is allowed on the higher tier.
    new_tier = _calc_tier(impressions)
    old_tier = _int(sub.get("tier_qualified", 0))
    if new_tier > old_tier:
        pb_db.set_field(row_id, "tier_qualified",    new_tier)
        pb_db.set_field(row_id, "has_played_minigame", 0)
        if sub.get("status") == "Overtime":
            pb_db.set_field(row_id, "status", "Pending")  # re-enable a fresh overtime
        logger.info("PB update-views: %s promoted to tier %d — mini-game unlocked", sub["alias"], new_tier)

    logger.info("PB update-views: %s updated to %d views", sub["alias"], impressions)
    return jsonify({
        "ok":      True,
        "alias":   sub["alias"],
        "message": "Views updated! Head to your decision page to see your updated tier.",
    })


# ── Mower bonus ──────────────────────────────────────────────────────────────

@payout_board_bp.route("/payout-board/mower-bonus", methods=["POST"])
def save_mower_bonus():
    """Award the +$100 mini-game bonus. Guarded so it can be granted AT MOST ONCE per
    overtime round — a replayed request, a mid-game refresh, or a raw curl cannot farm
    unlimited bonuses. Requires the Player Code (awards real money)."""
    data  = request.get_json() or {}
    code  = data.get("code",  "").strip().upper()
    if not code:
        return jsonify({"error": "Player Code required"}), 400
    try:
        subs = pb_db.read_all()
    except Exception:
        return jsonify({"error": "Service unavailable"}), 503
    sub = _find_sub_by_code_or_email(subs, code=code)
    if not sub:
        return jsonify({"error": "Not found"}), 404
    if sub["cashed_out"] == "Yes":
        return jsonify({"error": "Already cashed out"}), 400
    if sub.get("status") != "Overtime":
        return jsonify({"error": "No active overtime round — go overtime to play."}), 400
    # Defense-in-depth: only a player who actually qualifies for a cash tier can earn a bonus.
    if _calc_tier(_int(sub.get("co_impressions") or sub.get("impressions", 0))) <= 0:
        return jsonify({"error": "You must qualify for a cash tier before earning a bonus."}), 400

    round_no = _int(sub.get("overtime_round", 0))
    if round_no <= 0:
        return jsonify({"error": "No active overtime round."}), 400

    # Atomic award — one bonus per round AND never exceed the $1,000 total pool.
    awarded, new_bonus = pb_db.award_mower_bonus_once(
        sub["_row"], round_no, MINIGAME_BONUS, MINIGAME_BONUS_POOL
    )
    if not awarded:
        # Distinguish "pool fully claimed" from "already won this round" for the player.
        if pb_db.total_mower_bonus() + MINIGAME_BONUS > MINIGAME_BONUS_POOL:
            return jsonify({
                "ok": True, "bonus": 0, "bonus_bank": new_bonus, "pool_exhausted": True,
                "message": "The mini-game bonus pool has been fully claimed for this campaign.",
            })
        return jsonify({"ok": True, "bonus": 0, "bonus_bank": new_bonus, "already_awarded": True})

    try:
        from email_sim import send_mower_win
        first = sub.get("name", "").split()[0] if sub.get("name") else "there"
        send_mower_win(sub["email"], first, sub["alias"], MINIGAME_BONUS, sub.get("plus_code", ""))
    except Exception as e:
        logger.warning("Mower win email failed (non-fatal): %s", e)
    return jsonify({"ok": True, "bonus": MINIGAME_BONUS, "bonus_bank": new_bonus})


@payout_board_bp.route("/payout-board/minigame-played", methods=["POST"])
def minigame_played():
    """Mark has_played_minigame=1 on game completion (loss path — no bonus awarded)."""
    data  = request.get_json() or {}
    code  = data.get("code",  "").strip().upper()
    # Require the Player Code — an email-only caller could otherwise burn a victim's
    # one-per-round bonus opportunity (grief) by marking their round as played.
    if not code:
        return jsonify({"ok": True})
    try:
        subs = pb_db.read_all()
        sub  = _find_sub_by_code_or_email(subs, code=code)
        if sub and sub.get("cashed_out") != "Yes":
            pb_db.set_field(sub["_row"], "has_played_minigame", 1)
            # A completed loss consumes this overtime round's bonus opportunity, so a
            # follow-up curl to /mower-bonus cannot claim a win after losing.
            if sub.get("status") == "Overtime":
                round_no = _int(sub.get("overtime_round", 0))
                if round_no > 0 and _int(sub.get("bonus_awarded_round", 0)) < round_no:
                    pb_db.set_field(sub["_row"], "bonus_awarded_round", round_no)
    except Exception:
        pass  # silent — non-critical
    return jsonify({"ok": True})


# ── Go Overtime ───────────────────────────────────────────────────────────────

@payout_board_bp.route("/payout-board/go-overtime", methods=["POST"])
def go_overtime():
    """Called the moment a BP confirms GO OVERTIME on the cashout page.
    Releases their tier slot back to the live board and marks status=Overtime
    so admin can see they committed to overtime before re-submitting.
    """
    data  = request.get_json() or {}
    code  = data.get("code",  "").strip().upper()
    # Releasing a tier spot is destructive → require the Player Code, not a bare email
    # (a rival who knew your email could otherwise release your spot).
    if not code:
        return jsonify({"error": "Player Code required"}), 400
    try:
        subs = pb_db.read_all()
    except Exception:
        return jsonify({"error": "Service unavailable"}), 503

    sub = _find_sub_by_code_or_email(subs, code=code)
    if not sub:
        return jsonify({"error": "Not found"}), 404
    if sub.get("cashed_out") == "Yes":
        return jsonify({"error": "Already cashed out"}), 400
    if sub.get("status") == "Disqualified":
        return jsonify({"error": "Account disqualified"}), 400
    # Can't re-enter overtime in the same tier — this is what blocks bonus-round farming.
    # A new overtime attempt requires reaching a HIGHER tier via updated views first.
    if sub.get("status") == "Overtime":
        return jsonify({"error": "You're already in overtime. Grow your views to reach a higher tier for another attempt."}), 400
    # Must actually qualify for a cash tier to release a spot / play the bonus game.
    _ov_tier = _calc_tier(_int(sub.get("co_impressions") or sub.get("impressions", 0)))
    if _ov_tier <= 0:
        return jsonify({"error": "You need at least 500 views to qualify before going overtime."}), 400
    # No overtime at the top tier, UNLESS the top tier is full and this player can't claim
    # it. A top-tier creator who could still take an open top spot should just take it (no
    # point releasing/gambling). But a stuck challenger (top tier full, no spot for them)
    # has no higher tier to chase and Overtime is their only route to the Perfect Stripes
    # consolation — so allow it. Block only when the top tier still has room for them.
    _ov_settings = _load_settings()
    _ov_others = [s for s in subs if s["_row"] != sub["_row"]]
    if _ov_tier >= max(TIER_CONFIG) and not _tier_is_full(_ov_others, _ov_tier, _ov_settings):
        return jsonify({"error": "You're already at the top tier — there's no higher tier to go overtime for. Cash out when a spot is available, or hold your place in line."}), 400

    row = sub["_row"]
    round_no = _int(sub.get("overtime_round", 0)) + 1
    pb_db.set_field(row, "spot_claimed",        "No")       # release slot on live board
    pb_db.set_field(row, "status",              "Overtime") # admin-visible state
    pb_db.set_field(row, "has_played_minigame", 0)          # reset — new overtime = new mini-game attempt
    pb_db.set_field(row, "overtime_round",      round_no)   # advances the one-bonus-per-round guard
    logger.info("PB go-overtime: %s released %s spot (round %d)", sub["alias"], sub.get("cash_out_tier", ""), round_no)
    try:
        from email_sim import send_overtime_confirmation
        first = sub.get("name", "").split()[0] if sub.get("name") else "there"
        send_overtime_confirmation(sub["email"], first, sub["alias"], sub.get("cash_out_tier", ""), sub.get("plus_code", ""))
    except Exception as e:
        logger.warning("Overtime email failed (non-fatal): %s", e)
    return jsonify({
        "ok":        True,
        "tier":      sub.get("cash_out_tier", ""),
        "alias":     sub["alias"],
        "bonus_bank": _int(sub.get("mower_bonus", 0)),
    })


# ── Ecosystem Sandbox Tester ──────────────────────────────────────────────────

_SANDBOX_CREATORS = [
    {"first": "Jordan", "last": "Rivera", "email": "test.jordan@sandbox.com",
     "platform": "TikTok",         "views": 650,  "tier": 1, "status": "Pending"},
    {"first": "Morgan", "last": "Chen",   "email": "test.morgan@sandbox.com",
     "platform": "Instagram",      "views": 2800, "tier": 2, "status": "Verified"},
    {"first": "Riley",  "last": "Patel",  "email": "test.riley@sandbox.com",
     "platform": "YouTube Shorts", "views": 4200, "tier": 3, "status": "Verified"},
    {"first": "Casey",  "last": "Walsh",  "email": "test.casey@sandbox.com",
     "platform": "TikTok",         "views": 8500, "tier": 4, "status": "Pending"},
]


def _sandbox_row_base(now):
    return {
        "niche": "", "post_url": "https://example.com/test", "screenshot_url": "",
        "co_impressions": 0, "co_screenshot": "", "tier_qualified": 0,
        "cashed_out": "No", "cash_out_tier": "None", "spot_claimed": "No",
        "status": "Pending", "payout": "Unpaid",
        "wc_eligible": "No", "wc_entry": "No", "wc_won": "No",
        "sub_date": now, "co_date": "", "mower_bonus": 0,
        "handle": "", "cash_state": "CONDITIONAL", "submission_status": "Pending",
        "has_played_minigame": 0, "unlocked_tier": "", "claimed_tier": "",
    }


@payout_board_bp.route("/payout-board/api/repair-paid", methods=["POST"])
def repair_paid_records():
    """Fix legacy Paid records missing cashed_out/spot_claimed/status/tier_qualified."""
    subs = pb_db.read_all()
    fixed = []
    for s in subs:
        if s.get("payout") != "Paid":
            continue
        row = s["_row"]
        changes = []
        if s.get("cashed_out") != "Yes":
            pb_db.set_field(row, "cashed_out", "Yes")
            changes.append("cashed_out=Yes")
        if s.get("spot_claimed") != "Yes":
            pb_db.set_field(row, "spot_claimed", "Yes")
            changes.append("spot_claimed=Yes")
        if s.get("status") != "Verified":
            pb_db.set_field(row, "status", "Verified")
            changes.append("status=Verified")
        if _int(s.get("tier_qualified", 0)) == 0 and s.get("cash_out_tier"):
            tier_num = next((k for k, v in TIER_CONFIG.items() if v["name"] == s["cash_out_tier"]), None)
            if tier_num:
                pb_db.set_field(row, "tier_qualified", tier_num)
                changes.append(f"tier_qualified={tier_num}")
        if changes:
            fixed.append({"alias": s.get("alias"), "row": row, "changes": changes})
    return jsonify({"ok": True, "fixed": len(fixed), "records": fixed})


@payout_board_bp.route("/payout-board/sandbox/seed-creators", methods=["POST"])
def sandbox_seed_creators():
    """Seed 4 diverse test creators across all 4 tiers."""
    try:
        subs = pb_db.read_all()
    except Exception as e:
        return jsonify({"error": str(e)}), 503

    existing_emails   = {s["email"].lower() for s in subs}
    existing_aliases  = {s["alias"] for s in subs if s["alias"]}
    existing_codes    = {s["plus_code"] for s in subs if s["plus_code"]}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    created = []
    for c in _SANDBOX_CREATORS:
        if c["email"] in existing_emails:
            continue
        alias = _generate_alias(existing_aliases)
        code  = _generate_code(existing_codes)
        existing_aliases.add(alias)
        existing_codes.add(code)
        existing_emails.add(c["email"])
        tier_name = TIER_CONFIG.get(c["tier"], {}).get("name", "None")

        row = _sandbox_row_base(now)
        row.update({
            "name":         f"{c['first']} {c['last']}",
            "email":        c["email"],
            "alias":        alias,
            "platform":     c["platform"],
            "impressions":  c["views"],
            "tier_qualified": c["tier"],
            "cash_out_tier":  tier_name,
            "status":       c["status"],
            # pre-verified creators already hold a slot on the live board
            "spot_claimed": "Yes" if c["status"] == "Verified" else "No",
            "notes":        "SANDBOX TEST ACCOUNT",
            "plus_code":    code,
        })
        pb_db.append_row(row)
        created.append({
            "name":   f"{c['first']} {c['last']}",
            "email":  c["email"],
            "alias":  alias,
            "code":   code,
            "views":  c["views"],
            "tier":   tier_name,
            "status": c["status"],
        })

    return jsonify({"ok": True, "created": created})


@payout_board_bp.route("/payout-board/sandbox/seed-tier-caps", methods=["POST"])
def sandbox_seed_tier_caps():
    """Fill every tier to capacity with cashed-out sandbox submissions."""
    try:
        subs = pb_db.read_all()
    except Exception as e:
        return jsonify({"error": str(e)}), 503

    existing_emails  = {s["email"].lower() for s in subs}
    existing_aliases = {s["alias"] for s in subs if s["alias"]}
    existing_codes   = {s["plus_code"] for s in subs if s["plus_code"]}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    capped  = []

    for tier_num, tc in TIER_CONFIG.items():
        tier_name = tc["name"]
        already = sum(1 for s in subs
                      if s.get("cash_out_tier") == tier_name and s.get("cashed_out") == "Yes")
        to_add  = max(0, tc["cap"] - already)
        slots_added = 0

        for i in range(to_add):
            email = f"cap.t{tier_num}.{i}@sandbox.com"
            if email in existing_emails:
                continue
            alias = _generate_alias(existing_aliases)
            code  = _generate_code(existing_codes)
            existing_aliases.add(alias)
            existing_codes.add(code)
            existing_emails.add(email)

            row = _sandbox_row_base(now)
            row.update({
                "name":           f"Cap Test {tier_num}-{i}",
                "email":          email,
                "alias":          alias,
                "platform":       "TikTok",
                "impressions":    tc["threshold"] + 100,
                "tier_qualified": tier_num,
                "cashed_out":     "Yes",
                "cash_out_tier":  tier_name,
                "spot_claimed":   "Yes",
                "status":         "Verified",
                "co_date":        now,
                "notes":          "SANDBOX CAP SIMULATION",
                "plus_code":      code,
            })
            pb_db.append_row(row)
            slots_added += 1

        if slots_added:
            capped.append({"tier": tier_name, "slots_filled": slots_added})

    return jsonify({"ok": True, "capped": capped})


@payout_board_bp.route("/payout-board/sandbox/inject-fraud", methods=["POST"])
def sandbox_inject_fraud():
    """Inject duplicate-alias and implausible-views submissions to surface in admin radar."""
    try:
        subs = pb_db.read_all()
    except Exception as e:
        return jsonify({"error": str(e)}), 503

    existing_emails  = {s["email"].lower() for s in subs}
    existing_aliases = {s["alias"] for s in subs if s["alias"]}
    existing_codes   = {s["plus_code"] for s in subs if s["plus_code"]}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Re-use an existing alias (or a fixed one if DB is empty) for the duplicate
    dup_alias = next(iter(existing_aliases), "Gold Falcon")

    fraud_entries = [
        {
            "email": "fraud.test.dup@sandbox.com",
            "alias": dup_alias,
            "views": 3200,
            "flag":  "Duplicate alias injection",
        },
        {
            "email": "fraud.test.views@sandbox.com",
            "alias": _generate_alias(existing_aliases | {dup_alias}),
            "views": 99999,
            "flag":  "Implausible view count (99,999)",
        },
    ]

    injected = []
    for e in fraud_entries:
        if e["email"] in existing_emails:
            continue
        code = _generate_code(existing_codes)
        existing_codes.add(code)
        existing_emails.add(e["email"])

        row = _sandbox_row_base(now)
        row.update({
            "name":        "Fraud Test",
            "email":       e["email"],
            "alias":       e["alias"],
            "platform":    "TikTok",
            "impressions": e["views"],
            "notes":       f"FRAUD: {e['flag']}",
            "plus_code":   code,
        })
        pb_db.append_row(row)
        injected.append({"email": e["email"], "alias": e["alias"], "flag": e["flag"], "code": code})

    return jsonify({"ok": True, "injected": injected})


# ── Cash out flow ─────────────────────────────────────────────────────────────

@payout_board_bp.route("/payout-board/cashout", methods=["GET"])
def cashout_page():
    settings = _load_settings()
    email = request.args.get("email", "").strip().lower()
    code  = request.args.get("code",  "").strip().upper()
    sub   = None
    computed_tier = 0
    tier_stats = {}
    errors = {}
    opts = {}

    effective_impressions = 0
    if email or code:
        try:
            subs = pb_db.read_all()
            sub = _find_sub_by_code_or_email(subs, code=code, email=email)
            if sub:
                # Fix 2: effective_impressions uses the latest submitted count (co_ if present)
                effective_impressions = _int(sub.get("co_impressions") or sub.get("impressions", 0))
                computed_tier = _calc_tier(effective_impressions)
                tier_stats    = _tier_stats(subs, settings)
                opts          = _cashout_options(subs, settings, sub)
                email = sub["email"]   # normalise so template always has email
                code  = sub["plus_code"]
            else:
                errors = {"email": "No submission found. Check your Player Code or email."}
        except Exception as e:
            logger.error("PB cashout GET lookup: %s", e)
            errors = {"general": "Service unavailable. Try again shortly."}

    return render_template(
        "payout_board_cashout.html",
        settings=settings,
        prefill_email=email,
        prefill_code=code,
        tier_config=TIER_CONFIG,
        confirmed=False,
        errors=errors,
        sub=sub,
        computed_tier=computed_tier,
        tier_stats=tier_stats,
        mower_bonus=sub["mower_bonus"] if sub else 0,
        plus_code=sub["plus_code"] if sub else "",
        effective_impressions=effective_impressions,
        **opts,
    )


@payout_board_bp.route("/payout-board/cashout", methods=["POST"])
def cashout_confirm():
    settings = _load_settings()
    form = request.form
    files = request.files
    email              = form.get("email", "").strip().lower()
    code               = form.get("code",  "").strip().upper()
    co_imp_raw         = form.get("co_impressions", "").strip()
    co_screenshot_file = files.get("co_screenshot_file")
    errors = {}

    co_impressions = _int(co_imp_raw)
    # Views/screenshot validation deferred — pre-game form may have already saved them
    # (errors filled after sub lookup below if still missing)

    def _err_render(**kwargs):
        return render_template(
            "payout_board_cashout.html",
            settings=settings, prefill_email=email, prefill_code=code,
            tier_config=TIER_CONFIG, confirmed=False,
            sub=None, computed_tier=0, tier_stats={}, mower_bonus=0, plus_code=code,
            **kwargs,
        )

    # Look up sub early — needed to re-render eligible state on validation errors
    try:
        subs = pb_db.read_all()
    except Exception:
        return _err_render(errors={"general": "Service unavailable. Try again shortly."})

    sub = _find_sub_by_code_or_email(subs, code=code, email=email)
    if not sub:
        return _err_render(errors={"email": "No submission found. Check your Player Code or email."})

    # Normalise to sub's actual email/code
    email = sub["email"]
    code  = sub["plus_code"]

    if sub["cashed_out"] == "Yes":
        return render_template(
            "payout_board_cashout.html",
            settings=settings, prefill_email=email, prefill_code=code,
            tier_config=TIER_CONFIG, confirmed=False,
            errors={"email": "You have already cashed out."},
            sub=sub, computed_tier=0, tier_stats={}, mower_bonus=0, plus_code=code,
        )

    if sub.get("status") == "Disqualified":
        return render_template(
            "payout_board_cashout.html",
            settings=settings, prefill_email=email, prefill_code=code,
            tier_config=TIER_CONFIG, confirmed=False,
            errors={"general": "This account has been disqualified and cannot cash out."},
            sub=sub, computed_tier=0, tier_stats={}, mower_bonus=0, plus_code=code,
        )

    computed_tier = _calc_tier(_int(sub.get("co_impressions") or sub.get("impressions", 0)))
    tier_stats = _tier_stats(subs, settings)
    mower_bonus = int(sub.get("mower_bonus", 0) or 0)

    # If pre-game form already saved views/screenshot, use those values
    if co_impressions <= 0 and _int(sub.get("co_impressions", 0)) > 0:
        co_impressions = _int(sub["co_impressions"])
    if co_impressions <= 0:
        errors["co_impressions"] = "Current view count required."

    has_existing_screenshot = bool(sub.get("co_screenshot"))
    has_new_screenshot      = bool(co_screenshot_file and co_screenshot_file.filename)
    if not has_new_screenshot and not has_existing_screenshot:
        errors["co_screenshot"] = "Screenshot required to confirm cash out."
    elif has_new_screenshot and not _valid_image_upload(co_screenshot_file):
        errors["co_screenshot"] = "Screenshot must be a valid image (JPG, PNG, GIF, WEBP or HEIC)."

    # Step-down + Top Earner priority: figure out the tier they can actually cash at now.
    _eff_views = co_impressions if co_impressions > 0 else _int(sub.get("co_impressions") or sub.get("impressions", 0))
    opts = _cashout_options(subs, settings, sub, views=_eff_views)

    if errors:
        return render_template(
            "payout_board_cashout.html",
            settings=settings, prefill_email=email, prefill_code=code,
            tier_config=TIER_CONFIG, confirmed=False,
            errors=errors,
            sub=sub, computed_tier=computed_tier, tier_stats=tier_stats,
            mower_bonus=mower_bonus, plus_code=code, **opts,
        )

    # Screenshot — new upload wins; fall back to pre-game saved screenshot.
    # Validated image only (same whitelist + magic-byte check as submit/update-views).
    final_co_screenshot = sub.get("co_screenshot", "")
    if has_new_screenshot:
        final_co_screenshot, up_err = _save_image_upload(co_screenshot_file)
        if up_err:
            return render_template(
                "payout_board_cashout.html",
                settings=settings, prefill_email=email, prefill_code=code,
                tier_config=TIER_CONFIG, confirmed=False,
                errors={"co_screenshot": up_err},
                sub=sub, computed_tier=computed_tier, tier_stats=tier_stats,
                mower_bonus=mower_bonus, plus_code=code, **opts,
            )

    row = sub["_row"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Cash out at the tier they can take RIGHT NOW: their qualified tier if they hold
    # priority for an open spot, otherwise the best open tier below it (step-down).
    tier = opts["cashout_tier"]
    tier_name = opts["cashout_tier_name"] or "None"

    # Views must still qualify for a cash tier at all.
    if opts["qualified_tier"] <= 0:
        return render_template(
            "payout_board_cashout.html",
            settings=settings, prefill_email=email, prefill_code=code,
            tier_config=TIER_CONFIG, confirmed=False,
            errors={"general": "Your current view count no longer qualifies for a cash tier. Submit updated views to qualify."},
            sub=sub, computed_tier=computed_tier, tier_stats=tier_stats,
            mower_bonus=mower_bonus, plus_code=code, **opts,
        )

    # Qualified, but every tier at/below is full (or higher-ranked creators hold the
    # open spots) — they're waitlisted, not cashed out.
    if tier <= 0:
        _ql = opts["qualified_tier_name"]
        _rk = opts["waitlist_rank"]
        _pos = f"You're #{_rk} in line for {_ql}. " if _rk else ""
        return render_template(
            "payout_board_cashout.html",
            settings=settings, prefill_email=email, prefill_code=code,
            tier_config=TIER_CONFIG, confirmed=False,
            errors={"general": f"Every tier you qualify for is full right now. {_pos}"
                               f"A spot opens the moment a player goes Overtime, and you're covered by the Wildcard at campaign close."},
            sub=sub, computed_tier=computed_tier, tier_stats=tier_stats,
            mower_bonus=mower_bonus, plus_code=code, **opts,
        )

    # Final safety — NEVER oversell the chosen tier (exclude this player from the count).
    others = [s for s in subs if s["_row"] != row]
    if _tier_is_full(others, tier, settings):
        return render_template(
            "payout_board_cashout.html",
            settings=settings, prefill_email=email, prefill_code=code,
            tier_config=TIER_CONFIG, confirmed=False,
            errors={"general": f"The {tier_name} tier just filled up — every spot is claimed. "
                               f"Grow your views to reach a higher tier, or check back if a spot reopens."},
            sub=sub, computed_tier=computed_tier, tier_stats=tier_stats,
            mower_bonus=mower_bonus, plus_code=code, **opts,
        )

    pb_db.set_cell(row, "I", co_impressions)
    pb_db.set_cell(row, "J", final_co_screenshot)
    pb_db.set_cell(row, "K", tier)
    pb_db.set_cell(row, "L", "Yes")
    pb_db.set_cell(row, "M", tier_name)
    pb_db.set_cell(row, "N", "Yes")   # reserve the tier spot immediately (prevents oversell)
    pb_db.set_cell(row, "O", "Pending")
    pb_db.set_cell(row, "U", now)

    base_prize = TIER_CONFIG[tier]["prize"] if tier > 0 else 0
    prize = base_prize + mower_bonus

    # Send cashout-received email (non-fatal)
    try:
        from email_sim import send_cashout_received
        first = sub["name"].split()[0] if sub.get("name") else "there"
        send_cashout_received(sub["email"], first, sub["alias"], tier_name, prize, code)
    except Exception as _e:
        logger.warning("Cashout received email failed (non-fatal): %s", _e)

    return render_template(
        "payout_board_cashout.html",
        settings=settings, prefill_email=email, prefill_code=code,
        tier_config=TIER_CONFIG, confirmed=True,
        errors={},
        alias=sub["alias"],
        tier=tier,
        tier_name=tier_name,
        prize=prize,
        sub=sub, computed_tier=computed_tier, tier_stats=tier_stats,
        mower_bonus=mower_bonus, plus_code=code,
    )


# ── Wildcard flow ─────────────────────────────────────────────────────────────

@payout_board_bp.route("/payout-board/wildcard", methods=["GET"])
def wildcard_page():
    settings = _load_settings()
    if not settings.get("wildcard_window_open"):
        return redirect(url_for("payout_board.public_live"))
    wc_seconds = _wildcard_seconds_remaining(settings)
    return render_template(
        "payout_board_wildcard.html",
        settings=settings, errors={}, form_data={},
        confirmed=False, alias="", wc_seconds=wc_seconds,
        wildcard_cap=WILDCARD_CAP,
    )


@payout_board_bp.route("/payout-board/wildcard", methods=["POST"])
def wildcard_submit():
    settings = _load_settings()
    if not settings.get("wildcard_window_open"):
        return redirect(url_for("payout_board.public_live"))

    form = request.form
    email = form.get("email", "").strip().lower()
    wc_seconds = _wildcard_seconds_remaining(settings)
    errors = {}

    if not email or "@" not in email:
        errors["email"] = "Valid email required."
        return render_template(
            "payout_board_wildcard.html",
            settings=settings, errors=errors, form_data=form,
            confirmed=False, alias="", wc_seconds=wc_seconds,
            wildcard_cap=WILDCARD_CAP,
        )

    try:
        subs = pb_db.read_all()
    except Exception:
        return render_template(
            "payout_board_wildcard.html",
            settings=settings,
            errors={"general": "Service unavailable."},
            form_data=form, confirmed=False, alias="",
            wc_seconds=wc_seconds, wildcard_cap=WILDCARD_CAP,
        )

    sub = next((s for s in subs if s["email"].lower() == email), None)

    if not sub:
        return render_template(
            "payout_board_wildcard.html",
            settings=settings,
            errors={"email": "No submission found for this email."},
            form_data=form, confirmed=False, alias="",
            wc_seconds=wc_seconds, wildcard_cap=WILDCARD_CAP,
        )

    if sub["wc_eligible"] != "Yes":
        return render_template(
            "payout_board_wildcard.html",
            settings=settings,
            errors={"email": "You are not eligible for the Wildcard. Only BPs who did not cash out qualify."},
            form_data=form, confirmed=False, alias="",
            wc_seconds=wc_seconds, wildcard_cap=WILDCARD_CAP,
        )

    wc_entry_count = sum(1 for s in subs if s["wc_entry"] == "Yes")
    if wc_entry_count >= WILDCARD_CAP:
        return render_template(
            "payout_board_wildcard.html",
            settings=settings,
            errors={"email": "Wildcard is full. All 4 spots have been claimed."},
            form_data=form, confirmed=False, alias="",
            wc_seconds=wc_seconds, wildcard_cap=WILDCARD_CAP,
        )

    if sub["wc_entry"] == "Yes":
        return render_template(
            "payout_board_wildcard.html",
            settings=settings,
            errors={"email": "You have already submitted a Wildcard entry."},
            form_data=form, confirmed=False, alias="",
            wc_seconds=wc_seconds, wildcard_cap=WILDCARD_CAP,
        )

    pb_db.set_cell(sub["_row"], "R", "Yes")

    # Send wildcard confirmation email (non-fatal)
    try:
        from email_sim import send_wildcard_confirmation
        first = sub["name"].split()[0] if sub.get("name") else "there"
        send_wildcard_confirmation(sub["email"], first, sub["alias"], sub.get("plus_code", ""))
    except Exception as _e:
        logger.warning("Wildcard email failed (non-fatal): %s", _e)

    return render_template(
        "payout_board_wildcard.html",
        settings=settings, errors={}, form_data={},
        confirmed=True, alias=sub["alias"],
        wc_seconds=wc_seconds, wildcard_cap=WILDCARD_CAP,
    )


@payout_board_bp.route("/payout-board/admin/prize-config", methods=["GET"])
def prize_config_page():
    """Return prize config data as JSON for admin panel."""
    config = pb_db.get_prize_config()
    claimed = pb_db.get_spots_claimed_by_tier()
    for row in config:
        row["spots_claimed"] = claimed.get(row["tier_name"], 0)
        row["spots_remaining"] = max(0, row["spot_cap"] - row["spots_claimed"])
    log = pb_db.get_prize_config_log(50)
    return jsonify({"config": config, "log": log})


@payout_board_bp.route("/payout-board/admin/prize-config/update", methods=["POST"])
def prize_config_update():
    """Update a prize config row.

    Prize amounts and view thresholds are FIXED for the campaign (single source of
    truth = TIER_CONFIG) and cannot be changed here — attempting to raises a clear
    error so an admin can't create a board-vs-payout mismatch. Spot caps, lock, and
    active state remain adjustable; cap changes flow into settings['tier_caps'] so the
    live game logic actually honors them.
    """
    data = request.get_json() or {}
    try:
        row_id   = int(data.get("id", 0))
        spot_cap = int(data.get("spot_cap", 1))
    except (ValueError, TypeError):
        return jsonify({"error": "id and spot_cap must be numbers."}), 400
    tier_name = str(data.get("tier_name", "")).strip()
    active    = int(bool(data.get("active", True)))
    locked    = int(bool(data.get("locked", False)))
    if not row_id or not tier_name:
        return jsonify({"error": "Missing required fields"}), 400

    tier_num = next((k for k, v in TIER_CONFIG.items() if v["name"] == tier_name), None)

    # Canonical prize/threshold — ignore any client-supplied change for competition tiers.
    if tier_num:
        canonical_thr   = TIER_CONFIG[tier_num]["threshold"]
        canonical_prize = TIER_CONFIG[tier_num]["prize"]
        req_thr   = _int(data.get("view_threshold", canonical_thr))
        req_prize = _int(data.get("prize_amount", canonical_prize))
        if req_thr != canonical_thr or req_prize != canonical_prize:
            return jsonify({
                "error": "Prize amounts and view thresholds are fixed for this campaign "
                         "and cannot be edited. You can adjust spot caps, lock, and active state.",
            }), 400
        view_thresh, prize_amount = canonical_thr, canonical_prize
        # Persist the adjustable cap where the game logic reads it.
        spot_cap = max(0, spot_cap)
        s = _load_settings()
        caps = s.get("tier_caps", {})
        caps[str(tier_num)] = spot_cap
        s["tier_caps"] = caps
        _save_settings(s)
    else:
        # Non-competition rows (e.g. Mini-Game Bonus) keep their stored values.
        view_thresh  = _int(data.get("view_threshold", 0))
        prize_amount = _int(data.get("prize_amount", 0))

    pb_db.update_prize_config_row(row_id, tier_name, view_thresh, prize_amount, spot_cap, active, locked)
    return jsonify({"ok": True})
