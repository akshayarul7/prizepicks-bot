"""
24/7 Autonomous Cloud Engine (Budget & IP Guarded)
---------------------------------------------------
Runs continuously inside a persistent cloud worker (Railway / Render / Koyeb):
1. During Pre-Game Windows (9:00 AM - 11:30 AM & 1:30 PM - 4:30 PM PT):
   - Polls live PrizePicks and FanDuel boards EVERY 2 MINUTES.
   - Evaluates +EV discrepancies with OddsJam de-vigging.
   - Once a slip clears threshold (>= 57.0%), immediately locks it in and posts to Discord.
2. During Live Game Window (4:30 PM - 9:30 PM PT):
   - Polls live ESPN box scores EVERY 2 MINUTES.
   - Updates player stats & progress bars.
   - EDITS the Discord slip card in-place in real-time!
3. Nightly (9:30 PM - 10:00 PM PT):
   - Automatically settles final box scores, commits/pushes to GitHub.
4. Deep Night Hibernate (10:00 PM - 8:30 AM PT):
   - Sleeps in 30-minute blocks with garbage collection to ensure $5/month Railway credit lasts easily!
5. Automated IP Block / Cloudflare / 403 & Exception Alerts:
   - Dispatches instant Discord @everyone alerts if scrapers encounter 403/429/Cloudflare captchas.
   - Alerts on unhandled exceptions with tracebacks.
"""

import datetime
import gc
import http.server
import json
import logging
import os
import resource
import subprocess
import sys
import threading
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

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

_last_error_alert_ts = 0.0


def alert_engine_error(title: str, msg: str, tb: str = ""):
    global _last_error_alert_ts
    now = time.time()
    if now - _last_error_alert_ts > 900:  # Throttle to once every 15 minutes
        _last_error_alert_ts = now
        logger.error(f"🚨 DISPATCHING DISCORD ERROR ALERT: {title}")
        discord_notifier.notify_error_alert(title, msg, tb)


def get_current_pt_time() -> datetime.datetime:
    """
    Returns exact current Pacific Time (automatically accounts for PST vs PDT Daylight Saving Time).
    """
    return datetime.datetime.now(ZoneInfo("America/Los_Angeles"))


def get_scanning_phase(time_decimal: float) -> Tuple[bool, float, str]:
    """
    Dynamic 3-Stage Decaying Threshold Architecture (Pacific Time):
    - Phase 1 (9:00 AM - 1:00 PM PT): Threshold 58.5%
      Only fires on high-conviction morning pricing blunders (>= -150 juice).
    - Phase 2 (1:30 PM - 3:30 PM PT): Threshold 57.8%
      Captures emerging injury replacements and confirmed starting lineup news.
    - Phase 3 (3:30 PM - 4:15 PM PT): Threshold 57.0%
      Pre-lock final window (45 mins before earliest tip-off). Locks in the #1 highest-EV lineup on the board.
    """
    if 9.0 <= time_decimal < 13.0:
        return True, 58.5, "Phase 1: Morning Blunder Hunter (58.5% Hurdle)"
    elif 13.5 <= time_decimal < 15.5:
        return True, 57.8, "Phase 2: Afternoon Lineup Builder (57.8% Hurdle)"
    elif 15.5 <= time_decimal <= 16.25:
        return True, 57.0, "Phase 3: Pre-Lock Peak Consensus (57.0% Hurdle)"
    return False, 57.0, "Standby"


def get_memory_mb() -> float:
    """Returns current memory consumption in MB."""
    try:
        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # On Linux (Railway), ru_maxrss is in KB; on macOS it is in bytes
        if sys.platform == "darwin":
            return round(usage / (1024.0 * 1024.0), 2)
        return round(usage / 1024.0, 2)
    except Exception:
        return 0.0


def git_commit_and_push(commit_msg: str):
    """Commits updated paper_bets.json to GitHub repository using GITHUB_TOKEN if available."""
    try:
        token = os.environ.get("GITHUB_TOKEN")
        repo = os.environ.get("GITHUB_REPOSITORY", "akshayarul7/prizepicks-bot")
        if token:
            remote_url = f"https://x-access-token:{token}@github.com/{repo}.git"
            subprocess.run(["git", "remote", "set-url", "origin", remote_url], check=True)

        subprocess.run(["git", "config", "user.name", "prizepicks-cloud-bot"], check=True)
        subprocess.run(["git", "config", "user.email", "cloud-bot@users.noreply.github.com"], check=True)
        subprocess.run(
            ["git", "add", "paper_bets.json", "paper_bets.csv", "paper_bets_volume.json", "paper_bets_volume.csv"],
            check=True,
        )
        diff = subprocess.run(["git", "diff", "--staged", "--quiet"])
        if diff.returncode != 0:
            subprocess.run(["git", "commit", "-m", commit_msg], check=True)
            subprocess.run(["git", "push", "origin", "main"], check=True)
            logger.info("Successfully pushed updated database to GitHub.")
    except Exception as e:
        logger.warning(f"Git push skipped/failed: {e}")


class CloudHealthHandler(http.server.BaseHTTPRequestHandler):
    """Provides a lightweight HTTP health-check and test endpoints for Render / Koyeb."""
    def do_GET(self):
        # 1. Manual Alert Test endpoint
        if self.path == "/test-alert":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            success = discord_notifier.post_to_discord(
                [{
                    "title": "🧪 CLOUD BOT TEST ALERT",
                    "description": "✅ **Communication Verified!** Render cloud worker is successfully sending payloads to Discord.",
                    "color": 0x2ECC71,
                    "fields": [
                        {"name": "Host", "value": "Render.com Free Web Service", "inline": True},
                        {"name": "Status", "value": "Healthy & Online", "inline": True},
                    ],
                    "footer": {"text": "PrizePicks Cloud Engine • Manual Test Trigger"},
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }],
                content="🔔 **Cloud Bot Test Notification**"
            )
            resp = {"status": "success" if success else "failed", "message": "Check Discord now!"}
            self.wfile.write(json.dumps(resp, indent=2).encode("utf-8"))
            return

        # 2. Manual +EV Scan Test endpoint
        if self.path == "/test-scan":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            def background_test_scan():
                try:
                    logger.info("🧪 [TEST SCAN TRIGGERED VIA URL] Running live board scan...")
                    res = auto_bot.run_pipeline(min_threshold=55.0, force=True)
                    if res.get("status") == "PLACED":
                        logger.info(f"🎯 Test slip #{res.get('slip_id')} placed and sent to Discord!")
                        pt_now = get_current_pt_time()
                        git_commit_and_push(f"Lock in test slip #{res.get('slip_id')} for {pt_now.strftime('%Y-%m-%d')}")
                except Exception as e:
                    logger.error(f"Test scan error: {e}")
                    alert_engine_error("Manual Test Scan Error", str(e), traceback.format_exc())

            threading.Thread(target=background_test_scan, daemon=True).start()
            resp = {
                "status": "scan_started",
                "message": "Manual +EV scan triggered in background! Check Discord in 20-30 seconds for the card.",
            }
            self.wfile.write(json.dumps(resp, indent=2).encode("utf-8"))
            return

        # 3. Database export endpoint
        if self.path.startswith("/db"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                db_target = "paper_bets_volume.json" if ("volume" in self.path or "file=paper_bets_volume.json" in self.path) else "paper_bets.json"
                db = paper_tracker.load_database(db_target)
                self.wfile.write(json.dumps(db, indent=2).encode("utf-8"))
            except Exception as e:
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        # 4. Push Baseline Model Calibration Report to Discord anytime
        if self.path in ["/post-calibration", "/stats", "/calibration"]:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                db = paper_tracker.load_database("paper_bets.json")
                slips = db.get("slips", [])
                report = paper_tracker.generate_performance_report(slips)
                cal = report.get("model_calibration", {})
                success = discord_notifier.notify_model_calibration(cal, report, webhook_url=discord_notifier.DISCORD_WEBHOOK_URL)
                resp = {
                    "status": "success" if success else "failed",
                    "track": "Baseline (1-Slip/Day)",
                    "message": "Sent live Model Calibration & Accuracy Report to Discord! Check your channel.",
                    "total_legs_evaluated": cal.get("total_legs", 0),
                    "empirical_hit_rate": f"{cal.get('actual_hit_rate', 0.0)}%",
                    "predicted_win_rate": f"{cal.get('avg_predicted_win_rate', 0.0)}%",
                    "calibration_delta": f"{cal.get('calibration_delta', 0.0):+}%",
                }
            except Exception as e:
                resp = {"status": "error", "error": str(e)}
            self.wfile.write(json.dumps(resp, indent=2).encode("utf-8"))
            return

        # 5. Push Volume Model Calibration Report to Dedicated Volume Discord channel
        if self.path in ["/post-calibration-volume", "/stats-volume", "/calibration-volume"]:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                db = paper_tracker.load_database("paper_bets_volume.json")
                slips = db.get("slips", [])
                report = paper_tracker.generate_performance_report(slips)
                cal = report.get("model_calibration", {})
                success = discord_notifier.notify_model_calibration(cal, report, webhook_url=discord_notifier.DISCORD_VOLUME_WEBHOOK_URL)
                resp = {
                    "status": "success" if success else "failed",
                    "track": "Accelerated (3-Slips/Day)",
                    "message": "Sent live 3-Slip Accelerated Calibration Report to Discord! Check your volume channel.",
                    "total_legs_evaluated": cal.get("total_legs", 0),
                    "empirical_hit_rate": f"{cal.get('actual_hit_rate', 0.0)}%",
                    "predicted_win_rate": f"{cal.get('avg_predicted_win_rate', 0.0)}%",
                    "calibration_delta": f"{cal.get('calibration_delta', 0.0):+}%",
                }
            except Exception as e:
                resp = {"status": "error", "error": str(e)}
            self.wfile.write(json.dumps(resp, indent=2).encode("utf-8"))
            return

        # 6. Default Health Check and Performance Dashboard (Dual Track)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            pt_now = get_current_pt_time()
            time_dec = pt_now.hour + (pt_now.minute / 60.0)
            is_active, cur_thresh, phase_title = get_scanning_phase(time_dec)

            db_base = paper_tracker.load_database("paper_bets.json")
            rep_base = paper_tracker.generate_performance_report(db_base.get("slips", []))

            db_vol = paper_tracker.load_database("paper_bets_volume.json")
            rep_vol = paper_tracker.generate_performance_report(db_vol.get("slips", []))

            now_ts = time.time()
            pp_cooldown_rem = max(0, int(auto_bot._pp_cooldown_until - now_ts))
            fd_cooldown_rem = max(0, int(auto_bot._fd_cooldown_until - now_ts))

            payload = {
                "service": "PrizePicks +EV Real-Time Cloud Engine (Dual-Track)",
                "status": "ONLINE",
                "time_pt": pt_now.strftime("%Y-%m-%d %I:%M:%S %p PT"),
                "ram_usage_mb": get_memory_mb(),
                "api_health": {
                    "prizepicks": "IN_COOLDOWN" if pp_cooldown_rem > 0 else "HEALTHY_READY",
                    "prizepicks_cooldown_sec": pp_cooldown_rem,
                    "prizepicks_cache_age_sec": int(now_ts - auto_bot._pp_cache_ts) if auto_bot._pp_cache_ts else None,
                    "fanduel": "IN_COOLDOWN" if fd_cooldown_rem > 0 else "HEALTHY_READY",
                    "fanduel_cooldown_sec": fd_cooldown_rem,
                },
                "scanning_schedule": {
                    "current_phase": phase_title,
                    "target_threshold": f"{cur_thresh:.1f}%",
                    "window_active": is_active,
                },
                "endpoints": {
                    "health_check": "/",
                    "test_discord_alert": "/test-alert",
                    "trigger_live_scan": "/test-scan",
                    "view_database_baseline": "/db",
                    "view_database_volume": "/db?file=paper_bets_volume.json",
                    "push_calibration_baseline": "/stats",
                    "push_calibration_volume": "/stats-volume",
                },
                "track_baseline_1_slip_day": {
                    "total_staked": f"${rep_base.get('total_staked', 0.0):.2f}",
                    "total_returned": f"${rep_base.get('total_returned', 0.0):.2f}",
                    "net_profit": f"+${rep_base.get('net_profit', 0.0):.2f}" if rep_base.get('net_profit', 0.0) >= 0 else f"-${abs(rep_base.get('net_profit', 0.0)):.2f}",
                    "roi": f"{rep_base.get('roi_percent', 0.0):+.1f}%",
                    "slips_record": f"{rep_base.get('slips_won', 0)}W - {rep_base.get('slips_lost', 0)}L - {rep_base.get('slips_pushed', 0)}P",
                    "leg_win_rate": f"{rep_base.get('leg_win_rate', 0.0):.1f}% ({rep_base.get('total_legs_hit', 0)}/{rep_base.get('total_legs_hit', 0) + rep_base.get('total_legs_missed', 0)})",
                    "total_slips": len(db_base.get("slips", [])),
                },
                "track_accelerated_3_slips_day": {
                    "total_staked": f"${rep_vol.get('total_staked', 0.0):.2f}",
                    "total_returned": f"${rep_vol.get('total_returned', 0.0):.2f}",
                    "net_profit": f"+${rep_vol.get('net_profit', 0.0):.2f}" if rep_vol.get('net_profit', 0.0) >= 0 else f"-${abs(rep_vol.get('net_profit', 0.0)):.2f}",
                    "roi": f"{rep_vol.get('roi_percent', 0.0):+.1f}%",
                    "slips_record": f"{rep_vol.get('slips_won', 0)}W - {rep_vol.get('slips_lost', 0)}L - {rep_vol.get('slips_pushed', 0)}P",
                    "leg_win_rate": f"{rep_vol.get('leg_win_rate', 0.0):.1f}% ({rep_vol.get('total_legs_hit', 0)}/{rep_vol.get('total_legs_hit', 0) + rep_vol.get('total_legs_missed', 0)})",
                    "total_slips": len(db_vol.get("slips", [])),
                },
            }
        except Exception as e:
            payload = {"service": "PrizePicks +EV Real-Time Cloud Engine", "status": "ONLINE", "error": str(e)}
        self.wfile.write(json.dumps(payload, indent=2).encode("utf-8"))

    def log_message(self, format, *args):
        # Mute standard HTTP access logging to keep application logs clean
        pass


def start_health_server():
    """Starts background HTTP server on the cloud platform's assigned port."""
    port = int(os.environ.get("PORT", 8080))
    try:
        server = http.server.HTTPServer(("0.0.0.0", port), CloudHealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        logger.info(f"🌐 Cloud Health Check Web Server listening on port {port}")
    except Exception as e:
        logger.warning(f"Health server failed to bind on port {port}: {e}")


def run_continuous_loop():
    logger.info("=================================================================")
    logger.info("🚀 PRIZEPICKS +EV REAL-TIME CLOUD ENGINE STARTED")
    logger.info("🛡️ Guardrails: 403 WAF Monitoring + Railway $5 Budget Saver Active")
    logger.info("=================================================================")

    # Start health check server for Render / Koyeb
    start_health_server()


    # Initial image registry update
    try:
        player_image_registry.build_or_update_image_registry()
    except Exception as e:
        logger.warning(f"Initial image registry build error: {e}")

    while True:
        try:
            pt_now = get_current_pt_time()
            hour = pt_now.hour
            minute = pt_now.minute
            time_decimal = hour + (minute / 60.0)

            db_base = paper_tracker.load_database("paper_bets.json")
            db_vol = paper_tracker.load_database("paper_bets_volume.json")

            has_pending_base = any(s.get("status") == "PENDING" for s in db_base.get("slips", []))
            has_pending_vol = any(s.get("status") == "PENDING" for s in db_vol.get("slips", []))
            has_pending = has_pending_base or has_pending_vol

            today_str = pt_now.strftime("%Y-%m-%d")
            has_today_base = any(s.get("date_placed", "").startswith(today_str) for s in db_base.get("slips", []))
            today_vol_count = sum(1 for s in db_vol.get("slips", []) if s.get("date_placed", "").startswith(today_str))

            mem_mb = get_memory_mb()
            logger.info(
                f"⏱️ Tick: {pt_now.strftime('%I:%M %p PT')} | RAM: {mem_mb} MB | "
                f"Baseline Slip: {has_today_base} | Volume Slips: {today_vol_count}/3"
            )

            # -------------------------------------------------------------
            # MODE A: LIVE GAME IN-PLACE UPDATER (4:00 PM - 9:30 PM PT)
            # -------------------------------------------------------------
            if has_pending and 16.0 <= time_decimal < 21.5:
                logger.info("🏀 [LIVE GAMES ACTIVE] Polling ESPN live box scores across both tracks...")
                try:
                    live_game_updater.update_active_slips_live()
                except Exception as e:
                    logger.error(f"Live updater error: {e}")
                    alert_engine_error("Live Game Updater Error", str(e), traceback.format_exc())
                gc.collect()
                time.sleep(120)
                continue

            # -------------------------------------------------------------
            # MODE B: NIGHTLY FINAL SETTLEMENT (After 9:30 PM PT)
            # -------------------------------------------------------------
            if has_pending and time_decimal >= 21.5:
                logger.info("🌙 [NIGHTLY SETTLE] Running final box score settlement across both tracks...")
                try:
                    if has_pending_base:
                        paper_tracker.auto_settle_pending_slips(
                            db_file="paper_bets.json",
                            webhook_url=discord_notifier.DISCORD_WEBHOOK_URL,
                        )
                    if has_pending_vol:
                        paper_tracker.auto_settle_pending_slips(
                            db_file="paper_bets_volume.json",
                            webhook_url=discord_notifier.DISCORD_VOLUME_WEBHOOK_URL,
                        )
                    git_commit_and_push(f"Auto-settle nightly slips for {today_str}")
                    logger.info("Settled for the night.")
                except Exception as e:
                    logger.error(f"Nightly settlement error: {e}")
                    alert_engine_error("Nightly Settlement Error", str(e), traceback.format_exc())
                gc.collect()
                time.sleep(900)
                continue

            # -------------------------------------------------------------
            # MODE C: PRE-GAME +EV SCANNING (DYNAMIC 3-STAGE DECAYING THRESHOLD)
            # Phase 1: 9:00 AM - 1:00 PM PT (58.5% threshold - morning pricing blunders)
            # Phase 2: 1:30 PM - 3:30 PM PT (57.8% threshold - afternoon lineup news)
            # Phase 3: 3:30 PM - 4:15 PM PT (57.0% threshold - pre-lock peak EV consensus)
            # -------------------------------------------------------------
            is_scan_active, dynamic_thresh, phase_name = get_scanning_phase(time_decimal)

            placed_any = False

            # 1. Baseline Track (1 slip/day)
            if is_scan_active and not has_today_base and not has_pending_base:
                logger.info(f"🔍 [Baseline - {pt_now.strftime('%I:%M %p PT')}] Scanning for top #1 daily slip (Hurdle: {dynamic_thresh}%)...")
                try:
                    res = auto_bot.run_pipeline(min_threshold=dynamic_thresh, force=False)
                    if res.get("status") == "PLACED":
                        logger.info(f"🎯 BASELINE SLIP LOCKED IN & POSTED TO DISCORD ({phase_name})!")
                        placed_any = True
                except Exception as e:
                    logger.error(f"Baseline scan error: {e}")

            # 2. Accelerated Track (3 slips/day)
            if is_scan_active and today_vol_count < 3:
                logger.info(f"⚡ [Accelerated - {pt_now.strftime('%I:%M %p PT')}] Scanning for up to 3 non-overlapping slips ({today_vol_count}/3 placed, Hurdle: {dynamic_thresh}%)...")
                try:
                    vol_res = auto_bot.run_volume_pipeline(min_threshold=dynamic_thresh, target_slips=3)
                    if vol_res.get("placed_count", 0) > 0:
                        logger.info(f"🎯 ACCELERATED VOLUME SLIPS LOCKED IN ({vol_res.get('placed_count')} slips)!")
                        placed_any = True
                except Exception as e:
                    logger.error(f"Accelerated scan error: {e}")

            if placed_any:
                git_commit_and_push(f"Lock in slips for {today_str}")

            if is_scan_active and (not has_today_base or today_vol_count < 3):
                gc.collect()
                # If rate-limited, sleep longer to clear Cloudflare 1015 cooldown
                if auto_bot._pp_cooldown_until > time.time():
                    wait_sec = min(900, max(360, int(auto_bot._pp_cooldown_until - time.time()) + 10))
                    logger.info(f"⏳ Cooling down for {wait_sec}s to clear WAF rate-limit...")
                    time.sleep(wait_sec)
                else:
                    time.sleep(360)
                continue

            # -------------------------------------------------------------
            # MODE D: OVERNIGHT HIBERNATE (10:00 PM - 8:30 AM PT)
            # -------------------------------------------------------------
            if time_decimal >= 22.0 or time_decimal < 8.5:
                logger.info("💤 [OVERNIGHT DEEP SLEEP] Sports slates idle. Hibernating 30 mins to preserve credit...")
                gc.collect()
                time.sleep(1800)
                continue

            # -------------------------------------------------------------
            # MODE E: DAYTIME / EVENING STANDBY (Between scan windows or post-lock)
            # -------------------------------------------------------------
            if (has_today_base or has_pending_base) and (today_vol_count >= 3 or has_pending_vol):
                logger.info(f"✅ [ALL SLIPS ACTIVE] Both baseline & 3-slip accelerated slates active. Waiting for live tip-off / settlement...")
            else:
                logger.info(f"☕ [STANDBY - {pt_now.strftime('%I:%M %p PT')}] Waiting for next scanning window...")
            gc.collect()
            time.sleep(300)

        except Exception as e:
            tb = traceback.format_exc()
            logger.error(f"Critical error in continuous engine loop: {e}\n{tb}")
            alert_engine_error("Continuous Loop Crash", str(e), tb)
            gc.collect()
            time.sleep(60)


if __name__ == "__main__":
    run_continuous_loop()
