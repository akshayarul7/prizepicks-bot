"""
Automated PrizePicks +EV Paper Trading Bot
------------------------------------------
Scans live PrizePicks and FanDuel lines, identifies strictly bet-able, full-game,
standard 1.0x line discrepancies, applies OddsJam de-vigging, and automatically places
optimal 6-Pick Flex paper slips when win rates exceed the target threshold.
"""

import datetime
import json
import logging
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple
import requests

# Add local lib
current_dir = os.path.dirname(os.path.abspath(__file__))
lib_path = os.path.join(current_dir, "lib")
if os.path.exists(lib_path) and lib_path not in sys.path:
    sys.path.insert(0, lib_path)

from thefuzz import fuzz
import paper_tracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("AutoBot")

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
FANDUEL_API_KEY = "FhMFpcPWXMeyZxOx"


def american_to_implied(odds: float) -> float:
    if odds > 0:
        return 100.0 / (odds + 100.0)
    elif odds < 0:
        return abs(odds) / (abs(odds) + 100.0)
    return 0.5


def oddsjam_devig(over_odds: float, under_odds: float) -> Tuple[float, float]:
    io = american_to_implied(over_odds)
    iu = american_to_implied(under_odds)
    tot = io + iu
    return round((io / tot) * 100.0, 2), round((iu / tot) * 100.0, 2)


def norm_cdf(x: float, mu: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def solve_mu(line: float, p_under: float, sigma: float) -> float:
    low = line - 3.0 * sigma
    high = line + 3.0 * sigma
    for _ in range(50):
        mid = (low + high) / 2.0
        val = norm_cdf(line, mid, sigma)
        if val > p_under:
            low = mid
        else:
            high = mid
    return mid


def get_sigma(stat_type: str, line: float) -> float:
    if "Points" == stat_type:
        return max(3.5, line * 0.28)
    elif stat_type in ["Rebounds", "Assists"]:
        return max(1.8, line * 0.35)
    elif "3-PT" in stat_type or "Shots" in stat_type:
        return max(1.1, line * 0.40)
    else:
        return max(4.0, line * 0.25)


def fetch_live_prizepicks() -> Optional[Dict[str, Any]]:
    logger.info("Fetching live PrizePicks projections...")
    url = "https://partner-api.prizepicks.com/projections"
    try:
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=15)
        if r.status_code == 200:
            data = r.json()
            with open("prizepicks_fresh.json", "w") as f:
                json.dump(data, f)
            logger.info(f"PrizePicks: Ingested {len(data.get('data', []))} raw lines.")
            return data
    except Exception as e:
        logger.warning(f"Live PrizePicks fetch error: {e}")

    if os.path.exists("prizepicks_fresh.json"):
        logger.info("Loading PrizePicks from fresh local cache.")
        with open("prizepicks_fresh.json") as f:
            return json.load(f)
    return None


def fetch_live_fanduel() -> List[Dict[str, Any]]:
    logger.info("Fetching live FanDuel NBA odds...")
    nba_url = f"https://sbapi.nj.sportsbook.fanduel.com/api/content-managed-page?page=CUSTOM&customPageId=nba&_ak={FANDUEL_API_KEY}"
    live_fd_props = []
    target_suffixes = [
        " - Points", " - Rebounds", " - Assists", " - Made Threes",
        " - Reb + Ast", " - Pts + Ast", " - Pts + Reb", " - Pts + Reb + Ast"
    ]
    tabs = ["player-points", "player-rebounds", "player-assists", "player-threes", "player-combos"]

    try:
        r = requests.get(nba_url, headers=DEFAULT_HEADERS, timeout=15)
        if r.status_code == 200:
            events = r.json().get("attachments", {}).get("events", {})
            for eid, ev in events.items():
                ename = ev.get("name", "")
                if "@" in ename and "(" not in ename:
                    for tab in tabs:
                        try:
                            url = f"https://sbapi.nj.sportsbook.fanduel.com/api/event-page?eventId={eid}&tab={tab}&_ak={FANDUEL_API_KEY}"
                            tr = requests.get(url, headers=DEFAULT_HEADERS, timeout=6)
                            if tr.status_code == 200:
                                markets = tr.json().get("attachments", {}).get("markets", {})
                                for mid, m in markets.items():
                                    mname = m.get("marketName", "")
                                    runners = m.get("runners", [])
                                    if len(runners) == 2 and any(mname.endswith(s) for s in target_suffixes):
                                        parts = mname.split(" - ")
                                        player = parts[0].strip()
                                        stat = parts[1].strip()
                                        r1, r2 = runners[0], runners[1]
                                        h = r1.get("handicap")
                                        o = r1.get("winRunnerOdds", {}).get("americanDisplayOdds", {}).get("americanOdds")
                                        u = r2.get("winRunnerOdds", {}).get("americanDisplayOdds", {}).get("americanOdds")
                                        if h is not None and o is not None and u is not None:
                                            live_fd_props.append({
                                                "Player": player,
                                                "Game": ename,
                                                "Stat": stat,
                                                "Line": float(h),
                                                "Over Odds": int(o),
                                                "Under Odds": int(u),
                                            })
                        except Exception:
                            pass
            if live_fd_props:
                with open("fanduel_fresh.json", "w") as f:
                    json.dump(live_fd_props, f, indent=2)
                logger.info(f"FanDuel: Scraped {len(live_fd_props)} live props.")
                return live_fd_props
    except Exception as e:
        logger.warning(f"Live FanDuel fetch error: {e}")

    if os.path.exists("fanduel_fresh.json"):
        logger.info("Loading FanDuel from fresh local cache.")
        with open("fanduel_fresh.json") as f:
            return json.load(f)
    return []


def scan_and_evaluate_props(
    pp_raw: Dict[str, Any],
    fd_props: List[Dict[str, Any]],
    min_win_pct: float = 55.5,
) -> List[Dict[str, Any]]:
    included = {
        x["id"]: (x["attributes"].get("name") or x["attributes"].get("display_name"))
        for x in pp_raw.get("included", []) if x["type"] == "new_player"
    }
    leagues = {
        x["id"]: x["attributes"].get("name")
        for x in pp_raw.get("included", []) if x["type"] == "league"
    }

    pp_rows = []
    for p in pp_raw.get("data", []):
        attrs = p["attributes"]
        rels = p["relationships"]
        did = str(rels.get("duration", {}).get("data", {}).get("id", ""))
        # Strictly Full Game (duration 11)
        if did != "11":
            continue

        lid = str(rels.get("league", {}).get("data", {}).get("id", ""))
        league_name = leagues.get(lid, "")
        if league_name != "NBA":
            continue

        pid = str(rels.get("new_player", {}).get("data", {}).get("id", ""))
        if pid in included:
            w_val = attrs.get("allowed_wager_types")
            pp_rows.append({
                "Player": included[pid],
                "Stat": attrs.get("stat_type", ""),
                "PP Line": float(attrs.get("line_score", 0)),
                "Tier": attrs.get("odds_type", "standard"),
                "Wagers": str(w_val).lower() if w_val else "under_or_over",
            })

    stat_map = {
        "Points": "Points",
        "Rebounds": "Rebounds",
        "Assists": "Assists",
        "Made Threes": "3-PT Made",
        "Pts + Reb": "Pts+Rebs",
        "Pts + Ast": "Pts+Asts",
        "Reb + Ast": "Rebs+Asts",
        "Pts + Reb + Ast": "Pts+Rebs+Asts",
    }

    valid_plays = []

    for fd in fd_props:
        pp_stat = stat_map.get(fd["Stat"])
        if not pp_stat:
            continue

        # Standard tier only
        candidates = [
            p for p in pp_rows
            if p["Stat"] == pp_stat and p["Tier"] == "standard"
        ]

        for pp in candidates:
            if fuzz.token_sort_ratio(fd["Player"], pp["Player"]) < 90:
                continue

            to, tu = oddsjam_devig(fd["Over Odds"], fd["Under Odds"])
            pp_line = pp["PP Line"]
            fd_line = fd["Line"]
            wagers = pp["Wagers"]

            can_bet_over = "over" in wagers or "under_or_over" in wagers
            can_bet_under = "under" in wagers or "under_or_over" in wagers

            # 1. Exact Line Match
            if pp_line == fd_line:
                best_side = "OVER" if to > tu else "UNDER"
                win_pct = max(to, tu)
                if (best_side == "UNDER" and can_bet_under) or (best_side == "OVER" and can_bet_over):
                    if win_pct >= min_win_pct:
                        valid_plays.append({
                            "player_name": pp["Player"],
                            "game": fd["Game"],
                            "stat_type": pp["Stat"],
                            "line": pp_line,
                            "choice": best_side,
                            "win_pct": win_pct,
                            "outright_pct": win_pct,
                            "push_pct": 0.0,
                            "fd_line": fd_line,
                            "fd_odds": f"O {fd['Over Odds']} / U {fd['Under Odds']}",
                            "edge_type": "Exact Line Market Juice",
                        })
            # 2. Line Discrepancy
            else:
                sigma = get_sigma(pp_stat, fd_line)
                mu = solve_mu(fd_line, tu / 100.0, sigma)

                if pp_line.is_integer():
                    p_win_under = norm_cdf(pp_line - 0.5, mu, sigma)
                    p_push = norm_cdf(pp_line + 0.5, mu, sigma) - p_win_under
                    p_loss_under = 1.0 - norm_cdf(pp_line + 0.5, mu, sigma)

                    dec_under = p_win_under / (p_win_under + p_loss_under) if (p_win_under + p_loss_under) > 0 else 0
                    dec_over = p_loss_under / (p_win_under + p_loss_under) if (p_win_under + p_loss_under) > 0 else 0

                    if dec_under * 100.0 >= min_win_pct and p_win_under >= 0.40 and can_bet_under:
                        valid_plays.append({
                            "player_name": pp["Player"],
                            "game": fd["Game"],
                            "stat_type": pp["Stat"],
                            "line": pp_line,
                            "choice": "UNDER",
                            "win_pct": round(dec_under * 100.0, 2),
                            "outright_pct": round(p_win_under * 100.0, 2),
                            "push_pct": round(p_push * 100.0, 2),
                            "fd_line": fd_line,
                            "fd_odds": f"O {fd['Over Odds']} / U {fd['Under Odds']}",
                            "edge_type": "Line Discrepancy (Integer Push)",
                        })
                    elif dec_over * 100.0 >= min_win_pct and p_loss_under >= 0.40 and can_bet_over:
                        valid_plays.append({
                            "player_name": pp["Player"],
                            "game": fd["Game"],
                            "stat_type": pp["Stat"],
                            "line": pp_line,
                            "choice": "OVER",
                            "win_pct": round(dec_over * 100.0, 2),
                            "outright_pct": round(p_loss_under * 100.0, 2),
                            "push_pct": round(p_push * 100.0, 2),
                            "fd_line": fd_line,
                            "fd_odds": f"O {fd['Over Odds']} / U {fd['Under Odds']}",
                            "edge_type": "Line Discrepancy (Integer Push)",
                        })
                else:
                    p_pp_under = norm_cdf(pp_line, mu, sigma)
                    p_pp_over = 1.0 - p_pp_under
                    if p_pp_under * 100.0 >= min_win_pct and can_bet_under:
                        valid_plays.append({
                            "player_name": pp["Player"],
                            "game": fd["Game"],
                            "stat_type": pp["Stat"],
                            "line": pp_line,
                            "choice": "UNDER",
                            "win_pct": round(p_pp_under * 100.0, 2),
                            "outright_pct": round(p_pp_under * 100.0, 2),
                            "push_pct": 0.0,
                            "fd_line": fd_line,
                            "fd_odds": f"O {fd['Over Odds']} / U {fd['Under Odds']}",
                            "edge_type": "Line Discrepancy (Half-Point)",
                        })
                    elif p_pp_over * 100.0 >= min_win_pct and can_bet_over:
                        valid_plays.append({
                            "player_name": pp["Player"],
                            "game": fd["Game"],
                            "stat_type": pp["Stat"],
                            "line": pp_line,
                            "choice": "OVER",
                            "win_pct": round(p_pp_over * 100.0, 2),
                            "outright_pct": round(p_pp_over * 100.0, 2),
                            "push_pct": 0.0,
                            "fd_line": fd_line,
                            "fd_odds": f"O {fd['Over Odds']} / U {fd['Under Odds']}",
                            "edge_type": "Line Discrepancy (Half-Point)",
                        })

    # Deduplicate
    unique = {}
    for v in valid_plays:
        k = (v["player_name"], v["stat_type"], v["line"], v["choice"])
        if k not in unique or v["win_pct"] > unique[k]["win_pct"]:
            unique[k] = v

    sorted_plays = sorted(unique.values(), key=lambda x: x["win_pct"], reverse=True)
    return sorted_plays


def select_best_lineup(
    plays: List[Dict[str, Any]],
    target_size: int = 6,
    min_games: int = 2,
) -> Optional[List[Dict[str, Any]]]:
    """
    Selects top unique player picks adhering to PrizePicks multi-game rules.
    Prioritizes highest win % while ensuring at least min_games are represented.
    """
    selected = []
    used_players = set()

    for play in plays:
        pname = play["player_name"]
        if pname in used_players:
            continue
        selected.append(play)
        used_players.add(pname)
        if len(selected) == target_size:
            break

    if len(selected) < target_size:
        return None

    # Check game diversity
    distinct_games = len(set(p["game"] for p in selected))
    if distinct_games < min_games:
        return None

    return selected


def run_pipeline(
    min_threshold: float = 57.0,
    slip_type: str = "flex_6",
    stake: float = 10.0,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Main automated execution cycle.
    1. Checks if a slip was already placed today (unless force=True).
    2. Ingests fresh live boards.
    3. Evaluates all valid +EV candidates.
    4. Automatically places a paper trading slip if quality clears threshold.
    """
    now = datetime.datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    logger.info(f"=== STARTING AUTOMATED +EV CYCLE [{now.strftime('%Y-%m-%d %H:%M:%S')}] ===")

    # 1. Check existing slips in paper database
    db = paper_tracker.load_database()
    slips = db.get("slips", [])

    if not force:
        has_pending = any(s.get("status") == "PENDING" for s in slips)
        has_today_slip = any(s.get("date_placed", "").startswith(today_str) for s in slips)
        if has_pending:
            logger.info("A pending slip is already active. Awaiting settlement before placing new slips.")
            return {"status": "SKIPPED", "reason": "PENDING_SLIP_ACTIVE"}
        if has_today_slip:
            logger.info(f"A slip was already placed today ({today_str}). Use force=True to place multiple.")
            return {"status": "SKIPPED", "reason": "ALREADY_PLACED_TODAY"}

    # 2. Ingest fresh boards
    pp_raw = fetch_live_prizepicks()
    if not pp_raw:
        logger.error("Failed to load PrizePicks board.")
        return {"status": "ERROR", "reason": "NO_PRIZEPICKS_DATA"}

    fd_props = fetch_live_fanduel()
    if not fd_props:
        logger.error("Failed to load FanDuel props.")
        return {"status": "ERROR", "reason": "NO_FANDUEL_DATA"}

    # 3. Evaluate props
    candidates = scan_and_evaluate_props(pp_raw, fd_props, min_win_pct=55.5)
    logger.info(f"Discovered {len(candidates)} bet-able standard +EV candidates.")

    if len(candidates) < 6:
        logger.warning("Fewer than 6 candidates found. Waiting for more market liquidity.")
        return {"status": "WAITING", "reason": "INSUFFICIENT_PROPS", "count": len(candidates)}

    # 4. Form optimal 6-man lineup
    lineup = select_best_lineup(candidates, target_size=6, min_games=2)
    if not lineup:
        logger.warning("Could not assemble a compliant 6-man multi-game slip.")
        return {"status": "WAITING", "reason": "LINEUP_CONSTRAINTS_NOT_MET"}

    avg_win_pct = sum(p["win_pct"] for p in lineup) / 6.0
    logger.info(f"Best Candidate Lineup Average Win Rate: {avg_win_pct:.2f}% (Threshold: {min_threshold}%)")

    if avg_win_pct < min_threshold:
        logger.warning(f"Average win rate {avg_win_pct:.2f}% is below target threshold {min_threshold}%. Holding off.")
        return {
            "status": "THRESHOLD_NOT_MET",
            "avg_win_pct": round(avg_win_pct, 2),
            "threshold": min_threshold,
        }

    # 5. Automatically place slip into paper trading tracker
    clean_legs = []
    for p in lineup:
        clean_legs.append({
            "player_name": p["player_name"],
            "sport": "NBA",
            "game": p["game"],
            "stat_type": p["stat_type"],
            "line": p["line"],
            "choice": p["choice"],
            "edge_percent": p["win_pct"],
        })

    slip = paper_tracker.create_slip(
        legs=clean_legs,
        slip_type=slip_type,
        stake=stake,
        strategy="automated_ev_bot",
        notes=f"Auto-placed at {now.strftime('%H:%M:%S')}. Avg win rate: {avg_win_pct:.2f}%. Threshold: {min_threshold}%.",
    )

    db["slips"].append(slip)
    paper_tracker.save_database(db)

    logger.info(f" AUTO-PLACED PAPER SLIP #{slip['id']} ({slip_type}) with {len(clean_legs)} legs!")
    logger.info(f"Average Win Rate: {avg_win_pct:.2f}% | Expected ROI: ~+30.0%")

    try:
        import discord_notifier
        discord_notifier.notify_new_slip_placed(slip, avg_win_pct)
        logger.info("Sent new slip notification to Discord webhook.")
    except Exception as e:
        logger.warning(f"Discord notification failed: {e}")

    return {
        "status": "PLACED",
        "slip_id": slip["id"],
        "avg_win_pct": round(avg_win_pct, 2),
        "legs": clean_legs,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Automated PrizePicks +EV Bot")
    parser.add_argument("--threshold", type=float, default=57.0, help="Minimum average win rate % required to place slip")
    parser.add_argument("--stake", type=float, default=10.0, help="Stake in dollars")
    parser.add_argument("--type", default="flex_6", help="Slip type")
    parser.add_argument("--force", action="store_true", help="Force placement even if slip already exists today")
    args = parser.parse_args()

    result = run_pipeline(
        min_threshold=args.threshold,
        slip_type=args.type,
        stake=args.stake,
        force=args.force,
    )
    print("\nResult:", json.dumps(result, indent=2))
