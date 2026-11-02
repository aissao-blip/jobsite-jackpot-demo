# 🎯 Jobsite Jackpot — Portfolio Demo

**▶ Live production site: [jobsitejackpot.com](https://jobsitejackpot.com)** — the real deployed
campaign. **This repository** is the standalone, run-it-yourself demo (fictional data, no secrets)
that showcases the same product locally at `http://localhost:5000`.

A standalone, fully interactive demonstration of **Jobsite Jackpot** — a real-money-style
*creator cash competition* I designed and built to run a brand's creator-partnership campaign.
Creators submit a social post, climb cash tiers as their views grow, and make a strategic
**Cash Out vs. Go Overtime** decision, while an admin runs verification, payouts, and
anti-fraud from a live operations dashboard.

This demo is **independent and self-contained**: fictional data, no company systems, no API
keys, and **no real emails or payments are ever sent**. It runs locally in your browser.

> **Branding note:** the live campaign ran for a specific brand sponsor. For this portfolio
> build, all sponsor-specific names and the third-party payout provider have been replaced
> with a fictional sponsor ("Toolforge") and generic "payout provider" language. The game,
> mechanics, and code are the work being showcased.

---

## What you can do in the demo

- **Watch the live board** — animated countdown, scrolling ticker, four cash tiers filling
  up, a leaderboard with "Challenger" standings, a live activity feed, and a confirmed-payouts
  board — all refreshing every 30 seconds.
- **Join as a creator** — submit a post + view count, get an instant Player Code and a public
  alias (your real identity stays private).
- **Make the decision** — enter your Player Code to reach your Decision Page: **Cash Out** your
  guaranteed tier prize, or **Go Overtime** to release your spot and play the *Perfect Stripes*
  mower mini-game for a +$100 bonus.
- **Run operations as admin** — verify submissions, approve/deny cash-outs, catch duplicate
  creators (Fraud Radar), repair a mis-tiered record, manage the Top Earner waitlist, lock the
  game, and export a payout CSV.

---

## Quick start

**Prerequisites:** Python **3.9 or newer** (the demo is tested on 3.9). No other services needed.

### macOS / Linux
```bash
cd Jobsite_Jackpot_Portfolio_Demo
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
```

### Windows (PowerShell)
```powershell
cd Jobsite_Jackpot_Portfolio_Demo
python -m venv venv
venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Then open **http://localhost:5000** in your browser.

The first launch automatically seeds **22 fictional creators** across every tier and game state.

---

## Where to go

| Screen | URL | Notes |
|---|---|---|
| **Live board** | http://localhost:5000/payout-board/live | The public game |
| **Player decision page** | enter a Player Code in the "Returning Player?" box on the board | Codes are shown in the admin Player Registry |
| **Admin dashboard** | http://localhost:5000/payout-board | Login: **`admin`** / **`jackpot-dev`** |
| **Official rules** | http://localhost:5000/payout-board/terms | |
| **Reset the demo** | http://localhost:5000/demo/reset | Restores fresh fictional data |

### Reset the demo data
- In the browser: visit **`/demo/reset`** (or the "Reset demo" link in the admin top bar).
- From the terminal: `python reset_demo.py`

Both wipe the board and re-seed the original fictional roster and a fresh, live countdown window.

---

## Configuration (all optional)

Copy `.env.example` to `.env` only if you want to override defaults. The demo runs with none of them.

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `5000` | Local server port |
| `ADMIN_PASSWORD` | `jackpot-dev` | Admin panel password (local demo default) |
| `SECRET_KEY` | demo value | Flask session secret |

There are deliberately **no** SendGrid / payment-provider / cloud credentials anywhere in this
project. Email notifications are **simulated** and recorded to `data/outbox.json`.

---

## What's in here

```
app.py                 Standalone Flask entry point
payout_board.py        The game engine: routes, tier logic, cash-out/overtime, admin API
pb_db.py               SQLite data layer
email_sim.py           Simulated email layer (records to data/outbox.json; sends nothing)
seed_demo.py           Fictional creator roster + reset
reset_demo.py          CLI reset
templates/             Public board, decision page, submit, terms, wildcard, admin
static/                CSS + JS (live refresh, mini-game, sounds)
requirements.txt       Flask
.env.example           Optional config (no secrets)
portfolio/             Case study, technical overview, walkthrough, feature audit, screenshots
```

`data/` (SQLite DB, settings, simulated outbox) is created at runtime and is safe to delete —
it rebuilds on next launch.

See **`portfolio/`** for the case study, technical overview, demo walkthrough script, feature
audit, and screenshots.
