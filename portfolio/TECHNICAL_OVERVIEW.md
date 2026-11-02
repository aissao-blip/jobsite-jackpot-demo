# Technical Overview — Jobsite Jackpot

## Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.9+ | Ubiquitous, easy to run locally |
| Web framework | Flask (Blueprint) | Lightweight; the whole game is one self-contained blueprint |
| Data | SQLite | Zero-config, file-based; perfect for a portable demo |
| Front end | Server-rendered Jinja2 + vanilla JS + Tailwind (CDN) | No build step; a single `python app.py` runs everything |
| Notifications | Pluggable email client | Production uses a transactional provider; **this demo uses a simulated layer that sends nothing** |

No database server, no message queue, no cloud dependency, no API keys.

## Architecture at a glance

```
            ┌──────────────────────── Public game ────────────────────────┐
Browser ──► /payout-board/live  (server-rendered board)                     │
        ◄── /payout-board/api/board-data  (JSON, polled every 30s) ─────────┘
        ──► /payout-board/submit        join
        ──► /payout-board/check-status  returning player
        ──► /payout-board/cashout       Cash Out / decision page
        ──► /payout-board/go-overtime + /payout-board/mower-bonus  mini-game

            ┌──────────────────────── Operator ───────────────────────────┐
        ──► /payout-board               admin dashboard (Basic Auth)        │
        ──► /payout-board/api/*         verify, approve, DQ, mark-paid,      │
            toggle board, lock game, export CSV, fix tier, ...              ┘

payout_board.py  ── game engine (routes + rules)
   │  uses
   ├─ pb_db.py      ── SQLite: submissions, prize_config, logs
   └─ email_sim.py  ── simulated notifications (demo)   [prod: real email client]
```

Everything is one Flask blueprint (`payout_board_bp`) registered by `app.py`. `app.py` adds only a
home redirect and a demo-reset convenience route.

## Data model

One primary table, `submissions` — a row per creator with: identity (name, email, anonymous
`alias`, `plus_code`), the post (`platform`, `post_url`, `impressions`, screenshots), and game
state (`tier_qualified`, `cashed_out`, `cash_out_tier`, `spot_claimed`, `status`, `payout`,
`mower_bonus`, `overtime_round`, `has_played_minigame`, wildcard fields). A `prize_config` table
holds tiers/thresholds/caps; a log table audits config changes.

Tiers (the campaign economy):

| Tier | Threshold | Prize | Spots |
|---|---|---|---|
| Entry Level | 500+ views | $150 | 10 |
| Day Rate | 2,000+ | $400 | 5 |
| Double Shift | 4,000+ | $600 | 3 |
| Top Earner | 7,000+ | $1,500 | 2 |
| Perfect Stripes bonus | — | +$100 | pooled, $1,000 cap |

## How the allocation engine decides

The core function (`_cashout_options`) answers, for a given creator *right now*:

1. **What tier do your views qualify you for?** (`_calc_tier`)
2. **Is that tier full?** (`_claimed_spots` vs the tier cap)
3. **If open** → you can claim it (first-come).
4. **If full** → you become a **Challenger** with a waitlist rank (highest views, then earliest
   submission), and the engine **steps you down** to the best open *lower* tier so you can still
   cash out something rather than being stranded.

Every public surface (leaderboard label, activity feed, decision page) reads the tier you can
*actually claim now* from this one function — which is what keeps the board honest when tiers fill.

Guards around money: a tier can't be oversold (cap checked excluding the acting player), a creator
can cash out only once, and the mini-game bonus is awarded at most once per overtime round and
never beyond the pool cap.

## Request consistency (no "flip")

The board is rendered two ways — the initial server-rendered HTML and a 30-second JSON refresh —
and both call the *same* builder functions (`_build_leaderboard`, `_build_activity_feed`, tier
stats). This guarantees the live view and the refresh always agree.

## Security & safety posture

- **Admin Basic Auth**, fail-closed in production (missing password ⇒ locked out, not open).
- **No secrets in the project** — notifications are simulated; there are no provider keys.
- **PII stays private** — the public board shows only anonymous aliases; names/emails/codes are
  admin-only.
- **Fail-closed around money** — oversell/double-pay/bonus-farming are all structurally prevented.

## Running / resetting

`python app.py` seeds fictional data on first run and serves on port 5000. `python reset_demo.py`
(or `/demo/reset`) wipes and re-seeds. The `data/` directory is disposable and rebuilds on launch.
