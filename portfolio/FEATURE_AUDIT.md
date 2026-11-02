# Feature Audit — Jobsite Jackpot

This documents (1) what the **original** Jobsite Jackpot actually does, (2) what was **recreated
or changed** for this independent portfolio build, and (3) exactly what was **verified in this
demo** versus not. It is written to be honest about test coverage — unverified items are labeled.

---

## 1. Original project — feature inventory

Status key: ✅ fully functional · ◑ partial · ○ placeholder

| Feature | Status | Notes |
|---|---|---|
| Public live board (hero, countdown, tier cards, ticker) | ✅ | Server-rendered + 30s JS refresh kept byte-consistent |
| Leaderboard with "Challenger" standings | ✅ | Derived from views + tier fullness |
| Live activity feed | ✅ | One item per active creator; shows the tier a creator can actually claim |
| Confirmed-payouts board | ✅ | Alias-only (no PII) |
| Creator submission / join flow | ✅ | Creates entry, unique Player Code, anonymous alias, confirmation email |
| Returning-player status check | ✅ | Private profile by Player Code |
| Cash-out flow | ✅ | With **step-down** (take the best open lower tier when yours is full) |
| Views-priority allocation | ✅ | First-come on open spots; waitlist ranking when a tier is full |
| Go Overtime + "Perfect Stripes" mini-game | ✅ | +$100 bonus, $1,000 pool cap, one-bonus-per-round anti-farming |
| Admin: verify / approve cash-out / DQ / mark paid | ✅ | HTTP Basic Auth, fail-closed in production |
| Admin: "Back to Game", keep-playing nudge | ✅ | Reopen an unpaid cash-out; email a nudge |
| Admin: Fraud Radar (duplicate creator detection) | ✅ | Flags repeat names/handles across entries |
| Admin: Top Earner waitlist | ✅ | Ranked by views, then earliest submission |
| Admin: prize config view, CSV export | ✅ | Payout export for processing |
| Admin: board controls (toggle board, close/reopen tier, set close date, wildcard window) | ✅ | Settings-backed |
| Admin: game lock + "game has ended" banner | ✅ | Blocks new joins; existing players unaffected |
| Admin: "fix tier" record repair | ✅ | Corrects a mis-tiered record |
| Wildcard window / draw | ✅ | Bonus entry window near campaign close |
| Email notifications (9 types) | ✅ | Via SendGrid in production (requires a valid key) |

**Audit method:** I did not rely on the README. Status reflects the actual route handlers and
templates in the source, which I worked in directly.

---

## 2. What was recreated / changed for this independent demo

| Area | Original | This demo |
|---|---|---|
| Email | SendGrid transactional send | **Simulated** layer (`email_sim.py`) — records to `data/outbox.json`, sends nothing |
| Data | Real creator records in a hosted DB | **22 fictional creators** (`seed_demo.py`) + reset |
| Sponsor branding | Real brand + payout provider | Genericized to fictional **"Toolforge"** + "the payout provider" |
| Hosting coupling | Part of a larger dashboard app, deployed to a cloud host | **Standalone Flask app** (`app.py`), no external dependencies |
| Live overrides | A few per-player display/payout corrections keyed to real aliases/codes | Removed (empty maps) |
| Countdown | Fixed campaign dates | **Rolling live window** (starts ~a week ago, closes ~3 weeks out) so the demo never looks stale |
| Game state | Could be locked (campaign ended) | **Open/playable** by default for exploration |
| Credentials | Env-based secrets on the host | **None in the archive**; demo admin password documented as `jackpot-dev` |

The game engine itself (`payout_board.py`, `pb_db.py`, templates, static JS/CSS) is the original
author's own work, ported with the changes above — not reconstructed from scratch.

---

## 3. Verified in THIS demo (tested)

Verified via automated checks against the standalone app and by viewing it in a browser:

- ✅ **All public pages render HTTP 200**: live board, terms, submit, cash-out decision page
  (wildcard correctly redirects when its window is closed).
- ✅ **Admin pages render 200 with auth; 401 without** — the Basic Auth guard works standalone.
- ✅ **Board API** returns tiers (with claimed/cap/full), leaderboard (22), confirmed cash-outs
  (12), prize-pool math, and **challenger** standings (two challengers present on the seeded board).
- ✅ **New join** creates a record, assigns an anonymous alias + Player Code, and records a
  **simulated** `submission_confirmation` email (nothing sent).
- ✅ **Cash-out** processes end-to-end (record flips to cashed-out at the correct tier) and records
  a simulated `cashout_received` email.
- ✅ **Leaderboard + activity feed show the *claimable* tier** (step-down aware), not an unwinnable
  full tier.
- ✅ **Reset** (`/demo/reset` and `reset_demo.py`) restores the fictional roster and a live window.
- ✅ **Security scan is clean**: no API keys/tokens, no real emails, no sponsor-proprietary strings,
  no real player data anywhere in the shipped tree.
- ✅ Screenshots in `screenshots/` were captured from this running demo.

## 4. NOT exhaustively tested (honest gaps)

- ◑ **Individual admin mutation buttons** (verify / approve / DQ / mark-paid / fraud-radar actions /
  CSV download) were **not each clicked through in the demo browser**. Their endpoints are ported
  unchanged from the working production code and the admin page renders, but treat per-button
  behavior as *ported, not re-verified here*.
- ◑ **Perfect Stripes mini-game front-end gameplay** (the canvas arcade) was not manually played in
  the demo; the seeded "overtime + bonus" state renders and the award endpoint is ported, but a
  full manual playthrough was not performed in this environment.
- These are called out so nothing is overstated. Everything in section 3 was actually run.
