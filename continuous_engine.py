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
    """Returns current Pacific Time."""
    # UTC minus 7 hours (PDT) / 8 hours (PST)
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=7)


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
        subprocess.run(["git", "add", "paper_bets.json", "paper_bets.csv"], check=True)
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

        # 3. Default Health Check and Performance Dashboard
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        try:
            pt_now = get_current_pt_time()
            db = paper_tracker.load_database()
            slips = db.get("slips", [])
            report = paper_tracker.generate_performance_report(slips)
            payload = {
                "service": "PrizePicks +EV Real-Time Cloud Engine",
                "status": "ONLINE",
                "time_pt": pt_now.strftime("%Y-%m-%d %I:%M:%S %p PT"),
                "ram_usage_mb": get_memory_mb(),
                "endpoints": {
                    "health_check": "/",
                    "test_discord_alert": "/test-alert",
                    "trigger_live_scan": "/test-scan",
                },
                "performance": {
                    "total_staked": f"${report.get('total_staked', 0.0):.2f}",
                    "total_returned": f"${report.get('total_returned', 0.0):.2f}",
                    "net_profit": f"+${report.get('net_profit', 0.0):.2f}" if report.get('net_profit', 0.0) >= 0 else f"-${abs(report.get('net_profit', 0.0)):.2f}",
                    "roi": f"{report.get('roi_percent', 0.0):+.1f}%",
                    "slips_record": f"{report.get('slips_won', 0)}W - {report.get('slips_lost', 0)}L - {report.get('slips_pushed', 0)}P",
                    "leg_win_rate": f"{report.get('leg_win_rate', 0.0):.1f}% ({report.get('total_legs_hit', 0)}/{report.get('total_legs_hit', 0) + report.get('total_legs_missed', 0)})",
                    "total_slips": len(slips),
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

            db = paper_tracker.load_database()
            has_pending = any(s.get("status") == "PENDING" for s in db.get("slips", []))
            today_str = pt_now.strftime("%Y-%m-%d")
            has_today_slip = any(s.get("date_placed", "").startswith(today_str) for s in db.get("slips", []))

            mem_mb = get_memory_mb()
            logger.info(f"⏱️ Tick: {pt_now.strftime('%I:%M %p PT')} | RAM: {mem_mb} MB | Pending: {has_pending}")

            # -------------------------------------------------------------
            # MODE A: LIVE GAME IN-PLACE UPDATER (4:30 PM - 9:30 PM PT)
            # -------------------------------------------------------------
            if has_pending and 16.5 <= time_decimal < 21.5:
                logger.info("🏀 [LIVE GAMES ACTIVE] Polling ESPN live box scores...")
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
                logger.info("🌙 [NIGHTLY SETTLE] Running final box score settlement...")
                try:
                    paper_tracker.auto_settle_pending_slips()
                    git_commit_and_push(f"Auto-settle nightly slips for {today_str}")
                    logger.info("Settled for the night.")
                except Exception as e:
                    logger.error(f"Nightly settlement error: {e}")
                    alert_engine_error("Nightly Settlement Error", str(e), traceback.format_exc())
                gc.collect()
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
                try:
                    res = auto_bot.run_pipeline(min_threshold=57.0, force=False)
                    if res.get("status") == "PLACED":
                        logger.info("🎯 SLIP LOCKED IN & POSTED TO DISCORD!")
                        git_commit_and_push(f"Lock in slip #{res.get('slip_id')} for {today_str}")
                except Exception as e:
                    logger.error(f"Auto bot scan error: {e}")
                    alert_engine_error("AutoBot Scan Failure", str(e), traceback.format_exc())
                gc.collect()
                time.sleep(120)
                continue

            # -------------------------------------------------------------
            # MODE D: OVERNIGHT HIBERNATE (10:00 PM - 8:30 AM PT)
            # -------------------------------------------------------------
            # Crucial for Railway $5 budget: sleep in 30-min chunks, zero CPU
            if time_decimal >= 22.0 or time_decimal < 8.5:
                logger.info("💤 [OVERNIGHT DEEP SLEEP] Sports slates idle. Hibernating 30 mins to preserve $5 credit...")
                gc.collect()
                time.sleep(1800)
                continue

            # -------------------------------------------------------------
            # MODE E: DAYTIME INTER-WINDOW IDLE (11:30 AM - 1:30 PM PT)
            # -------------------------------------------------------------
            logger.info("☕ [INTER-WINDOW STANDBY] Waiting for afternoon sharp consensus window...")
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
