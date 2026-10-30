"""
24/7 Autonomous Cloud Engine
-----------------------------
Runs continuously inside a persistent cloud worker (Render / Railway / VPS / VM):
1. During Pre-Game Windows (9:00 AM - 11:30 AM & 1:30 PM - 4:30 PM PT):
   - Polls live PrizePicks and FanDuel boards EVERY 2 MINUTES.
   - Evaluates +EV discrepancies with OddsJam de-vigging.
   - Once a slip clears threshold (>= 57.0%), immediately locks it in and posts to Discord.
2. During Live Game Window (4:30 PM - 9:30 PM PT):
   - Polls live ESPN box scores EVERY 2 MINUTES.
   - Updates player stats & progress bars.
   - EDITS the Discord slip card in-place in real-time!
3. Nightly (9:30 PM PT):
   - Automatically settles final box scores, commits/pushes to GitHub, and sleeps until next morning.
"""

import datetime
import logging
import os
import subprocess
import sys
import time

import auto_bot
import discord_notifier
import live_game_updater
import paper_tracker
import player_image_registry
import slip_card_generator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ContinuousEngine")


def get_current_pt_time() -> datetime.datetime:
    """Returns current Pacific Time."""
    # UTC minus 7 hours (PDT) / 8 hours (PST)
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=7)


def git_commit_and_push(commit_msg: str):
    """Commits updated paper_bets.json to GitHub repository."""
    try:
        subprocess.run(["git", "config", "user.name", "prizepicks-cloud-bot"], check=True)
        subprocess.run(["git", "config", "user.email", "cloud-bot@users.noreply.github.com"], check=True)
        subprocess.run(["git", "add", "paper_bets.json", "paper_bets.csv"], check=True)
        diff = subprocess.run(["git", "diff", "--staged", "--quiet"])
        if diff.returncode != 0:
            subprocess.run(["git", "commit", "-m", commit_msg], check=True)
            subprocess.run(["git", "push"], check=True)
            logger.info("Successfully pushed updated database to GitHub.")
    except Exception as e:
        logger.warning(f"Git push skipped/failed: {e}")


def run_continuous_loop():
    logger.info("=================================================================")
    logger.info("🚀 PRIZEPICKS +EV REAL-TIME CLOUD ENGINE STARTED")
    logger.info("=================================================================")

    # Initial image registry update
    player_image_registry.build_or_update_image_registry()

    while True:
        try:
            pt_now = get_current_pt_time()
            hour = pt_now.hour
            minute = pt_now.minute
            time_decimal = hour + (minute / 60.0)

            db = paper_tracker.load_database()
            has_pending = any(s.get("status") == "PENDING" for s in db.get("slips", []))
            today_str = pt_now.strftime("%Y-%m-%d")
            has_today_slip = any(s.get("date_placed", "").startswith(today_str) for s in db.get("slips", []))

            # -------------------------------------------------------------
            # MODE A: LIVE GAME IN-PLACE UPDATER (4:30 PM - 9:30 PM PT)
            # -------------------------------------------------------------
            if has_pending and 16.5 <= time_decimal < 21.5:
                logger.info("🏀 [LIVE GAMES ACTIVE] Polling ESPN live box scores...")
                live_game_updater.update_active_slips_live()
                # Sleep 2 minutes
                time.sleep(120)
                continue

            # -------------------------------------------------------------
            # MODE B: NIGHTLY FINAL SETTLEMENT (After 9:30 PM PT)
            # -------------------------------------------------------------
            if has_pending and time_decimal >= 21.5:
                logger.info("🌙 [NIGHTLY SETTLE] Running final box score settlement...")
                paper_tracker.auto_settle_pending_slips()
                git_commit_and_push(f"Auto-settle nightly slips for {today_str}")
                logger.info("Settled for the night. Sleeping 15 minutes...")
                time.sleep(900)
                continue

            # -------------------------------------------------------------
            # MODE C: PRE-GAME +EV SCANNING WINDOWS (EVERY 2 MINUTES)
            # Window 1: 9:00 AM - 11:30 AM PT (9.0 <= time <= 11.5)
            # Window 2: 1:30 PM - 4:30 PM PT (13.5 <= time <= 16.5)
            # -------------------------------------------------------------
            in_window_1 = (9.0 <= time_decimal <= 11.5)
            in_window_2 = (13.5 <= time_decimal <= 16.5)

            if (in_window_1 or in_window_2) and not has_today_slip and not has_pending:
                logger.info(f"🔍 [SCAN WINDOW ACTIVE - {pt_now.strftime('%H:%M:%S')} PT] Scanning boards (Every 2 mins)...")
                res = auto_bot.run_pipeline(min_threshold=57.0, force=False)
                if res.get("status") == "PLACED":
                    logger.info("🎯 SLIP LOCKED IN & POSTED TO DISCORD!")
                    git_commit_and_push(f"Lock in slip #{res.get('slip_id')} for {today_str}")
                # Sleep 2 minutes
                time.sleep(120)
                continue

            # -------------------------------------------------------------
            # MODE D: OFF-HOURS / WAITING
            # -------------------------------------------------------------
            # If slip already placed today, or outside active windows, sleep 5 minutes
            time.sleep(300)

        except Exception as e:
            logger.error(f"Error in continuous engine loop: {e}", exc_info=True)
            time.sleep(60)


if __name__ == "__main__":
    run_continuous_loop()
