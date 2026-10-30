"""
Live In-Game Tracker & Real-Time Card Updater
---------------------------------------------
Polls live in-game box scores from ESPN during active games (e.g. every 3-5 mins),
updates player progress bars, and EDITS the existing Discord card message in-place!
"""

import datetime
import json
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional
import requests

import discord_notifier
import paper_tracker
import player_image_registry
import slip_card_generator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("LiveUpdater")


def fetch_live_espn_stats() -> Dict[str, Dict[str, float]]:
    """
    Fetches real-time box scores from ESPN for active and completed games today.
    """
    scoreboard_url = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
    all_players = {}

    try:
        r = requests.get(scoreboard_url, timeout=10)
        if r.status_code != 200:
            return all_players

        events = r.json().get("events", [])
        for ev in events:
            # Track both in-progress and completed games
            ev_id = ev.get("id")
            sum_url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={ev_id}"
            s_resp = requests.get(sum_url, timeout=10)
            if s_resp.status_code != 200:
                continue

            sum_data = s_resp.json()
            boxscore = sum_data.get("boxscore", {})
            players_data = boxscore.get("players", [])

            for team in players_data:
                for stat_grp in team.get("statistics", []):
                    keys = stat_grp.get("names", [])
                    athletes = stat_grp.get("athletes", [])

                    for athlete in athletes:
                        ath_name = athlete.get("athlete", {}).get("displayName")
                        stats_vals = athlete.get("stats", [])
                        if not ath_name or not stats_vals:
                            continue

                        stats_dict = dict(zip(keys, stats_vals))

                        def parse_float(val):
                            try:
                                return float(val)
                            except (ValueError, TypeError):
                                return 0.0

                        pts = parse_float(stats_dict.get("PTS", stats_dict.get("points", 0)))
                        reb = parse_float(stats_dict.get("REB", stats_dict.get("rebounds", 0)))
                        ast = parse_float(stats_dict.get("AST", stats_dict.get("assists", 0)))

                        all_players[ath_name] = {
                            "Points": pts,
                            "Rebounds": reb,
                            "Assists": ast,
                            "Pts+Rebs": pts + reb,
                            "Pts+Asts": pts + ast,
                            "Rebs+Asts": reb + ast,
                            "Pts+Rebs+Asts": pts + reb + ast,
                        }
    except Exception as e:
        logger.warning(f"Live ESPN fetch error: {e}")

    return all_players


def update_active_slips_live():
    """
    Finds pending slips, updates their current live stats,
    and edits the Discord card in-place.
    """
    db = paper_tracker.load_database()
    pending_slips = [s for s in db.get("slips", []) if s.get("status") == "PENDING"]

    if not pending_slips:
        logger.info("No pending slips to track live.")
        return

    live_stats = fetch_live_espn_stats()
    if not live_stats:
        logger.info("No live games in progress right now.")
        return

    reg = player_image_registry.build_or_update_image_registry()

    for slip in pending_slips:
        updated_any = False
        for leg in slip.get("legs", []):
            pname = leg.get("player_name")
            stat_type = leg.get("stat_type")

            # Check if player in live stats
            matched_name = None
            for es_name in live_stats:
                if pname.lower() in es_name.lower() or es_name.lower() in pname.lower():
                    matched_name = es_name
                    break

            if matched_name and stat_type in live_stats[matched_name]:
                cur_val = live_stats[matched_name][stat_type]
                if leg.get("actual_result") != cur_val:
                    leg["actual_result"] = cur_val
                    updated_any = True
                    logger.info(f"Live Stat Update: {pname} {stat_type} = {cur_val} (Target: {leg.get('line')})")

        if updated_any:
            paper_tracker.save_database(db)
            # Edit existing Discord card in-place!
            msg_id = slip.get("discord_message_id")
            p_imgs = {l.get("player_name"): reg.get(l.get("player_name")) for l in slip.get("legs", [])}
            slip_card_generator.send_slip_card_to_discord(
                slip,
                p_imgs,
                discord_notifier.DISCORD_WEBHOOK_URL,
                message_id=msg_id,
            )
            logger.info(f"Updated live progress card on Discord for slip #{slip['id']}.")


if __name__ == "__main__":
    update_active_slips_live()
