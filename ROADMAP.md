# 🗺️ Master Scaling Roadmap: Algorithmic +EV Betting Pipeline

A structured, 5-phase execution plan for taking the automated PrizePicks +EV betting model from paper simulation to consistent real-money cash flow (~$100 to $200+/week) while maintaining account longevity and mitigating platform risk.

---

## 📅 Timeline & Progression Architecture

```
[ Phase 1: 30-Day Paper Validation ]
       │  (Zero capital risk, mathematical verification)
       ▼
[ Phase 2: 2-Week Real-Money Pilot ($5/slip) ]
       │  (Execution latency, platform friction, bankroll routine)
       ▼
[ Phase 3: Core Target Scale ($25/slip) ]
       │  (Target: ~$100 to $125/week net cash flow)
       ▼
[ Phase 4: PrizePicks "Ghost Mode" Ceiling ($35–$40/slip) ]
       │  (Max safe single-platform ceiling: ~$150–$200/week)
       ▼
[ Phase 5: Multi-Book Horizontal Expansion ]
       │  (Underdog Fantasy, Sleeper, Betr: $1,500–$3,000+/month)
```

---

## Phase 1: Zero-Risk Paper Validation
* **Duration**: **30 Total Days** (November 1, 2026 – December 1, 2026)
* **Capital Risk**: **$0.00** (Simulated $10 paper slips tracked in `paper_bets.json` on Render cloud)
* **Slip Structure**: 6-Pick Flex only (Standard 1.0x lines, Full-Game duration, $\ge 2$ games)
* **Scanning Engine**: Dynamic 3-stage decaying threshold:
  * **Phase 1** (9:00 AM – 1:00 PM PT): `58.5%` hurdle (Morning pricing blunders)
  * **Phase 2** (1:30 PM – 3:30 PM PT): `57.8%` hurdle (Afternoon injury/lineup steam)
  * **Phase 3** (3:30 PM – 4:15 PM PT): `57.0%` hurdle (Pre-lock peak market consensus)
* **Graduation Gate (Criteria to advance to Phase 2)**:
  1. Minimum **25 completed slips** settled across the trial.
  2. Overall individual leg win rate $\ge \mathbf{55.0\%}$ (break-even is 54.2%).
  3. Cumulative net paper ROI is positive ($\ge \mathbf{+15.0\%}$).
  4. Zero cloud downtime, data-drift, or settlement drops over the evaluation window.
  5. **Model Probability Calibration**: Verify that empirical leg hit rates match the algorithm's predicted win probabilities (target calibration delta within $\pm 3.5\%$, Brier Score $< 0.2400$) to mathematically prove pricing accuracy before wagering real money.

---

## Phase 2: Live Real-Money Pilot
* **Duration**: **14 Days / 2 Weeks** (December 2, 2026 – December 16, 2026)
* **Initial Bankroll Deposit**: **$100 – $150** (20–30 units of safety cushion on PrizePicks)
* **Stake per Slip**: **$5.00** (1 slip/day; ~14 slips total)
* **Primary Objectives**:
  * Measure alert-to-placement latency (target: sub-2 minutes from Discord push to mobile lock).
  * Identify line movement or bump frequency between notification and placement.
  * Experience real-money variance and emotional discipline under low-stakes conditions.
  * Verify payment processing (deposits and test withdrawals).
* **Graduation Gate (Criteria to advance to Phase 3)**:
  1. Successfully entered $\ge 12$ slips manually without missing moved lines.
  2. Smooth mobile execution workflow established.
  3. No emotional deviations (never chasing or altering algorithmic picks).

---

## Phase 3: The Target Scale ($100/Week Cash Flow Benchmark)
* **Duration**: **30 Days / 1 Month** (December 17, 2026 – January 17, 2027)
* **Active Bankroll**: **$750 – $1,000** (30–40 unit bankroll reserve)
* **Stake per Slip**: **$25.00**
* **Volume**: 1 slip/day (optionally 2 slips/day on high-liquidity weekend slates)
* **Financial Projection**:
  * Weekly Staked: ~$175 – $350
  * **Expected Net Profit**: **~$100 to $125 / week** (~$400 to $500 / month)
* **Account Safety Protocol**:
  * Maximum 6/6 sweep payout is **$625** (remains below PrizePicks' manual audit threshold).
  * Withdraw profits on a fixed schedule (e.g. bi-weekly or monthly), never immediately following a big hit.
* **Graduation Gate (Criteria to advance to Phase 4)**:
  1. 30-day cumulative bankroll is net positive.
  2. Realized ROI aligns within $\pm 5\%$ of the theoretical model expectations.

---

## Phase 4: PrizePicks "Ghost Mode" Ceiling
* **Duration**: **Ongoing Core Operations** (Starting ~January 18, 2027)
* **Active Bankroll**: **$1,200 – $1,500**
* **Stake per Slip**: **$35.00 – $40.00**
* **Financial Projection**:
  * Weekly Staked: ~$250 – $500
  * **Expected Net Profit**: **~$150 to $200 / week** (~$600 to $850 / month)
* **Account Longevity & Anti-Limiting Rules**:
  * **The $50 Line**: Keep all single-entry stakes under $50 to prevent automated sharp-profiling algorithms from tagging the account.
  * **Promotional Camouflage**: Play the occasional $5 "Taco Tuesday" promo or recreational booster to mimic casual bettor telemetry.
  * **Manual Telemetry**: Exclusively place slips via the official mobile app UI (zero third-party browser automation).

---

## Phase 5: Multi-Book Horizontal Expansion
* **Objective**: Scale total income to **$1,500 – $3,000+ / month** without increasing bet sizes or risking account limits on any single platform.
* **Target Platforms**:
  1. **Underdog Fantasy**: 5-pick and 6-pick structures with favorable standard line odds.
  2. **Sleeper**: Dynamic multiplier DFS slips.
  3. **Betr Picks**: Multi-leg prop entries.
* **Implementation Plan**:
  * Extend `auto_bot.py` scraper to ingest Underdog Fantasy and Sleeper API feeds alongside PrizePicks.
  * Compare all platforms against FanDuel / sharp book market consensus.
  * Emit distinct Discord notifications (e.g., `#prizepicks-alerts` and `#underdog-alerts`).
  * Split capital across books ($25–$35 per platform), compounding daily profit while remaining in the "invisible" tier on every book.

---

## 📊 Core Principles & Mathematical Invariants

1. **Strict 6-Pick Flex Dominance**:
   * Break-even per leg: **`54.21%`** (-118 juice equivalent).
   * Flex cushion: 5/6 returns 2.0x, 4/6 returns 0.4x.
   * Avoid 3-Pick Power (58.5% hurdle) and 3-Pick Flex (60.8% hurdle).
2. **Dynamic Decaying Threshold**:
   * High hurdle in the morning (`58.5%`) prevents premature locking of early volatile lines.
   * Lowest hurdle (`57.0%`) only activates right before tip-off when market liquidity and consensus are at their peak.
3. **Fixed Unit Sizing**:
   * Never increase unit size during cold streaks to "chase" losses.
   * Expected ROI (~25% to 30%) realizes over hundreds of independent legs through the law of large numbers.
