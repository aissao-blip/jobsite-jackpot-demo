"""SQLite database layer for Jobsite Jackpot.

Tables:
  submissions       — one row per BP
  prize_config      — dynamic tier/prize settings (replaces hardcoded TIERS)
  prize_config_log  — audit log of admin changes to prize config
"""

import os
import sqlite3
import logging

logger = logging.getLogger(__name__)

_COL_LETTER: dict[str, str] = {
    "A": "name",            "B": "email",          "C": "alias",
    "D": "niche",           "E": "platform",        "F": "post_url",
    "G": "screenshot_url",  "H": "impressions",     "I": "co_impressions",
    "J": "co_screenshot",   "K": "tier_qualified",  "L": "cashed_out",
    "M": "cash_out_tier",   "N": "spot_claimed",    "O": "status",
    "P": "payout",          "Q": "wc_eligible",     "R": "wc_entry",
    "S": "wc_won",          "T": "sub_date",        "U": "co_date",
    "V": "notes",           "W": "mower_bonus",     "X": "plus_code",
}


def _db_path() -> str:
    from payout_board import _DATA_DIR
    return os.path.join(_DATA_DIR, "submissions.db")


def init_db() -> None:
    """Create all tables if they don't exist. Safe to call on every startup."""
    path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)

    # ── submissions ──────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS submissions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            name             TEXT    DEFAULT '',
            email            TEXT    UNIQUE,
            alias            TEXT    DEFAULT '',
            niche            TEXT    DEFAULT '',
            platform         TEXT    DEFAULT '',
            post_url         TEXT    DEFAULT '',
            screenshot_url   TEXT    DEFAULT '',
            impressions      INTEGER DEFAULT 0,
            co_impressions   INTEGER DEFAULT 0,
            co_screenshot    TEXT    DEFAULT '',
            tier_qualified   INTEGER DEFAULT 0,
            cashed_out       TEXT    DEFAULT 'No',
            cash_out_tier    TEXT    DEFAULT 'None',
            spot_claimed     TEXT    DEFAULT 'No',
            status           TEXT    DEFAULT 'Pending',
            payout           TEXT    DEFAULT 'Unpaid',
            wc_eligible      TEXT    DEFAULT 'No',
            wc_entry         TEXT    DEFAULT 'No',
            wc_won           TEXT    DEFAULT 'No',
            sub_date         TEXT    DEFAULT '',
            co_date          TEXT    DEFAULT '',
            notes            TEXT    DEFAULT '',
            mower_bonus      INTEGER DEFAULT 0,
            plus_code        TEXT    DEFAULT '',
            handle           TEXT    DEFAULT '',
            cash_state       TEXT    DEFAULT 'CONDITIONAL',
            submission_status TEXT   DEFAULT 'Pending',
            has_played_minigame INTEGER DEFAULT 0,
            unlocked_tier    TEXT    DEFAULT '',
            claimed_tier     TEXT    DEFAULT '',
            overtime_round      INTEGER DEFAULT 0,
            bonus_awarded_round INTEGER DEFAULT 0
        )
    """)

    # Add new columns to existing databases (migration — safe to run repeatedly)
    _add_column_if_missing(conn, "submissions", "handle",              "TEXT DEFAULT ''")
    _add_column_if_missing(conn, "submissions", "cash_state",          "TEXT DEFAULT 'CONDITIONAL'")
    _add_column_if_missing(conn, "submissions", "submission_status",   "TEXT DEFAULT 'Pending'")
    _add_column_if_missing(conn, "submissions", "has_played_minigame", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "submissions", "unlocked_tier",       "TEXT DEFAULT ''")
    _add_column_if_missing(conn, "submissions", "claimed_tier",        "TEXT DEFAULT ''")
    # Overtime-round tracking — closes the mini-game bonus farming / replay exploit.
    # overtime_round increments each time a player goes overtime; bonus_awarded_round
    # records the round a +$100 bonus was granted (or a loss consumed), so at most one
    # bonus can ever be awarded per overtime round.
    _add_column_if_missing(conn, "submissions", "overtime_round",      "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "submissions", "bonus_awarded_round", "INTEGER DEFAULT 0")

    # ── prize_config ─────────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prize_config (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            tier_name      TEXT    NOT NULL,
            view_threshold INTEGER DEFAULT 0,
            prize_amount   INTEGER DEFAULT 0,
            spot_cap       INTEGER DEFAULT 1,
            active         INTEGER DEFAULT 1,
            sort_order     INTEGER DEFAULT 0,
            locked         INTEGER DEFAULT 0
        )
    """)
    _add_column_if_missing(conn, "prize_config", "locked", "INTEGER DEFAULT 0")

    # Seed default prize config if empty
    count = conn.execute("SELECT COUNT(*) FROM prize_config").fetchone()[0]
    if count == 0:
        defaults = [
            ("Entry Level",    500,  150,  10, 1, 1),
            ("Day Rate",      2000,  400,   5, 1, 2),
            ("Double Shift",  4000,  600,   3, 1, 3),
            ("Top Earner",    7000, 1500,   2, 1, 4),
            ("Mini-Game Bonus",  0,  100, 999, 1, 5),
        ]
        conn.executemany(
            "INSERT INTO prize_config (tier_name, view_threshold, prize_amount, spot_cap, active, sort_order) VALUES (?,?,?,?,?,?)",
            defaults,
        )

    # Migration: rename legacy "Overtime" tier to "Double Shift" (safe to run repeatedly)
    conn.execute("UPDATE prize_config SET tier_name='Double Shift' WHERE tier_name='Overtime'")

    # Enforce canonical prize/threshold on every startup. The single source of truth is
    # payout_board.TIER_CONFIG; mirroring it here keeps the admin panel and CSV display in
    # lockstep with the payout math even if an earlier build allowed prizes to be edited.
    # (Spot caps are intentionally NOT forced — they stay admin-adjustable.)
    _canonical_tiers = [
        ("Entry Level",     500,  150),
        ("Day Rate",       2000,  400),
        ("Double Shift",   4000,  600),
        ("Top Earner",     7000, 1500),
        ("Mini-Game Bonus",   0,  100),
    ]
    for _name, _thr, _prize in _canonical_tiers:
        conn.execute(
            "UPDATE prize_config SET view_threshold=?, prize_amount=? WHERE tier_name=?",
            (_thr, _prize, _name),
        )

    # ── prize_config_log ─────────────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prize_config_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT    DEFAULT (datetime('now')),
            tier_name   TEXT    DEFAULT '',
            field       TEXT    DEFAULT '',
            old_value   TEXT    DEFAULT '',
            new_value   TEXT    DEFAULT '',
            admin_user  TEXT    DEFAULT 'admin'
        )
    """)

    conn.commit()
    conn.close()


def _add_column_if_missing(conn, table: str, column: str, definition: str) -> None:
    """ALTER TABLE ... ADD COLUMN if the column doesn't exist yet."""
    existing = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


# ── submissions CRUD ──────────────────────────────────────────────────────────

def read_all() -> list[dict]:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM submissions ORDER BY id").fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d["_row"] = d["id"]
        d["impressions"]     = int(d.get("impressions") or 0)
        d["co_impressions"]  = int(d.get("co_impressions") or 0)
        d["tier_qualified"]  = int(d.get("tier_qualified") or 0)
        d["mower_bonus"]     = int(d.get("mower_bonus") or 0)
        d["has_played_minigame"] = int(d.get("has_played_minigame") or 0)
        d["overtime_round"]      = int(d.get("overtime_round") or 0)
        d["bonus_awarded_round"] = int(d.get("bonus_awarded_round") or 0)
        result.append(d)
    return result


def append_row(row_dict: dict) -> None:
    conn = sqlite3.connect(_db_path())
    cols = list(row_dict.keys())
    vals = [row_dict[c] for c in cols]
    placeholders = ",".join("?" * len(cols))
    col_str = ",".join(cols)
    conn.execute(f"INSERT INTO submissions ({col_str}) VALUES ({placeholders})", vals)
    conn.commit()
    conn.close()


def set_field(row_id: int, field: str, value) -> None:
    conn = sqlite3.connect(_db_path())
    conn.execute(f"UPDATE submissions SET {field}=? WHERE id=?", (value, row_id))
    conn.commit()
    conn.close()


def set_cell(row_id: int, col_letter: str, value) -> None:
    field = _COL_LETTER.get(col_letter.upper())
    if field:
        set_field(row_id, field, value)
    else:
        logger.warning("pb_db.set_cell: unknown column letter '%s'", col_letter)


def delete_all() -> None:
    conn = sqlite3.connect(_db_path())
    conn.execute("DELETE FROM submissions")
    conn.commit()
    conn.close()


def award_mower_bonus_once(row_id: int, round_no: int, amount: int, pool_cap=None):
    """Atomically award the mini-game bonus for a given overtime round EXACTLY once,
    and only while the total bonus pool has room.

    Two guards live inside the single conditional UPDATE, so both are race-safe (SQLite
    serializes writers, so concurrent requests are evaluated one at a time against
    committed state):
      - bonus_awarded_round < round_no  → one award per overtime round
      - SUM(mower_bonus) + amount <= pool_cap  → never exceed the total pool
    Returns (awarded: bool, new_bonus_bank: int).
    """
    conn = sqlite3.connect(_db_path())
    try:
        if pool_cap is None:
            cur = conn.execute(
                "UPDATE submissions "
                "SET mower_bonus = mower_bonus + ?, bonus_awarded_round = ?, has_played_minigame = 1 "
                "WHERE id = ? AND bonus_awarded_round < ? AND cashed_out != 'Yes' AND status = 'Overtime'",
                (amount, round_no, row_id, round_no),
            )
        else:
            cur = conn.execute(
                "UPDATE submissions "
                "SET mower_bonus = mower_bonus + ?, bonus_awarded_round = ?, has_played_minigame = 1 "
                "WHERE id = ? AND bonus_awarded_round < ? AND cashed_out != 'Yes' AND status = 'Overtime' "
                "AND (SELECT COALESCE(SUM(mower_bonus), 0) FROM submissions) + ? <= ?",
                (amount, round_no, row_id, round_no, amount, pool_cap),
            )
        awarded = cur.rowcount > 0
        conn.commit()
        row = conn.execute("SELECT mower_bonus FROM submissions WHERE id=?", (row_id,)).fetchone()
    finally:
        conn.close()
    return awarded, int(row[0]) if row else 0


def total_mower_bonus() -> int:
    """Total mini-game bonus awarded across all players (for pool-cap messaging)."""
    conn = sqlite3.connect(_db_path())
    row = conn.execute("SELECT COALESCE(SUM(mower_bonus), 0) FROM submissions").fetchone()
    conn.close()
    return int(row[0]) if row else 0


# ── prize_config CRUD ─────────────────────────────────────────────────────────

def get_prize_config() -> list[dict]:
    """Return all prize config rows ordered by sort_order."""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM prize_config ORDER BY sort_order").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_tiers() -> list[dict]:
    """Return active competition tiers (excludes Mini-Game Bonus row)."""
    config = get_prize_config()
    return [r for r in config if r["active"] and r["tier_name"] != "Mini-Game Bonus"]


def get_minigame_bonus() -> int:
    """Return the current mini-game bonus prize amount."""
    conn = sqlite3.connect(_db_path())
    row = conn.execute(
        "SELECT prize_amount FROM prize_config WHERE tier_name='Mini-Game Bonus' LIMIT 1"
    ).fetchone()
    conn.close()
    return int(row[0]) if row else 100


def update_prize_config_row(row_id: int, tier_name: str, view_threshold: int,
                             prize_amount: int, spot_cap: int, active: int,
                             locked: int = 0,
                             admin_user: str = "admin") -> None:
    """Update a prize config row and write a log entry for each changed field."""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    old_row = conn.execute("SELECT * FROM prize_config WHERE id=?", (row_id,)).fetchone()
    old = dict(old_row) if old_row else {}

    new_vals = {
        "tier_name": tier_name,
        "view_threshold": view_threshold,
        "prize_amount": prize_amount,
        "spot_cap": spot_cap,
        "active": active,
        "locked": locked,
    }

    for field, new_val in new_vals.items():
        old_val = old.get(field)
        if str(old_val) != str(new_val):
            conn.execute(
                "INSERT INTO prize_config_log (tier_name, field, old_value, new_value, admin_user) VALUES (?,?,?,?,?)",
                (old.get("tier_name", ""), field, str(old_val), str(new_val), admin_user),
            )

    conn.execute(
        "UPDATE prize_config SET tier_name=?, view_threshold=?, prize_amount=?, spot_cap=?, active=?, locked=? WHERE id=?",
        (tier_name, view_threshold, prize_amount, spot_cap, active, locked, row_id),
    )
    conn.commit()
    conn.close()


def get_prize_config_log(limit: int = 50) -> list[dict]:
    """Return recent prize config changes."""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM prize_config_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_spots_claimed_by_tier() -> dict:
    """Count submissions with spot_claimed='Yes' grouped by cash_out_tier."""
    conn = sqlite3.connect(_db_path())
    rows = conn.execute(
        "SELECT cash_out_tier, COUNT(*) FROM submissions WHERE spot_claimed='Yes' GROUP BY cash_out_tier"
    ).fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}
