"""
PrizePicks Paper Trading & Strategy Backtesting / Forward-Testing Engine
-------------------------------------------------------------------------
Allows users to place simulated ("fake") bets, track pending slips,
settle them against final box scores (automatically via ESPN API or manually),
and evaluate whether a betting strategy is profitable over time (Win Rate, ROI, Net Units).
"""

import argparse
import datetime
import json
import logging
import os
import sys
import uuid
from typing import Any, Dict, List, Optional, Tuple
import pandas as pd

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("PaperTracker")

DB_FILE = "paper_bets.json"
CSV_FILE = "paper_bets.csv"

# ===========================================================================
# PRIZEPICKS OFFICIAL PAYOUT MULTIPLIERS
# ===========================================================================
# Format: {slip_type: {num_hits: payout_multiplier}}
# Multipliers are gross return on stake (e.g., 5.0x returns $50 on $10 stake = +$40 net profit).
PAYOUT_TABLES = {
    "power_2": {2: 3.0},
    "power_3": {3: 5.0},
    "power_4": {4: 10.0},
    "flex_3": {3: 3.0, 2: 0.75},
    "flex_4": {4: 5.0, 3: 1.25},
    "flex_5": {5: 10.0, 4: 2.0, 3: 0.4},
    "flex_6": {6: 25.0, 5: 2.0, 4: 0.4},
    "single": {1: 1.82},  # Theoretical single leg payout (-122 odds)
}

DOWNGRADE_MAP = {
    "power_4": "power_3",
    "power_3": "power_2",
    "power_2": "single",
    "flex_6": "flex_5",
    "flex_5": "flex_4",
    "flex_4": "flex_3",
}


# ===========================================================================
# DATABASE / PERSISTENCE LAYER
# ===========================================================================

def load_database(filepath: str = DB_FILE) -> Dict[str, Any]:
    if not os.path.exists(filepath):
        return {"slips": [], "version": "1.0"}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to read {filepath}: {e}")
        return {"slips": [], "version": "1.0"}


def save_database(data: Dict[str, Any], filepath: str = DB_FILE, export_csv: bool = True):
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    if export_csv:
        export_slips_to_csv(data["slips"], CSV_FILE)


def export_slips_to_csv(slips: List[Dict[str, Any]], filepath: str = CSV_FILE):
    flat_rows = []
    for slip in slips:
        for idx, leg in enumerate(slip.get("legs", []), start=1):
            flat_rows.append({
                "Slip ID": slip.get("id"),
                "Date Placed": slip.get("date_placed"),
                "Strategy": slip.get("strategy"),
                "Slip Type": slip.get("slip_type"),
                "Stake ($)": slip.get("stake"),
                "Slip Status": slip.get("status"),
                "Payout ($)": slip.get("return_payout"),
                "Net Profit ($)": slip.get("net_profit"),
                "Leg #": idx,
                "Player": leg.get("player_name"),
                "Stat Type": leg.get("stat_type"),
                "Line": leg.get("line"),
                "Choice": leg.get("choice"),
                "Actual Result": leg.get("actual_result"),
                "Leg Status": leg.get("status"),
                "Edge %": leg.get("edge_percent"),
                "Game": leg.get("game"),
            })
    if flat_rows:
        df = pd.DataFrame(flat_rows)
        df.to_csv(filepath, index=False)
        logger.debug(f"Exported {len(flat_rows)} legs to {filepath}")
    elif os.path.exists(filepath):
        # Clear CSV if no slips
        pd.DataFrame().to_csv(filepath, index=False)


# ===========================================================================
# CORE BETTING & SETTLEMENT LOGIC
# ===========================================================================

def create_slip(
    legs: List[Dict[str, Any]],
    slip_type: str = "power_3",
    stake: float = 10.0,
    strategy: str = "default_strategy",
    notes: str = "",
) -> Dict[str, Any]:
    """
    Creates a new paper betting slip.
    """
    slip_id = str(uuid.uuid4())[:8]
    # Always timestamp in Pacific Time (PST/PDT)
    now_pt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=7)
    now_str = now_pt.strftime("%Y-%m-%d %I:%M:%S %p PT")

    clean_legs = []
    for leg in legs:
        clean_legs.append({
            "leg_id": str(uuid.uuid4())[:6],
            "player_name": leg.get("player_name", "").strip(),
            "sport": leg.get("sport", "NBA").strip().upper(),
            "game": leg.get("game", ""),
            "stat_type": leg.get("stat_type", "").strip(),
            "line": float(leg.get("line", 0.0)),
            "choice": leg.get("choice", "OVER").strip().upper(),
            "actual_result": None,
            "status": "PENDING",  # PENDING, HIT, MISS, PUSH
            "edge_percent": float(leg.get("edge_percent", 0.0)),
        })

    slip = {
        "id": slip_id,
        "date_placed": now_str,
        "strategy": strategy,
        "slip_type": slip_type,
        "stake": float(stake),
        "notes": notes,
        "legs": clean_legs,
        "status": "PENDING",  # PENDING, WON, LOST, PUSH
        "return_payout": 0.0,
        "net_profit": 0.0,
        "payout_multiplier": 0.0,
    }
    return slip


def grade_leg(choice: str, line: float, actual: float) -> str:
    """
    Evaluates whether an individual pick hit, missed, or pushed.
    """
    if actual > line:
        return "HIT" if choice == "OVER" else "MISS"
    elif actual < line:
        return "HIT" if choice == "UNDER" else "MISS"
    else:
        return "PUSH"


def grade_slip(slip: Dict[str, Any]) -> Dict[str, Any]:
    """
    Grades an entire slip once all legs are settled.
    Calculates PrizePicks payout multiplier and net profit.
    """
    legs = slip.get("legs", [])
    if not legs:
        return slip

    # If any leg is still pending, slip remains pending
    if any(l.get("status") == "PENDING" for l in legs):
        slip["status"] = "PENDING"
        return slip

    hits = sum(1 for l in legs if l.get("status") == "HIT")
    pushes = sum(1 for l in legs if l.get("status") == "PUSH")
    total_legs = len(legs)

    slip_type = slip.get("slip_type", f"power_{total_legs}")

    # Handle push downgrades
    adjusted_slip_type = slip_type
    for _ in range(pushes):
        adjusted_slip_type = DOWNGRADE_MAP.get(adjusted_slip_type, adjusted_slip_type)

    payout_table = PAYOUT_TABLES.get(adjusted_slip_type, {})
    multiplier = payout_table.get(hits, 0.0)

    # All legs push: stake refund
    if pushes == total_legs:
        multiplier = 1.0

    stake = slip.get("stake", 10.0)
    return_payout = round(stake * multiplier, 2)
    net_profit = round(return_payout - stake, 2)

    slip["payout_multiplier"] = multiplier
    slip["return_payout"] = return_payout
    slip["net_profit"] = net_profit

    if multiplier > 1.0:
        slip["status"] = "WON"
    elif multiplier == 1.0:
        slip["status"] = "PUSH"
    else:
        slip["status"] = "LOST"

    return slip


# ===========================================================================
# AUTOMATED STAT SETTLEMENT (ESPN PUBLIC API)
# ===========================================================================

def fetch_espn_boxscore_data(sport: str = "basketball", league: str = "nba", date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Fetches final box score stats for all games on a given date (YYYYMMDD).
    Defaults to today or yesterday.
    """
    import requests
    if not date_str:
        date_str = datetime.datetime.now().strftime("%Y%m%d")

    scoreboard_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/scoreboard?dates={date_str}"
    all_game_summaries = []

    try:
        r = requests.get(scoreboard_url, timeout=10)
        if r.status_code != 200:
            return []
        data = r.json()
        events = data.get("events", [])
        for ev in events:
            # Check if game is completed
            status = ev.get("status", {}).get("type", {})
            if status.get("completed", False):
                ev_id = ev.get("id")
                sum_url = f"https://site.api.espn.com/apis/site/v2/sports/{sport}/{league}/summary?event={ev_id}"
                s_resp = requests.get(sum_url, timeout=10)
                if s_resp.status_code == 200:
                    all_game_summaries.append(s_resp.json())
    except Exception as e:
        logger.warning(f"Could not reach ESPN API (check internet connection): {e}")

    return all_game_summaries


def parse_player_stats_from_summaries(summaries: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """
    Parses player statistics across all completed games into normalized dict:
    { "payton pritchard": { "points": 18, "3-pt made": 3, "assists": 5, ... } }
    """
    all_players = {}

    for summary in summaries:
        boxscore = summary.get("boxscore", {})
        players_data = boxscore.get("players", [])

        for team in players_data:
            stats_categories = team.get("statistics", [])
            for cat in stats_categories:
                raw_keys = cat.get("names", [])
                athletes = cat.get("athletes", [])
                for athlete_entry in athletes:
                    athlete = athlete_entry.get("athlete", {})
                    name = athlete.get("displayName", "").strip().lower()
                    vals = athlete_entry.get("stats", [])

                    p_stats = {}
                    for k, v in zip(raw_keys, vals):
                        k_norm = k.lower().replace("-", " ").strip()
                        # Parse 3PT shooting "3-7"
                        if "-" in str(v) and "3pt" in k_norm:
                            try:
                                p_stats["3-pt made"] = float(v.split("-")[0])
                            except ValueError:
                                pass
                        else:
                            try:
                                p_stats[k_norm] = float(v)
                            except ValueError:
                                pass

                    pts = p_stats.get("pts", p_stats.get("points", 0.0))
                    reb = p_stats.get("reb", p_stats.get("rebounds", 0.0))
                    ast = p_stats.get("ast", p_stats.get("assists", 0.0))
                    stl = p_stats.get("stl", p_stats.get("steals", 0.0))
                    blk = p_stats.get("blk", p_stats.get("blocks", 0.0))

                    normalized_stats = {
                        "points": pts,
                        "rebounds": reb,
                        "assists": ast,
                        "steals": stl,
                        "blocks": blk,
                        "3-pt made": p_stats.get("3-pt made", p_stats.get("3pt", 0.0)),
                        "pts+rebs+asts": pts + reb + ast,
                        "pts+rebs": pts + reb,
                        "pts+asts": pts + ast,
                        "rebs+asts": reb + ast,
                        "blks+stls": blk + stl,
                    }

                    if name not in all_players:
                        all_players[name] = {}
                    all_players[name].update(normalized_stats)

    return all_players


def auto_settle_pending_slips(date_str: Optional[str] = None):
    """
    Queries ESPN for box scores and automatically resolves all pending legs and slips.
    """
    db = load_database()
    pending_slips = [s for s in db.get("slips", []) if s.get("status") == "PENDING"]
    if not pending_slips:
        logger.info("No pending slips found to settle.")
        return

    logger.info(f"Checking box scores to settle {len(pending_slips)} pending slips...")
    summaries = fetch_espn_boxscore_data(sport="basketball", league="nba", date_str=date_str)
    if not summaries:
        logger.warning("No completed game box scores found on ESPN. Games may not have finished yet.")
        return

    player_stats = parse_player_stats_from_summaries(summaries)
    settled_any = False

    for slip in pending_slips:
        for leg in slip.get("legs", []):
            if leg.get("status") == "PENDING":
                p_name = leg["player_name"].lower()
                stat_key = leg["stat_type"].lower().strip()

                # Find player in box scores
                matched_name = None
                for name in player_stats:
                    if p_name in name or name in p_name:
                        matched_name = name
                        break

                if matched_name and stat_key in player_stats[matched_name]:
                    actual_val = player_stats[matched_name][stat_key]
                    leg["actual_result"] = actual_val
                    leg["status"] = grade_leg(leg["choice"], leg["line"], actual_val)
                    settled_any = True
                    logger.info(f"Auto-Settled: {leg['player_name']} {leg['stat_type']} -> Actual: {actual_val} vs Line: {leg['line']} ({leg['status']})")

        grade_slip(slip)

    if settled_any:
        save_database(db)
        logger.info("Successfully updated slip statuses with official box scores.")
        try:
            import discord_notifier
            report = generate_performance_report(db.get("slips", []))
            for s in pending_slips:
                if s.get("status") in ["WON", "LOST", "PUSH"]:
                    discord_notifier.notify_settlement_recap(s, {
                        "total_staked": report.get("total_staked", 0.0),
                        "total_returned": report.get("total_returned", 0.0),
                        "net_profit": report.get("net_profit", 0.0),
                        "roi_percent": report.get("roi_percent", 0.0),
                        "won_slips": report.get("slips_won", 0),
                        "lost_slips": report.get("slips_lost", 0),
                        "hit_legs": report.get("total_legs_hit", 0),
                        "missed_legs": report.get("total_legs_missed", 0),
                        "leg_hit_rate": report.get("leg_win_rate", 0.0),
                    })
            logger.info("Sent settlement recap to Discord webhook.")
        except Exception as e:
            logger.warning(f"Discord settlement notification failed: {e}")
    else:
        logger.info("No matching player stats found in final games yet.")


# ===========================================================================
# ANALYTICS & STRATEGY EVALUATION
# ===========================================================================

def generate_performance_report(slips: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not slips:
        return {"total_slips": 0}

    total_slips = len(slips)
    settled_slips = [s for s in slips if s.get("status") in ["WON", "LOST", "PUSH"]]
    pending_slips = [s for s in slips if s.get("status") == "PENDING"]

    total_staked = sum(s.get("stake", 0.0) for s in settled_slips)
    total_returned = sum(s.get("return_payout", 0.0) for s in settled_slips)
    net_profit = round(total_returned - total_staked, 2)
    roi_percent = round((net_profit / total_staked * 100), 2) if total_staked > 0 else 0.0

    slips_won = sum(1 for s in settled_slips if s.get("status") == "WON")
    slips_lost = sum(1 for s in settled_slips if s.get("status") == "LOST")
    slips_pushed = sum(1 for s in settled_slips if s.get("status") == "PUSH")
    slip_win_rate = round((slips_won / len(settled_slips) * 100), 2) if settled_slips else 0.0

    # Leg-level analytics
    all_legs = [leg for s in settled_slips for leg in s.get("legs", [])]
    settled_legs = [l for l in all_legs if l.get("status") in ["HIT", "MISS", "PUSH"]]
    legs_hit = sum(1 for l in settled_legs if l.get("status") == "HIT")
    legs_missed = sum(1 for l in settled_legs if l.get("status") == "MISS")
    leg_win_rate = round((legs_hit / (legs_hit + legs_missed) * 100), 2) if (legs_hit + legs_missed) > 0 else 0.0

    # Strategy breakdown
    strategies = {}
    for s in settled_slips:
        strat = s.get("strategy", "unknown")
        if strat not in strategies:
            strategies[strat] = {"staked": 0.0, "returned": 0.0, "won": 0, "total": 0}
        strategies[strat]["staked"] += s.get("stake", 0.0)
        strategies[strat]["returned"] += s.get("return_payout", 0.0)
        strategies[strat]["total"] += 1
        if s.get("status") == "WON":
            strategies[strat]["won"] += 1

    strat_summary = {}
    for strat, v in strategies.items():
        stk = v["staked"]
        ret = v["returned"]
        pft = round(ret - stk, 2)
        strat_roi = round((pft / stk * 100), 2) if stk > 0 else 0.0
        strat_wr = round((v["won"] / v["total"] * 100), 2) if v["total"] > 0 else 0.0
        strat_summary[strat] = {
            "slips": v["total"],
            "win_rate": strat_wr,
            "staked": stk,
            "profit": pft,
            "roi_percent": strat_roi,
        }

    return {
        "total_slips": total_slips,
        "settled_slips": len(settled_slips),
        "pending_slips": len(pending_slips),
        "slips_won": slips_won,
        "slips_lost": slips_lost,
        "slips_pushed": slips_pushed,
        "slip_win_rate": slip_win_rate,
        "leg_win_rate": leg_win_rate,
        "total_legs_hit": legs_hit,
        "total_legs_missed": legs_missed,
        "total_staked": total_staked,
        "total_returned": total_returned,
        "net_profit": net_profit,
        "roi_percent": roi_percent,
        "strategies": strat_summary,
    }


def print_dashboard():
    db = load_database()
    slips = db.get("slips", [])
    report = generate_performance_report(slips)

    print("\n" + "=" * 80)
    print(" 📊 PRIZEPICKS STRATEGY PAPER TRADING & PERFORMANCE DASHBOARD")
    print("=" * 80)

    if report["total_slips"] == 0:
        print(" No paper bets placed yet! Run 'python paper_tracker.py auto-place' or 'place'.")
        print("=" * 80 + "\n")
        return

    print(f" Total Slips Placed : {report['total_slips']:<6} | Settled: {report['settled_slips']:<6} | Pending: {report['pending_slips']:<6}")
    print(f" Slip Record (W-L-P): {report['slips_won']}-{report['slips_lost']}-{report['slips_pushed']} ({report['slip_win_rate']}%)")
    print(f" Leg Hit Rate       : {report['total_legs_hit']}/{report['total_legs_hit'] + report['total_legs_missed']} ({report['leg_win_rate']}%) [PrizePicks Hurdle: 54.95%]")
    print("-" * 80)
    print(f" Total Staked       : ${report['total_staked']:,.2f}")
    print(f" Total Returned     : ${report['total_returned']:,.2f}")
    profit_sign = "+" if report['net_profit'] >= 0 else ""
    print(f" Net Profit / Loss  : {profit_sign}${report['net_profit']:,.2f}")
    print(f" Strategy ROI       : {profit_sign}{report['roi_percent']}%")
    print("=" * 80)

    if report.get("strategies"):
        print(" STRATEGY BREAKDOWN:")
        print(f" {'Strategy':<25} | {'Slips':<6} | {'Win Rate':<10} | {'Staked':<10} | {'Profit':<10} | {'ROI %':<8}")
        print("-" * 80)
        for strat, data in report["strategies"].items():
            sign = "+" if data['profit'] >= 0 else ""
            print(f" {strat:<25} | {data['slips']:<6} | {data['win_rate']:>6.1f}%   | ${data['staked']:<8.2f} | {sign}${data['profit']:<8.2f} | {sign}{data['roi_percent']}%")
        print("=" * 80)

    print(" RECENT SLIPS:")
    for slip in slips[-5:]:
        p_sign = "+" if slip.get('net_profit', 0) >= 0 else ""
        print(f" Slip #{slip['id']} [{slip['slip_type']}] - Status: {slip['status']} | Stake: ${slip['stake']} | P&L: {p_sign}${slip.get('net_profit', 0):.2f}")
        for leg in slip.get("legs", []):
            res_str = f"Actual: {leg['actual_result']}" if leg.get('actual_result') is not None else "Pending"
            print(f"   ↳ {leg['player_name']} - {leg['stat_type']} {leg['choice']} {leg['line']} ({leg['status']} - {res_str})")
    print("=" * 80 + "\n")


# ===========================================================================
# COMMAND LINE ACTIONS
# ===========================================================================

def auto_place_from_ev(
    csv_path: str = "prizepicks_betable_ev_plays.csv",
    slip_type: str = "power_3",
    stake: float = 10.0,
    strategy_name: str = "oddsjam_devig_ev",
    max_slips: int = 1,
):
    if not os.path.exists(csv_path):
        logger.error(f"Cannot find CSV at {csv_path}. Run devig_calculator.py first.")
        return

    df = pd.read_csv(csv_path)
    if df.empty:
        logger.warning(f"No plays found in {csv_path}.")
        return

    leg_count_map = {
        "power_2": 2, "power_3": 3, "power_4": 4,
        "flex_3": 3, "flex_4": 4, "flex_5": 5, "flex_6": 6,
        "single": 1
    }
    needed_legs = leg_count_map.get(slip_type, 3)

    if len(df) < needed_legs:
        logger.warning(f"Need at least {needed_legs} plays to create a {slip_type} slip, but only {len(df)} available.")
        return

    db = load_database()
    slips_created = 0

    for i in range(0, min(len(df), needed_legs * max_slips), needed_legs):
        chunk = df.iloc[i : i + needed_legs]
        if len(chunk) < needed_legs:
            break

        legs = []
        for _, row in chunk.iterrows():
            legs.append({
                "player_name": row.get("Player Name", row.get("Player Name (PrizePicks)", "")),
                "sport": row.get("League", "NBA"),
                "game": row.get("Game / Matchup", row.get("Game", "")),
                "stat_type": row.get("Stat Type", ""),
                "line": float(row.get("Line Value", 0.0)),
                "choice": row.get("Recommended Play", "OVER"),
                "edge_percent": float(row.get("Edge %", 0.0)),
            })

        slip = create_slip(
            legs=legs,
            slip_type=slip_type,
            stake=stake,
            strategy=strategy_name,
            notes=f"Auto-generated from {csv_path} with top {needed_legs} +EV picks",
        )
        db["slips"].append(slip)
        slips_created += 1
        logger.info(f"Placed Paper Slip #{slip['id']} ({slip_type}) with {len(legs)} legs at ${stake} stake.")

    save_database(db)
    print(f"\n Successfully placed {slips_created} paper slip(s) into {DB_FILE} and {CSV_FILE}!")


def manual_settle_slip(slip_id: str, results_dict: Dict[str, float]):
    db = load_database()
    found = False
    for slip in db.get("slips", []):
        if slip["id"] == slip_id:
            found = True
            for leg in slip.get("legs", []):
                p_name = leg["player_name"].lower()
                for key, val in results_dict.items():
                    if key.lower() in p_name or key.lower() == leg.get("leg_id", "").lower():
                        leg["actual_result"] = float(val)
                        leg["status"] = grade_leg(leg["choice"], leg["line"], float(val))
                        logger.info(f"Leg {leg['player_name']} ({leg['stat_type']}): Actual={val}, Line={leg['line']}, Choice={leg['choice']} -> {leg['status']}")
            grade_slip(slip)
            logger.info(f"Slip #{slip_id} settled: Status={slip['status']}, Profit=${slip['net_profit']}")

    if found:
        save_database(db)
    else:
        logger.error(f"Slip #{slip_id} not found.")


def manual_place_slip(legs_raw: str, slip_type: str, stake: float, strategy: str):
    """
    Parses 'Player,Stat,Line,Choice;Player2,Stat2,Line2,Choice2' into a slip.
    """
    legs = []
    for item in legs_raw.split(";"):
        parts = [p.strip() for p in item.split(",")]
        if len(parts) >= 4:
            legs.append({
                "player_name": parts[0],
                "stat_type": parts[1],
                "line": float(parts[2]),
                "choice": parts[3].upper(),
            })
    if not legs:
        print("Error: Invalid legs format. Example: --legs 'LeBron James,Points,24.5,OVER;Payton Pritchard,3-PT Made,2.5,UNDER'")
        return

    db = load_database()
    slip = create_slip(legs=legs, slip_type=slip_type, stake=stake, strategy=strategy)
    db["slips"].append(slip)
    save_database(db)
    print(f"\n Placed custom paper slip #{slip['id']} ({slip_type}) with {len(legs)} legs.")


def reset_database():
    save_database({"slips": [], "version": "1.0"})
    print("\n Cleared all paper betting slips. Fresh start ready!")


def list_slips():
    db = load_database()
    slips = db.get("slips", [])
    if not slips:
        print("\nNo slips in database.")
        return
    print(f"\n--- {len(slips)} Total Slips in {DB_FILE} ---")
    for s in slips:
        print(f"Slip ID: {s['id']} | Strategy: {s.get('strategy')} | Type: {s.get('slip_type')} | Stake: ${s.get('stake')} | Status: {s.get('status')} | Net: ${s.get('net_profit', 0.0)}")
        for l in s.get("legs", []):
            print(f"   [ID: {l['leg_id']}] {l['player_name']} - {l['stat_type']} {l['choice']} {l['line']} ({l['status']})")
    print()


# ===========================================================================
# MAIN ENTRYPOINT
# ===========================================================================

def main():
    parser = argparse.ArgumentParser(description="PrizePicks Paper Trading & Strategy Backtesting Engine")
    subparsers = parser.add_subparsers(dest="command")

    # summary
    subparsers.add_parser("summary", help="Show performance dashboard (Win Rate, ROI, Net Profit)")

    # list
    subparsers.add_parser("list", help="List all recorded slips and legs")

    # reset
    subparsers.add_parser("reset", help="Wipe all tracked paper slips to start clean")

    # auto-place
    auto_parser = subparsers.add_parser("auto-place", help="Automatically place a slip from prizepicks_betable_ev_plays.csv")
    auto_parser.add_argument("--csv", default="prizepicks_betable_ev_plays.csv", help="Path to devigged +EV CSV")
    auto_parser.add_argument("--type", default="power_3", choices=list(PAYOUT_TABLES.keys()), help="Slip type (power_2, power_3, flex_5, etc.)")
    auto_parser.add_argument("--stake", type=float, default=10.0, help="Simulated stake in dollars")
    auto_parser.add_argument("--strategy", default="oddsjam_devig_ev", help="Strategy label for tracking")
    auto_parser.add_argument("--max-slips", type=int, default=1, help="Number of slips to place")

    # place (custom)
    place_parser = subparsers.add_parser("place", help="Place a custom paper slip")
    place_parser.add_argument("--legs", required=True, help="Format: 'Player,Stat,Line,Choice;Player2,Stat2,Line2,Choice2'")
    place_parser.add_argument("--type", default="power_3", choices=list(PAYOUT_TABLES.keys()))
    place_parser.add_argument("--stake", type=float, default=10.0)
    place_parser.add_argument("--strategy", default="manual_test")

    # settle (manual)
    settle_parser = subparsers.add_parser("settle", help="Manually settle a slip by entering actual player stats")
    settle_parser.add_argument("--id", required=True, help="Slip ID to settle")
    settle_parser.add_argument("--results", required=True, help="Comma-separated player=stat pairs (e.g. 'Payton Pritchard=4,Mikal Bridges=9')")

    # settle-auto (via ESPN)
    auto_settle_parser = subparsers.add_parser("settle-auto", help="Auto-settle pending slips using official ESPN box scores")
    auto_settle_parser.add_argument("--date", default=None, help="Date in YYYYMMDD format (defaults to today)")

    args = parser.parse_args()

    if args.command == "summary" or not args.command:
        print_dashboard()
    elif args.command == "list":
        list_slips()
    elif args.command == "reset":
        reset_database()
    elif args.command == "place":
        manual_place_slip(args.legs, args.type, args.stake, args.strategy)
        print_dashboard()
    elif args.command == "auto-place":
        auto_place_from_ev(
            csv_path=args.csv,
            slip_type=args.type,
            stake=args.stake,
            strategy_name=args.strategy,
            max_slips=args.max_slips,
        )
        print_dashboard()
    elif args.command == "settle":
        pairs = {}
        for item in args.results.split(","):
            if "=" in item:
                k, v = item.split("=", 1)
                pairs[k.strip()] = float(v.strip())
        manual_settle_slip(args.id, pairs)
        print_dashboard()
    elif args.command == "settle-auto":
        auto_settle_pending_slips(date_str=args.date)
        print_dashboard()


if __name__ == "__main__":
    main()
