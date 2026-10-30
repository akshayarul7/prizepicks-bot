"""
Sports Betting +EV Tool - Step 3: OddsJam Proportional De-Vigging Calculator
----------------------------------------------------------------------------
Implements the exact OddsJam No-Vig algorithm (Proportional Method / Multiplicative)
to strip sportsbook vigorish, calculates true win probabilities, filters strictly
for BET-ABLE lines on PrizePicks (validating allowed_wager_types and Demon/Goblin
restrictions), and ranks +EV plays exceeding PrizePicks' 54.95% break-even hurdle.
"""

import json
import logging
import os
import sys
from typing import Optional, Tuple
import pandas as pd

# Ensure local lib directory is reachable
current_dir = os.path.dirname(os.path.abspath(__file__))
lib_path = os.path.join(current_dir, "lib")
if os.path.exists(lib_path) and lib_path not in sys.path:
    sys.path.insert(0, lib_path)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("DevigCalculator")

# Break-even hurdle rate for PrizePicks 3-Man Power Play (5x payout)
PRIZEPICKS_3MAN_BREAKEVEN = 54.95


# ===========================================================================
# PART A: THE ODDSJAM ALGORITHM (PROPORTIONAL METHOD)
# ===========================================================================

def american_to_implied(odds: float) -> float:
    """
    Converts American odds into raw implied probability (including sportsbook juice).

    Formula:
    - Positive odds (+150): 100 / (odds + 100) -> 100 / 250 = 0.40 (40.0%)
    - Negative odds (-150): abs(odds) / (abs(odds) + 100) -> 150 / 250 = 0.60 (60.0%)
    """
    try:
        numeric_odds = float(odds)
    except (ValueError, TypeError):
        return 0.50

    if numeric_odds > 0:
        return 100.0 / (numeric_odds + 100.0)
    elif numeric_odds < 0:
        return abs(numeric_odds) / (abs(numeric_odds) + 100.0)
    else:
        return 0.50


def oddsjam_devig(over_odds: float, under_odds: float) -> Tuple[float, float]:
    """
    Calculates the 'fair' (no-vig) win probability for both Over and Under
    using the exact OddsJam Proportional / Multiplicative De-vigging Algorithm.

    Steps:
    1. Calculate raw implied probabilities:
       - implied_over = american_to_implied(over_odds)
       - implied_under = american_to_implied(under_odds)
    2. Sum implied probabilities to measure total market overround (vig):
       - total_implied = implied_over + implied_under
    3. Proportionally strip the vig:
       - true_over = implied_over / total_implied
       - true_under = implied_under / total_implied
    """
    implied_over = american_to_implied(over_odds)
    implied_under = american_to_implied(under_odds)

    total_implied = implied_over + implied_under

    if total_implied <= 0:
        return 50.0, 50.0

    # Remove the vig proportionally and convert to percentages
    true_over = (implied_over / total_implied) * 100.0
    true_under = (implied_under / total_implied) * 100.0

    return round(true_over, 2), round(true_under, 2)


# ===========================================================================
# PART B: DATAFRAME APPLICATION & BET-ABILITY VALIDATION
# ===========================================================================

def check_prizepicks_bet_ability(row: pd.Series) -> bool:
    """
    Validates whether the recommended play (Over or Under) can ACTUALLY
    be selected on the PrizePicks platform:

    Rules:
    1. Demon props: PrizePicks ONLY offers 'MORE' (Over). 'LESS' (Under) is prohibited.
    2. Goblin props: PrizePicks ONLY offers 'MORE' (Over). 'LESS' (Under) is prohibited.
    3. Check PrizePicks API 'allowed_wager_types':
       - If allowed_wager_types == 'over', 'Under' is NOT bet-able.
       - If allowed_wager_types == 'under', 'Over' is NOT bet-able.
       - If allowed_wager_types == 'under_or_over', both sides are bet-able.
    """
    rec_play = str(row.get("Recommended Play", "")).strip().capitalize()
    allowed_wagers = str(row.get("Allowed Wagers", "under_or_over")).strip().lower()
    tier = str(row.get("PrizePicks Tier", "standard")).strip().lower()

    # Demons and Goblins are strictly "More" (Over) only on PrizePicks
    if tier in ["demon", "goblin"] and rec_play == "Under":
        return False

    # Check operator-level allowed wager constraints
    if rec_play == "Under" and allowed_wagers == "over":
        return False
    if rec_play == "Over" and allowed_wagers == "under":
        return False

    return True


def apply_devigging(df: pd.DataFrame) -> pd.DataFrame:
    """
    Applies the OddsJam de-vigging algorithm across a DataFrame of matched props.
    Computes 'True Over %', 'True Under %', 'Recommended Play', 'Edge %',
    and verifies 'Is Bet-able'.
    """
    if df.empty:
        return df

    df_result = df.copy()

    # Standardize column naming if originating from matcher
    if "Player Name (PrizePicks)" in df_result.columns and "Player Name" not in df_result.columns:
        df_result["Player Name"] = df_result["Player Name (PrizePicks)"]
    if "Game / Matchup" in df_result.columns and "Game" not in df_result.columns:
        df_result["Game"] = df_result["Game / Matchup"]

    devig_results = [
        oddsjam_devig(row["FanDuel Over"], row["FanDuel Under"])
        for _, row in df_result.iterrows()
    ]

    df_result["True Over %"] = [res[0] for res in devig_results]
    df_result["True Under %"] = [res[1] for res in devig_results]

    # Side with higher true probability
    df_result["Recommended Play"] = df_result.apply(
        lambda r: "Over" if r["True Over %"] > r["True Under %"] else "Under",
        axis=1,
    )
    # Highest win probability
    df_result["Edge %"] = df_result[["True Over %", "True Under %"]].max(axis=1)

    # Real-World Bet-ability Validation
    df_result["Is Bet-able"] = df_result.apply(check_prizepicks_bet_ability, axis=1)

    return df_result


def filter_and_rank_ev(
    df: pd.DataFrame,
    min_edge_threshold: float = PRIZEPICKS_3MAN_BREAKEVEN,
    strictly_betable_only: bool = True,
) -> pd.DataFrame:
    """
    Filters for positive expected value (+EV) plays where Edge % > min_edge_threshold,
    strictly requiring that the recommended play is bet-able on PrizePicks,
    and sorts descending so the highest edge plays appear at the top.
    """
    filtered = df.copy()

    # Enforce platform bet-ability (drop unbettable Unders on Demons/Goblins)
    if strictly_betable_only and "Is Bet-able" in filtered.columns:
        unbetable_count = len(filtered[~filtered["Is Bet-able"]])
        filtered = filtered[filtered["Is Bet-able"]].copy()
        logger.info(f"Filtered out {unbetable_count} unbettable plays (e.g. Under on Demon/Goblin props).")

    # Filter for +EV plays above break-even rate
    ev_df = filtered[filtered["Edge %"] > min_edge_threshold].copy()

    # Sort descending by Edge %
    ev_df = ev_df.sort_values(by="Edge %", ascending=False).reset_index(drop=True)

    logger.info(
        f"Retained {len(ev_df)} TRULY BET-ABLE +EV plays exceeding {min_edge_threshold}% win rate."
    )
    return ev_df


# ===========================================================================
# DEMO EXECUTION PIPELINE
# ===========================================================================

def load_live_matched_dataset() -> pd.DataFrame:
    """
    Loads fresh matched lines from live boards including tier metadata.
    """
    from fanduel_matcher import fetch_fanduel_odds, merge_prizepicks_and_fanduel
    from fetch_projections import fetch_live_projections, parse_prizepicks_json

    raw_pp = fetch_live_projections(use_cache=True)
    df_pp = parse_prizepicks_json(raw_pp, league_filter="NBA")
    df_fd = fetch_fanduel_odds(use_cache=True)
    return merge_prizepicks_and_fanduel(df_pp, df_fd, similarity_threshold=85)


def main():
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 1000)
    pd.set_option("display.colheader_justify", "left")

    print("\n" + "=" * 115)
    print(" SPORTS BETTING +EV TOOL - STEP 3: STRICTLY BET-ABLE NO-VIG CALCULATOR & +EV FILTER")
    print("=" * 115)

    # 1. Load matched live dataset
    logger.info("Loading matched PrizePicks vs FanDuel dataset...")
    df_matched = load_live_matched_dataset()
    logger.info(f"Loaded {len(df_matched):,} matched prop lines.")

    # 2. Apply OddsJam De-vigging Algorithm & Bet-ability Filter
    logger.info("Applying OddsJam proportional de-vigging & validating platform bet-ability...")
    df_devigged = apply_devigging(df_matched)

    # 3. Filter for +EV plays exceeding 54.95% break-even rate
    logger.info(f"Filtering for strictly bet-able +EV plays with Edge % > {PRIZEPICKS_3MAN_BREAKEVEN}%...")
    df_ev = filter_and_rank_ev(
        df_devigged,
        min_edge_threshold=PRIZEPICKS_3MAN_BREAKEVEN,
        strictly_betable_only=True,
    )

    # 4. Display Results
    print("\n" + "=" * 115)
    print(f" TOP 20 STRICTLY BET-ABLE +EV PLAYS ON PRIZEPICKS (Edge % > {PRIZEPICKS_3MAN_BREAKEVEN}% Break-Even)")
    print("=" * 115)

    columns_to_show = [
        "Player Name",
        "Stat Type",
        "Line Value",
        "PrizePicks Tier",
        "Recommended Play",
        "Edge %",
        "FanDuel Over",
        "FanDuel Under",
        "True Over %",
        "True Under %",
        "Game",
    ]

    # Display Top 20 rows
    print(df_ev[columns_to_show].head(20).to_string(index=True))
    print("=" * 115)
    print(f"\nSummary: Found {len(df_ev)} verified actionable, bet-able +EV plays on PrizePicks right now.")

    # Save to CSV
    df_ev.to_csv("prizepicks_betable_ev_plays.csv", index=False)
    logger.info("Saved actionable +EV plays to prizepicks_betable_ev_plays.csv")

    return df_ev


if __name__ == "__main__":
    main()
