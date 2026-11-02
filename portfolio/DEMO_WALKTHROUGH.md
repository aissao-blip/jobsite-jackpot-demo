# Demo Walkthrough — 60–90 second portfolio video script

A tight script for a screen-recorded walkthrough. Timings are a guide; narration is in quotes.
Run `python app.py` first and open **http://localhost:5000**.

---

### 0:00–0:10 — Hook (Live board hero)
**Show:** the live board loading — countdown ticking, scrolling ticker, the four cash tiers, the
"$8,300 total prize pool."
> "This is Jobsite Jackpot — a creator cash competition I built. A brand's partnership campaign,
> turned into a live game creators actually want to check."

### 0:10–0:25 — The game, at a glance
**Show:** scroll to the leaderboard and live activity feed. Point at the **Challenger** badges
("Next for Top Earner $1,500") and the confirmed-payouts.
> "Creators climb cash tiers as their views grow. The leaderboard shows who's cashed out, who's
> active, and who's next in line for a full tier — the 'Challengers.'"

### 0:25–0:40 — Join as a creator
**Show:** the "Submit Your Post" form — fill name, a post URL, a view count, pick a platform;
submit. The confirmation screen shows the instant **Player Code** and anonymous **alias**.
> "Joining is self-serve. Drop your post and views, and you instantly get a private Player Code
> and an anonymous board alias — your real identity never shows publicly."

### 0:40–1:00 — The strategic decision
**Show:** enter a Player Code in "Returning Player?" → the **Decision Page**. Highlight
**Cash Out $X** vs **Go Overtime** (release your spot, play Perfect Stripes for a +$100 bonus).
> "Once your views qualify you, you make the call: lock in a guaranteed prize now — or go
> Overtime, release your spot, and play the mini-game for a bonus. A safe floor, with a reason to
> keep posting."

### 1:00–1:20 — The operator dashboard
**Show:** log into the admin ( `admin` / `jackpot-dev` ). Pan the action queue (verify / approve /
DQ), the stat cards, **Fraud Radar**, the **Top Earner waitlist**, and the CSV export.
> "Behind it is one operations dashboard: verify screenshots, approve payouts, catch duplicate
> creators, manage the waitlist, and export payouts — the whole campaign from one screen."

### 1:20–1:30 — Close (the engineering point)
**Show:** back to the live board; let the 30-second refresh update a tier.
> "Fair, capped prize allocation, anti-fraud, and a live board that stays consistent on every
> refresh — all deterministic, all auditable. That's Jobsite Jackpot."

---

**Recording tips**
- Hit **/demo/reset** right before recording for a clean, lively board.
- The countdown is always live (rolling window), so the hero always looks active.
- Everything is simulated — safe to show the join and cash-out flows end to end.
