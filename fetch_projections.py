"""
PrizePicks Live Player Props Scraper (Step 1)
---------------------------------------------
Fetches the CURRENT live player props board from PrizePicks, parses the
relational JSON:API structure, cleans the dataset, and loads it into a
Pandas DataFrame.

Endpoints:
- Primary: https://partner-api.prizepicks.com/projections
  (Accessible live without DataDome captcha blocks, returns 30,000+ active props)
- Secondary / Frontend: https://api.prizepicks.com/projections
  (Protected by DataDome WAF; requires residential proxy or active session cookies)
"""

import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional
import pandas as pd
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("PrizePicksScraper")

# Endpoint URLs
PARTNER_API_URL = "https://partner-api.prizepicks.com/projections"
FRONTEND_API_URL = "https://api.prizepicks.com/projections"

# Cache configuration (Cloudflare limits repeated hits to partner API)
CACHE_FILE_PATH = "prizepicks_live_cache.json"
CACHE_TTL_SECONDS = 120  # Re-use cached live board if fetched within 2 minutes

# ---------------------------------------------------------------------------
# CUSTOM HEADERS EXPLANATION:
# Web servers and WAFs (Cloudflare & DataDome) inspect HTTP headers and TLS fingerprints.
# Default Python 'requests' sends 'User-Agent: python-requests/x.y.z', which triggers
# an immediate 403 Forbidden.
#
# Custom Headers:
# - 'User-Agent': Mimics a modern desktop browser (Chrome on macOS/Windows).
# - 'Accept': Tells the server we accept JSON payloads.
# - 'Accept-Language': Locale language preference.
# - 'Referer' / 'Origin': Tells PrizePicks that the request is originating from their web app.
# ---------------------------------------------------------------------------
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://app.prizepicks.com/",
    "Origin": "https://app.prizepicks.com",
}


def fetch_live_projections(
    use_cache: bool = True,
    cache_ttl: int = CACHE_TTL_SECONDS,
    timeout: int = 25,
) -> Optional[Dict[str, Any]]:
    """
    Fetches the active live projections board from PrizePicks.
    Checks local cache first to avoid Cloudflare 429 rate limits.
    """
    # 1. Check local cache freshness
    if use_cache and os.path.exists(CACHE_FILE_PATH):
        file_age = time.time() - os.path.getmtime(CACHE_FILE_PATH)
        if file_age < cache_ttl:
            logger.info(
                f"Loading live board from local cache ({CACHE_FILE_PATH}, age: {int(file_age)}s / TTL: {cache_ttl}s)..."
            )
            try:
                with open(CACHE_FILE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not read cache: {e}. Re-fetching from API.")

    # 2. Query Endpoints (Try Partner API first as frontend API has DataDome captcha)
    endpoints = [
        ("Partner API (Live Board)", PARTNER_API_URL),
        ("Frontend API", FRONTEND_API_URL),
    ]

    for label, url in endpoints:
        logger.info(f"Connecting to PrizePicks via {label} ({url})...")
        try:
            response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)

            # Check for Cloudflare rate limit (429)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After", "30-60")
                logger.warning(
                    f"Rate limited (429) on {label}. Cloudflare requested cooldown: {retry_after} seconds."
                )
                continue

            # Check for DataDome bot challenge (403)
            if response.status_code == 403:
                logger.warning(
                    f"Access Denied (403) on {label}. DataDome bot protection blocked the request."
                )
                continue

            response.raise_for_status()
            json_data = response.json()

            # Save fresh response to local cache
            try:
                with open(CACHE_FILE_PATH, "w", encoding="utf-8") as f:
                    json.dump(json_data, f)
                logger.info(f"Updated local cache at {CACHE_FILE_PATH}.")
            except Exception as cache_err:
                logger.warning(f"Unable to write cache: {cache_err}")

            return json_data

        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout ({timeout}s) connecting to {label}: {e}")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error to {label}: {e}")
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON returned by {label}: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP request error for {label}: {e}")

    # Fallback to existing cache even if expired if live network call failed
    if os.path.exists(CACHE_FILE_PATH):
        logger.warning("Network calls were blocked or rate-limited. Falling back to cached live data.")
        with open(CACHE_FILE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    return None


def parse_prizepicks_json(
    json_data: Dict[str, Any],
    league_filter: Optional[str] = None,
) -> pd.DataFrame:
    """
    Parses PrizePicks JSON:API structure and extracts:
    - Player Name
    - League
    - Stat Type
    - Line Value

    Parameters:
        json_data (dict): Raw JSON from PrizePicks.
        league_filter (str, optional): Filter by league, e.g. 'NBA', 'NFL', 'CS2'.
                                      If None, all leagues are included.

    Returns:
        pd.DataFrame: Cleaned DataFrame of projections.
    """
    if not isinstance(json_data, dict):
        raise ValueError("Invalid JSON data format: Expected a dictionary.")

    projections_data = json_data.get("data", [])
    included_data = json_data.get("included", [])

    logger.info(
        f"Parsing {len(projections_data):,} projections and {len(included_data):,} reference items..."
    )

    # 1. Build lookup tables from 'included'
    players_map: Dict[str, str] = {}
    leagues_map: Dict[str, str] = {}
    stat_types_map: Dict[str, str] = {}

    for item in included_data:
        item_type = item.get("type")
        item_id = str(item.get("id"))
        attrs = item.get("attributes", {})

        if item_type == "new_player":
            # Extract player name
            players_map[item_id] = attrs.get("name") or attrs.get("display_name") or "Unknown Player"
        elif item_type == "league":
            # Extract league name (NBA, NFL, NHL, etc.)
            leagues_map[item_id] = attrs.get("name") or "Unknown League"
        elif item_type == "stat_type":
            stat_types_map[item_id] = attrs.get("name") or "Unknown Stat"

    # 2. Extract projection records
    records: List[Dict[str, Any]] = []

    for proj in projections_data:
        attrs = proj.get("attributes", {})
        rels = proj.get("relationships", {})

        # Extract Line Value
        line_value = attrs.get("line_score")

        # Extract Stat Type
        stat_type = attrs.get("stat_type")
        if not stat_type:
            stat_rel_id = str(rels.get("stat_type", {}).get("data", {}).get("id", ""))
            stat_type = stat_types_map.get(stat_rel_id, "Unknown Stat")

        # Extract Player Name
        player_rel_id = str(rels.get("new_player", {}).get("data", {}).get("id", ""))
        player_name = players_map.get(player_rel_id, attrs.get("description", "Unknown Player"))

        # Extract League
        league_rel_id = str(rels.get("league", {}).get("data", {}).get("id", ""))
        league_name = leagues_map.get(league_rel_id, "Unknown League")

        # Apply league filter if specified
        if league_filter and league_name.upper() != league_filter.upper():
            continue

        records.append({
            "Player Name": player_name,
            "League": league_name,
            "Stat Type": stat_type,
            "Line Value": line_value,
            "Odds Type": attrs.get("odds_type", "standard"),
            "Allowed Wagers": attrs.get("allowed_wager_types", "under_or_over"),
        })

    # 3. Create DataFrame
    df = pd.DataFrame(records)

    # 4. Data Cleaning
    if not df.empty:
        for col in ["Player Name", "League", "Stat Type"]:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

        df["Line Value"] = pd.to_numeric(df["Line Value"], errors="coerce")

        # Filter out invalid entries
        initial_count = len(df)
        df = df.dropna(subset=["Player Name", "Line Value"])
        df = df[df["Player Name"] != "Unknown Player"]

        # Deduplicate
        df = df.drop_duplicates(subset=["Player Name", "League", "Stat Type", "Line Value"])
        df = df.reset_index(drop=True)

        logger.info(
            f"Cleaning completed: {len(df):,} valid records retained ({initial_count - len(df):,} invalid/duplicates filtered)."
        )
    else:
        logger.warning("No records matched the criteria.")

    return df


def main():
    """Main execution entry point."""
    # Tabular formatting settings for terminal
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 1000)
    pd.set_option("display.colheader_justify", "left")

    logger.info("Starting PrizePicks Live Board Ingestion...")

    # Fetch live data
    raw_json = fetch_live_projections(use_cache=True)

    if not raw_json:
        logger.error("Failed to retrieve live data from PrizePicks. Exiting.")
        sys.exit(1)

    # Parse all projections
    df = parse_prizepicks_json(raw_json)

    print("\n" + "=" * 80)
    print(f" LIVE PRIZEPICKS PROJECTIONS BOARD (Total Live Lines: {len(df):,})")
    print("=" * 80)

    if not df.empty:
        # Display first 20 rows of the live board
        print(df.head(20).to_string(index=True))
        print("=" * 80)

        # Show breakdown by active sports
        print("\nActive Projections by League:")
        print(df["League"].value_counts().head(10).to_string())

        # If NBA is active, show the top NBA lines
        nba_df = df[df["League"] == "NBA"]
        if not nba_df.empty:
            print("\n" + "-" * 80)
            print(f" Sample Live NBA Player Props ({len(nba_df):,} total NBA lines live right now):")
            print("-" * 80)
            print(nba_df.head(10).to_string(index=True))
            print("-" * 80)
    else:
        print("No records extracted.")

    return df


if __name__ == "__main__":
    main()
