# Case Study — Jobsite Jackpot

*A gamified, real-money creator competition that turns a brand's creator-partnership campaign
into a live, self-serve game — with an operations dashboard that keeps payouts fair, fast, and
fraud-resistant.*

---

## The business problem

Running a cash-incentive campaign across dozens of creators is operationally messy. The campaign
owner has to:

- field a stream of "how am I doing / what do I get paid?" messages,
- track each creator's view counts, tiers, and eligibility by hand,
- decide who gets which limited, capped prize when more creators qualify than there are spots,
- verify screenshots and catch duplicate or inflated entries before paying real money, and
- keep every creator motivated to keep posting instead of dropping off.

Done in spreadsheets and DMs, this is slow, error-prone, and easy to dispute — and every mistake
is a real payout or an upset creator.

## Why I designed Jobsite Jackpot

Instead of a back-office tracker, I built the campaign **as a game the creators play themselves**.
A public live board turns the campaign into something creators *want* to check: a countdown, cash
tiers filling up, a leaderboard, and a visible path to more money. The operational work (tiering,
allocation, verification, payouts, anti-fraud) is pushed into an automated engine and a single
admin dashboard, so the campaign scales without scaling the manual effort.

## Intended users

- **Creators / brand partners** — submit a post, watch their standing, and make a strategic
  decision (lock in a guaranteed prize now, or gamble for more).
- **The campaign operator (admin)** — one dashboard to verify, allocate, pay out, and police fraud.

## The solution and its features

**For creators (the public game):**
- A live board with a countdown, a scrolling ticker, four cash tiers (Entry Level → Top Earner),
  a leaderboard, a live activity feed, and a confirmed-payouts wall.
- Self-serve **join**: submit a post + view count, get an instant Player Code and an anonymous
  public alias (real identity stays private).
- A **Decision Page**: once your views qualify you for a tier, choose **Cash Out** (lock the
  guaranteed prize) or **Go Overtime** (release your spot, keep growing, and play the *Perfect
  Stripes* mini-game for a +$100 bonus).
- A **"Challenger"** standing that shows when you qualify for a tier that's currently full and
  where you sit in line for it.

**For the operator (the admin dashboard):**
- An action queue (cash-out requests + new submissions) with verify / approve / disqualify.
- **Fraud Radar** that flags duplicate creators across entries.
- A **Top Earner waitlist**, **prize/spot controls**, a **payout CSV export**, a **game lock**
  ("the game has ended") and record-repair tools.

## The automation architecture

Jobsite Jackpot's intelligence is a **deterministic automation engine**, not an LLM. The parts
that replace manual judgement:

1. **Tiering + allocation engine.** View counts map to cash tiers; each tier has a hard spot cap.
   When more creators qualify than a tier can hold, the engine applies **first-come on open spots**
   and a **views-priority waitlist** when a tier is full, and **steps a creator down** to the best
   open lower tier so no one is stranded. This is the logic that would otherwise be a human making
   contested, disputable calls about limited prizes.
2. **A per-creator state machine.** Pending → Verified → (Cash Out | Overtime) → Paid, with guards
   that prevent overselling a tier, double-paying, or farming the mini-game bonus (one bonus per
   overtime round, capped at a fixed pool).
3. **Anti-fraud checks.** One entry per email, duplicate-creator detection, and screenshot
   verification before any payout is approved.
4. **Event-driven notifications.** Nine notification types (join, verification, approval, cash-out
   received, overtime, mini-game win, keep-playing nudge, consolation offer, wildcard) fire off
   state changes. *In this demo they are simulated and nothing is sent.*
5. **A live-consistency layer.** The board server-renders and also refreshes every 30 seconds from
   a JSON API; both paths run the same logic so the public board never "flips" between the two.

## My role

I designed and built the whole system end-to-end: the game concept and economy (tiers, caps,
bonus pool), the allocation and anti-fraud logic, the Flask back end and SQLite data model, the
public game UI and the operator dashboard, the notification flows, and the live-sync layer. For
this portfolio build I also produced the standalone, credential-free, fictional-data version.

## Key design decisions

- **Make the campaign a self-serve game.** Shifting status-checking and decisions onto creators
  removes the biggest manual-support burden and increases engagement.
- **Guaranteed floor + optional gamble.** "Cash Out vs. Go Overtime" gives creators a safe prize
  *and* a reason to keep posting — without the operator chasing anyone.
- **Fairness as code, not opinion.** Capped tiers + step-down + views-priority make contested
  prize decisions deterministic and explainable, which heads off disputes over real money.
- **Verification before money.** Screenshots and fraud checks gate payouts; cash-outs are final
  and one-per-creator by construction.
- **Server render ≡ live refresh.** One source of truth for the board so the public view is always
  internally consistent.

## Challenges and lessons learned

- **Allocating scarce prizes fairly is the hard part.** The subtle bugs were all in the edges:
  reserving an open spot for someone who never claimed it, or an admin action silently re-tiering a
  creator who had stepped down. The lesson: the allocation tier a creator can *actually claim right
  now* must be the single value every surface reads from.
- **Public consistency matters.** A board that shows one thing on load and another after a refresh
  destroys trust in a money game; keeping both paths on identical logic was a core constraint.
- **Fail closed around money.** Admin access fails closed; tiers can't be oversold; the bonus pool
  is hard-capped. For real payouts, the safe default has to be "do nothing."

## Potential business value

*(Framed as intent and mechanism — not measured results. No performance, time-savings, revenue, or
adoption figures are claimed.)*

- **Less manual campaign ops** by making status-checking and decisions self-serve.
- **Fewer payout disputes** because tier allocation is deterministic and visible.
- **Lower fraud/overpayment risk** via one-entry-per-creator, duplicate detection, screenshot
  verification, and hard tier/pool caps.
- **Higher creator engagement** through a live, competitive, game-like experience with a built-in
  reason to keep posting.

Any quantified impact would need to be measured against a specific campaign; this case study
deliberately does not invent those numbers.
