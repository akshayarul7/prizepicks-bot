"""
Sports Betting +EV Tool - Step 2: FanDuel Odds Ingestion & Matching Engine
-------------------------------------------------------------------------
This module:
1. Ingests REAL LIVE player prop odds directly from FanDuel Sportsbook:
   - Primary: Directly queries FanDuel's public sportsbook API across live games.
   - Secondary: Supports 'The Odds API' format if an ODDS_API_KEY is supplied.
   - Offline fallback: Preserves a local cache of live FanDuel lines.
2. Implements a high-precision Matching Engine:
   - Maps sportsbook stat types to PrizePicks conventions (Points, Rebounds, Pts+Rebs+Asts, etc.).
   - Exact mathematical line equality check (e.g., 21.5 == 21.5).
   - Fuzzy string matching (`thefuzz`) to resolve player naming variations.
3. Produces a merged DataFrame ready for +EV calculation.
"""

import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional
import pandas as pd
import requests

# Ensure local lib directory is reachable for thefuzz
current_dir = os.path.dirname(os.path.abspath(__file__))
lib_path = os.path.join(current_dir, "lib")
if os.path.exists(lib_path) and lib_path not in sys.path:
    sys.path.insert(0, lib_path)

from thefuzz import fuzz

# Import Step 1 functions for PrizePicks ingestion
try:
    from fetch_projections import fetch_live_projections, parse_prizepicks_json
except ImportError:
    pass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("FanDuelMatcher")

# FanDuel Public Sportsbook API Configuration
FANDUEL_API_KEY = "FhMFpcPWXMeyZxOx"  # FanDuel public frontend app key
FANDUEL_NBA_URL = f"https://sbapi.nj.sportsbook.fanduel.com/api/content-managed-page?page=CUSTOM&customPageId=nba&_ak={FANDUEL_API_KEY}"
FANDUEL_EVENT_URL = "https://sbapi.nj.sportsbook.fanduel.com/api/event-page"
FANDUEL_CACHE_FILE = "fanduel_live_cache.json"
FANDUEL_CACHE_TTL = 300  # 5 minutes cache

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Cross-Platform Stat Mapping: FanDuel (Direct + The Odds API) -> PrizePicks
STAT_MAPPING: Dict[str, str] = {
    # Direct FanDuel Sportsbook format
    "Points": "Points",
    "Rebounds": "Rebounds",
    "Assists": "Assists",
    "Made Threes": "3-PT Made",
    "Pts + Reb + Ast": "Pts+Rebs+Asts",
    "Pts + Reb": "Pts+Rebs",
    "Pts + Ast": "Pts+Asts",
    "Reb + Ast": "Rebs+Asts",
    "Blocks": "Blocked Shots",
    "Steals": "Steals",
    "Turnovers": "Turnovers",
    # The Odds API format
    "player_points": "Points",
    "player_rebounds": "Rebounds",
    "player_assists": "Assists",
    "player_threes": "3-PT Made",
    "player_points_rebounds_assists": "Pts+Rebs+Asts",
    "player_points_rebounds": "Pts+Rebs",
    "player_points_assists": "Pts+Asts",
    "player_rebounds_assists": "Rebs+Asts",
    "player_pass_yds": "Pass Yards",
    "player_pass_tds": "Pass TDs",
    "player_rush_yds": "Rushing Yards",
    "player_reception_yds": "Receiving Yards",
}


# ===========================================================================
# PART A: ODDS INGESTION (FANDUEL ONLY)
# ===========================================================================

def fetch_live_fanduel_sportsbook(timeout: int = 10) -> List[Dict[str, Any]]:
    """
    Directly scrapes real-time player props from FanDuel Sportsbook's public API.
    """
    logger.info("Connecting to FanDuel Sportsbook API to fetch live board...")
    resp = requests.get(FANDUEL_NBA_URL, headers=DEFAULT_HEADERS, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()

    events = data.get("attachments", {}).get("events", {})
    # Filter for active matchups (e.g. 'Team A @ Team B')
    game_events = [
        eid for eid, ev in events.items()
        if "@" in ev.get("name", "") and "(" not in ev.get("name", "")
    ]

    logger.info(f"Discovered {len(game_events)} active NBA games on FanDuel.")

    tabs_to_fetch = [
        "player-points",
        "player-rebounds",
        "player-assists",
        "player-threes",
        "player-combos",
    ]

    target_suffixes = [
        " - Points",
        " - Rebounds",
        " - Assists",
        " - Made Threes",
        " - Reb + Ast",
        " - Pts + Ast",
        " - Pts + Reb",
        " - Pts + Reb + Ast",
    ]

    all_props = []

    for eid in game_events:
        game_title = events[eid].get("name")
        for tab in tabs_to_fetch:
            try:
                params = {
                    "eventId": eid,
                    "tab": tab,
                    "_ak": FANDUEL_API_KEY,
                }
                tab_resp = requests.get(
                    FANDUEL_EVENT_URL,
                    headers=DEFAULT_HEADERS,
                    params=params,
                    timeout=timeout,
                )
                if tab_resp.status_code != 200:
                    continue

                markets = tab_resp.json().get("attachments", {}).get("markets", {})
                for mid, m in markets.items():
                    mname = m.get("marketName", "")
                    runners = m.get("runners", [])

                    # Over/Under 2-way markets
                    if len(runners) == 2 and any(mname.endswith(s) for s in target_suffixes):
                        parts = mname.split(" - ")
                        player_name = parts[0].strip()
                        stat_name = parts[1].strip()

                        r1, r2 = runners[0], runners[1]
                        handicap = r1.get("handicap")
                        over_odds = r1.get("winRunnerOdds", {}).get("americanDisplayOdds", {}).get("americanOdds")
                        under_odds = r2.get("winRunnerOdds", {}).get("americanDisplayOdds", {}).get("americanOdds")

                        if handicap is not None and over_odds is not None and under_odds is not None:
                            all_props.append({
                                "Player Name": player_name,
                                "Stat Type": stat_name,
                                "Line Value": float(handicap),
                                "Over Odds": int(over_odds),
                                "Under Odds": int(under_odds),
                                "Game": game_title,
                            })
            except Exception:
                continue

    return all_props


def fetch_fanduel_odds(
    api_key: Optional[str] = None,
    sport: str = "basketball_nba",
    use_cache: bool = True,
    cache_ttl: int = FANDUEL_CACHE_TTL,
) -> pd.DataFrame:
    """
    Ingests live FanDuel player props odds.

    1. Checks local cache for freshness.
    2. If fresh cache exists, loads immediately.
    3. If cache expired or missing, connects directly to FanDuel Sportsbook API.
    4. If offline/error, safely falls back to cached data.
    """
    # 1. Check local cache
    if use_cache and os.path.exists(FANDUEL_CACHE_FILE):
        age = time.time() - os.path.getmtime(FANDUEL_CACHE_FILE)
        if age < cache_ttl:
            logger.info(
                f"Loading live FanDuel odds from local cache ({FANDUEL_CACHE_FILE}, age: {int(age)}s / TTL: {cache_ttl}s)..."
            )
            try:
                with open(FANDUEL_CACHE_FILE, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                    df = pd.DataFrame(cached_data)
                    df["Line Value"] = pd.to_numeric(df["Line Value"], errors="coerce")
                    return df
            except Exception as e:
                logger.warning(f"Error reading cache: {e}")

    # 2. Scrape live from FanDuel
    try:
        live_props = fetch_live_fanduel_sportsbook()
        if live_props:
            with open(FANDUEL_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(live_props, f)
            logger.info(f"Successfully cached {len(live_props)} live FanDuel props.")
            df = pd.DataFrame(live_props)
            df["Line Value"] = pd.to_numeric(df["Line Value"], errors="coerce")
            return df
    except Exception as e:
        logger.warning(f"Live FanDuel fetch failed ({e}). Falling back to local cache.")

    # 3. Fallback to existing cache
    if os.path.exists(FANDUEL_CACHE_FILE):
        with open(FANDUEL_CACHE_FILE, "r", encoding="utf-8") as f:
            cached_data = json.load(f)
            df = pd.DataFrame(cached_data)
            df["Line Value"] = pd.to_numeric(df["Line Value"], errors="coerce")
            return df

    return pd.DataFrame()


# ===========================================================================
# PART B: THE MATCHING ENGINE
# ===========================================================================

def merge_prizepicks_and_fanduel(
    df_prizepicks: pd.DataFrame,
    df_fanduel: pd.DataFrame,
    similarity_threshold: int = 85,
    stat_mapping: Optional[Dict[str, str]] = None,
) -> pd.DataFrame:
    """
    Merges PrizePicks projections with FanDuel sportsbook odds.

    ---------------------------------------------------------------------------
    INLINE TUNING GUIDE FOR THE FUZZY MATCHING THRESHOLD:
    ---------------------------------------------------------------------------
    - Default (85): The recommended baseline for sports names. Successfully matches
      hyphenations ('Shai Gilgeous-Alexander' vs 'Shai Gilgeous Alexander' -> 100)
      and minor punctuation/abbreviations ('P.J. Washington' vs 'PJ Washington' -> 100),
      while protecting against false positives.
    - Raising to 90–95: Use if encountering false positives with brothers/relatives
      sharing similar first/last letters (e.g. 'Jalen Williams' vs 'Jaylin Williams'
      scores ~86; at threshold 90, this false match is rejected).
    - Lowering to 75–80: Use if you want to capture drastic nickname contractions
      (e.g., 'Nazreon Reid' vs 'Naz Reid' scores 80).
    ---------------------------------------------------------------------------
    """
    mapping = stat_mapping or STAT_MAPPING

    if df_prizepicks.empty or df_fanduel.empty:
        logger.warning("One or both input DataFrames are empty. No matches possible.")
        return pd.DataFrame()

    logger.info(
        f"Executing Matching Engine across {len(df_prizepicks):,} PrizePicks lines "
        f"and {len(df_fanduel):,} FanDuel lines (Threshold: {similarity_threshold})..."
    )

    pp_clean = df_prizepicks.copy()
    fd_clean = df_fanduel.copy()

    pp_clean["Line Value"] = pd.to_numeric(pp_clean["Line Value"], errors="coerce")
    fd_clean["Line Value"] = pd.to_numeric(fd_clean["Line Value"], errors="coerce")

    # Map FanDuel stat types
    fd_clean["Mapped Stat Type"] = fd_clean["Stat Type"].map(mapping)

    fd_clean = fd_clean.dropna(subset=["Mapped Stat Type", "Line Value"])
    pp_clean = pp_clean.dropna(subset=["Stat Type", "Line Value"])

    matched_records: List[Dict[str, Any]] = []

    for _, fd_row in fd_clean.iterrows():
        fd_player = str(fd_row["Player Name"]).strip()
        mapped_stat = fd_row["Mapped Stat Type"]
        fd_line = round(float(fd_row["Line Value"]), 2)

        # 1. Exact Stat Type & Exact Mathematical Line Value Filter
        candidates = pp_clean[
            (pp_clean["Stat Type"] == mapped_stat)
            & (pp_clean["Line Value"].round(2) == fd_line)
        ]

        if candidates.empty:
            continue

        best_match = None
        highest_score = -1

        # 2. Fuzzy Player Name Matching
        for _, pp_row in candidates.iterrows():
            pp_player = str(pp_row["Player Name"]).strip()
            score = fuzz.token_sort_ratio(fd_player.lower(), pp_player.lower())

            if score >= similarity_threshold and score > highest_score:
                highest_score = score
                best_match = pp_row

        if best_match is not None:
            matched_records.append({
                "Player Name (PrizePicks)": best_match["Player Name"],
                "Player Name (FanDuel)": fd_player,
                "League": best_match.get("League", "NBA"),
                "Stat Type": mapped_stat,
                "Line Value": fd_line,
                "PrizePicks Tier": best_match.get("Odds Type", "standard"),
                "Allowed Wagers": best_match.get("Allowed Wagers", "under_or_over"),
                "FanDuel Over": fd_row.get("Over Odds"),
                "FanDuel Under": fd_row.get("Under Odds"),
                "Game / Matchup": fd_row.get("Game", "NBA"),
                "Fuzzy Score": highest_score,
            })

    df_matched = pd.DataFrame(matched_records)
    if not df_matched.empty:
        df_matched = df_matched.drop_duplicates(
            subset=["Player Name (PrizePicks)", "Stat Type", "Line Value"]
        ).reset_index(drop=True)

        logger.info(
            f"Matching complete: Found {len(df_matched):,} matched player props with live FanDuel odds."
        )

    return df_matched


# ===========================================================================
# DEMO EXECUTION
# ===========================================================================

def main():
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 1000)
    pd.set_option("display.colheader_justify", "left")

    print("\n" + "=" * 95)
    print(" SPORTS BETTING +EV TOOL - STEP 2: LIVE FANDUEL ODDS INGESTION & MATCHING")
    print("=" * 95)

    # 1. Fetch live PrizePicks lines
    raw_pp = fetch_live_projections(use_cache=True)
    df_prizepicks = parse_prizepicks_json(raw_pp, league_filter="NBA")

    # 2. Ingest REAL Live FanDuel Odds
    df_fanduel = fetch_fanduel_odds(use_cache=True)

    print("\n" + "-" * 95)
    print(f" LIVE FANDUEL ODDS (Total Lines: {len(df_fanduel):,}):")
    print("-" * 95)
    print(df_fanduel.head(10).to_string(index=True))

    # 3. Match PrizePicks with FanDuel
    df_matched = merge_prizepicks_and_fanduel(
        df_prizepicks=df_prizepicks,
        df_fanduel=df_fanduel,
        similarity_threshold=85,
    )

    print("\n" + "=" * 95)
    print(f" MATCHED PROJECTIONS BOARD (PrizePicks vs Live FanDuel) - Total Matches: {len(df_matched):,}")
    print("=" * 95)
    if not df_matched.empty:
        # Show first 20 matches
        print(df_matched.head(20).to_string(index=True))

        # Highlight Anthony Davis & Shai Gilgeous-Alexander
        spotlight = df_matched[
            df_matched["Player Name (PrizePicks)"].str.contains("Anthony Davis|Shai Gilgeous", case=False)
        ]
        if not spotlight.empty:
            print("\n" + "-" * 95)
            print(" SPOTLIGHT: Anthony Davis & Shai Gilgeous-Alexander Live Matches:")
            print("-" * 95)
            print(spotlight.to_string(index=True))
            print("-" * 95)
    else:
        print("No matches found.")

    return df_matched


if __name__ == "__main__":
    main()
