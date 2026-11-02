"""
Discord Webhook Notifier for PrizePicks +EV Engine
--------------------------------------------------
Formats and sends rich, aesthetic Discord embeds for:
1. New paper slips placed by the automated bot.
2. Nightly settlement results and P&L recaps.
3. Performance dashboards.
"""

import datetime
from typing import Any, Dict, List, Optional
import requests

DISCORD_WEBHOOK_URL = (
    "https://discord.com/api/webhooks/1565586149504712724/"
    "Uja5Yqm8hALt6l2hs1H-3VIc_4Q-XDY90iycgPzefKCkOIQQJfo4PaSpuS7FaOWui2fd"
)

COLOR_WIN = 0x2ECC71      # Emerald Green
COLOR_LOSS = 0xE74C3C     # Red
COLOR_PENDING = 0x3498DB  # Blue
COLOR_SUMMARY = 0x9B59B6  # Purple
COLOR_PUSH = 0xF1C40F     # Gold


def post_to_discord(embeds: List[Dict[str, Any]], content: Optional[str] = None) -> bool:
    if not DISCORD_WEBHOOK_URL:
        return False
    payload = {
        "username": "PrizePicks +EV Engine",
        "avatar_url": "https://i.imgur.com/8Q7Zg7M.png",
        "embeds": embeds,
    }
    if content:
        payload["content"] = content

    try:
        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        return r.status_code in [200, 204]
    except Exception as e:
        print(f"Failed to post to Discord: {e}")
        return False


def notify_cloudflare_blocked(platform: str, status_code: int, details: Optional[str] = None) -> bool:
    """
    Sends an urgent high-visibility Discord alert when Cloudflare, DataDome,
    or a 403 Forbidden / 429 Rate Limit occurs on scrapers.
    """
    embed = {
        "title": f"🚨 BOT/RATE LIMIT DETECTED — {platform} ({status_code})",
        "description": (
            f"**Platform:** `{platform}`\n"
            f"**HTTP Status:** `{status_code}`\n"
            f"**Impact:** Scraper IP was flagged or rate-limited by Cloudflare / DataDome.\n\n"
            f"**Action Required:**\n"
            f"• Cooldown requests or route via residential proxy.\n"
            f"• Bot will temporarily fall back to cached boards to prevent repeated bans."
        ),
        "color": COLOR_LOSS,
        "fields": [
            {
                "name": "Error Details",
                "value": f"```{details[:900]}```" if details else "Cloudflare challenge or 403 Forbidden.",
                "inline": False,
            }
        ],
        "footer": {"text": "PrizePicks Engine Alert • IP & WAF Monitor"},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return post_to_discord([embed], content="@everyone ⚠️ **PrizePicks / FanDuel Scraper Blocked**")


def notify_error_alert(title: str, message: str, error_details: Optional[str] = None) -> bool:
    """
    Dispatches a Discord notification whenever an unhandled exception or workflow issue arises.
    """
    embed = {
        "title": f"⚠️ WORKFLOW ISSUE: {title}",
        "description": message,
        "color": COLOR_LOSS,
        "fields": [
            {
                "name": "Exception Details",
                "value": f"```{error_details[:900]}```" if error_details else "No additional traceback.",
                "inline": False,
            }
        ] if error_details else [],
        "footer": {"text": "PrizePicks +EV Engine • Live Exception Tracker"},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return post_to_discord([embed])



def notify_new_slip_placed(slip: Dict[str, Any], avg_win_pct: float):
    legs = slip.get("legs", [])
    leg_lines = []
    for idx, l in enumerate(legs, 1):
        icon = "📈" if l.get("choice") == "OVER" else "📉"
        leg_lines.append(
            f"**{idx}. {l.get('player_name')}** ({l.get('sport')})\n"
            f"   ↳ {l.get('stat_type')} {icon} **{l.get('choice')} {l.get('line')}** "
            f"*(Win: {l.get('edge_percent', 0.0):.1f}%)*\n"
            f"   ↳ *{l.get('game')}*"
        )

    embed = {
        "title": f"🚀 NEW PAPER SLIP PLACED — #{slip.get('id')}",
        "description": (
            f"**Slip Type:** `{slip.get('slip_type')}` | **Stake:** `${slip.get('stake', 10.0):.2f}`\n"
            f"**Lineup Avg Win Rate:** `{avg_win_pct:.2f}%` 🔥 *(Hurdle: 55.09%)*\n"
            f"**Expected ROI:** `~+30.0%`"
        ),
        "color": COLOR_PENDING,
        "fields": [
            {
                "name": "📋 Lineup Selections (6 Legs)",
                "value": "\n".join(leg_lines),
                "inline": False,
            },
            {
                "name": "⚡ Quick Telemetry Link",
                "value": "📊 [**Tap here to view Model Accuracy & Calibration**](https://prizepicks-bot.onrender.com/stats)",
                "inline": False,
            },
        ],
        "footer": {
            "text": f"Status: PENDING • Placed on {slip.get('date_placed', datetime.datetime.now().strftime('%Y-%m-%d'))}"
        },
    }
    # Send Discord text embed
    post_to_discord([embed])

    # Also render and upload visual locked-in slip card
    msg_id = None
    try:
        import slip_card_generator
        import player_image_registry
        reg = player_image_registry.build_or_update_image_registry()
        p_imgs = {l.get("player_name"): reg.get(l.get("player_name")) for l in slip.get("legs", [])}
        msg_id = slip_card_generator.send_slip_card_to_discord(slip, p_imgs, DISCORD_WEBHOOK_URL)
        if msg_id and msg_id != "OK":
            slip["discord_message_id"] = msg_id
            import paper_tracker
            db = paper_tracker.load_database()
            for s in db.get("slips", []):
                if s.get("id") == slip.get("id"):
                    s["discord_message_id"] = msg_id
            paper_tracker.save_database(db)
    except Exception as e:
        print(f"Failed to send locked-in slip card image: {e}")

    return True


def notify_settlement_recap(slip: Dict[str, Any], dashboard_summary: Dict[str, Any]):
    status = slip.get("status", "PENDING")
    payout = slip.get("return_payout", 0.0)
    net = slip.get("net_profit", 0.0)
    stake = slip.get("stake", 10.0)
    net_str = f"+${net:.2f}" if net > 0 else (f"-${abs(net):.2f}" if net < 0 else "$0.00")

    if status == "WON":
        color = COLOR_WIN
        status_text = f"🏆 **SLIP WON ({net_str})**"
    elif status == "LOST":
        color = COLOR_LOSS
        if payout > 0:
            status_text = f"❌ **PARTIAL CASH / LOST ({net_str})**"
        else:
            status_text = f"❌ **SLIP LOST (-${stake:.2f})**"
    else:
        color = COLOR_PUSH
        status_text = f"🛡️ **SLIP PUSHED ($0.00)**"

    hits = []
    pushes = []
    misses = []

    for l in slip.get("legs", []):
        l_stat = l.get("status")
        choice = l.get("choice")
        actual = l.get("actual_result")
        text = f"• **{l.get('player_name')}**: {choice} {l.get('line')} {l.get('stat_type')} ➔ **Actual: {actual}**"

        if l_stat == "HIT":
            hits.append(text)
        elif l_stat == "PUSH":
            pushes.append(text)
        else:
            misses.append(text)

    fields = []
    if hits:
        fields.append({"name": f"✅ Winning Legs ({len(hits)})", "value": "\n".join(hits), "inline": False})
    if pushes:
        fields.append({"name": f"🛡️ Push Protected Legs ({len(pushes)})", "value": "\n".join(pushes), "inline": False})
    if misses:
        fields.append({"name": f"❌ Missed Legs ({len(misses)})", "value": "\n".join(misses), "inline": False})

    slip_embed = {
        "title": f"📋 SLIP SETTLED — #{slip.get('id')} ({slip.get('slip_type')})",
        "description": (
            f"{status_text}\n"
            f"**Stake:** `${stake:.2f}` | **Payout:** `${payout:.2f}` | **Net Profit:** `{net_str}`"
        ),
        "color": color,
        "fields": fields,
        "footer": {"text": "Official ESPN Box Scores Verified"},
    }

    dash_net = dashboard_summary.get("net_profit", 0.0)
    dash_net_str = f"+${dash_net:.2f}" if dash_net > 0 else (f"-${abs(dash_net):.2f}" if dash_net < 0 else "$0.00")
    dash_embed = {
        "title": "📊 OVERALL PERFORMANCE DASHBOARD",
        "color": COLOR_SUMMARY,
        "fields": [
            {
                "name": "💰 Bankroll Summary",
                "value": (
                    f"**Total Staked:** `${dashboard_summary.get('total_staked', 0.0):.2f}`\n"
                    f"**Total Returned:** `${dashboard_summary.get('total_returned', 0.0):.2f}`\n"
                    f"**Net Profit:** `{dash_net_str}`\n"
                    f"**Strategy ROI:** `{dashboard_summary.get('roi_percent', 0.0):+.1f}%`"
                ),
                "inline": True,
            },
            {
                "name": "🎯 Accuracy & Hit Rates",
                "value": (
                    f"**Slip Record:** {dashboard_summary.get('won_slips', 0)}W - {dashboard_summary.get('lost_slips', 0)}L\n"
                    f"**Decided Legs:** {dashboard_summary.get('hit_legs', 0)} Hits / {dashboard_summary.get('missed_legs', 0)} Misses\n"
                    f"**Leg Win Rate:** `{dashboard_summary.get('leg_hit_rate', 0.0):.1f}%`\n"
                    f"*(Hurdle: 55.09%)*"
                ),
                "inline": True,
            },
            {
                "name": "⚡ On-Demand Calibration Telemetry",
                "value": "🔗 [**Tap here anytime to push Live Model Calibration to Discord**](https://prizepicks-bot.onrender.com/stats)",
                "inline": False,
            },
        ],
        "footer": {"text": "PrizePicks +EV Engine • Nightly Settle"},
    }

    # Post text embeds
    post_to_discord([dash_embed, slip_embed])

    # Also render and upload visual PrizePicks app card
    try:
        import slip_card_generator
        import player_image_registry
        reg = player_image_registry.build_or_update_image_registry()
        p_imgs = {l.get("player_name"): reg.get(l.get("player_name")) for l in slip.get("legs", [])}
        message_id = slip.get("discord_message_id")
        slip_card_generator.send_slip_card_to_discord(slip, p_imgs, DISCORD_WEBHOOK_URL, message_id=message_id)
    except Exception as e:
        print(f"Failed to send visual card image: {e}")

    return True


def notify_model_calibration(cal: Dict[str, Any], report: Dict[str, Any]) -> bool:
    """
    Sends a rich, aesthetic Discord embed breaking down model probability calibration,
    actual vs predicted win rates, Brier score, and confidence tiers.
    """
    if not cal or cal.get("total_legs", 0) == 0:
        return False

    delta_sign = "+" if cal.get("calibration_delta", 0.0) >= 0 else ""
    delta_val = cal.get("calibration_delta", 0.0)
    delta_comment = "Conservative (Outperforming Model)" if delta_val >= 0 else "Overestimating"

    fields = [
        {
            "name": "📊 Core Accuracy Metrics",
            "value": (
                f"**Empirical Hit Rate:** `{cal.get('actual_hit_rate', 0.0):.1f}%` ({report.get('total_legs_hit', 0)}/{cal.get('total_legs', 0)} legs)\n"
                f"**Model Predicted Win %:** `{cal.get('avg_predicted_win_rate', 0.0):.1f}%`\n"
                f"**Calibration Delta:** `{delta_sign}{delta_val:.1f}%` ({delta_comment})\n"
                f"**Brier Score:** `{cal.get('brier_score', 0.0):.4f}` *(<0.2400 is sharp edge)*"
            ),
            "inline": False,
        }
    ]

    # Add Confidence Tiers
    tiers = cal.get("reliability_tiers", {})
    if tiers:
        tier_lines = []
        for tier, t_data in tiers.items():
            t_sign = "+" if t_data.get("delta", 0.0) >= 0 else ""
            tier_lines.append(
                f"• **{tier}**: {t_data.get('count')} legs | Pred: `{t_data.get('predicted_win_rate'):.1f}%` | Actual: `{t_data.get('actual_hit_rate'):.1f}%` ({t_sign}{t_data.get('delta'):.1f}%)"
            )
        fields.append({
            "name": "🎯 Confidence Tiers (Reliability Curve)",
            "value": "\n".join(tier_lines),
            "inline": False,
        })

    # Add Prop Type Breakdown
    stat_types = cal.get("stat_types", {})
    if stat_types:
        st_lines = []
        for st, s_data in stat_types.items():
            st_lines.append(f"• **{st}** ({s_data.get('count')} legs): `{s_data.get('actual_hit_rate'):.1f}%` hit rate (Pred: `{s_data.get('predicted_win_rate'):.1f}%`)")
        fields.append({
            "name": "🏀 Stat Type Accuracy",
            "value": "\n".join(st_lines),
            "inline": True,
        })

    # Add Choice Breakdown (OVER vs UNDER)
    choices = cal.get("choices", {})
    if choices:
        ch_lines = []
        for ch, c_data in choices.items():
            ch_lines.append(f"• **{ch}** ({c_data.get('count')} legs): `{c_data.get('actual_hit_rate'):.1f}%` hit rate (Pred: `{c_data.get('predicted_win_rate'):.1f}%`)")
        fields.append({
            "name": "⚖️ Pick Direction",
            "value": "\n".join(ch_lines),
            "inline": True,
        })

    fields.append({
        "name": "⚡ On-Demand Access",
        "value": "🔄 [**Tap here to re-run / refresh this report anytime**](https://prizepicks-bot.onrender.com/stats)",
        "inline": False,
    })

    embed = {
        "title": "🎯 MODEL PROBABILITY CALIBRATION & ACCURACY REPORT",
        "description": (
            "Empirical verification comparing the mathematical model's pre-game win probabilities "
            "against actual ground-truth box score outcomes."
        ),
        "color": COLOR_SUMMARY,
        "fields": fields,
        "footer": {"text": f"PrizePicks +EV Model Calibration • Evaluated {cal.get('total_legs', 0)} Settled Legs"},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    return post_to_discord([embed], content="🔔 **Live Model Calibration Report Requested**")


def send_telemetry_hub_message() -> bool:
    """
    Sends a permanent Quick-Access Telemetry Hub embed to Discord with clickable markdown links
    that the user can tap from mobile or desktop anytime.
    """
    embed = {
        "title": "🎛️ PRIZEPICKS +EV ENGINE — TELEMETRY & CONTROL HUB",
        "description": (
            "Tap any link below directly inside Discord to access real-time cloud data, "
            "trigger on-demand reports, or view your algorithm's accuracy.\n\n"
            "📌 *Tip: Pin this message to the top of this channel for 1-tap access anytime!*"
        ),
        "color": 0x3498DB,
        "fields": [
            {
                "name": "🎯 Model Calibration & Accuracy",
                "value": "📊 [**Tap to push Live Model Calibration to Discord**](https://prizepicks-bot.onrender.com/stats)",
                "inline": False,
            },
            {
                "name": "🌐 Cloud Server Health & Schedule",
                "value": "🟢 [**Tap to view Live Server Health & RAM Telemetry**](https://prizepicks-bot.onrender.com/)",
                "inline": False,
            },
            {
                "name": "📁 Paper Database Export",
                "value": "💾 [**Tap to download / view raw `paper_bets.json`**](https://prizepicks-bot.onrender.com/db)",
                "inline": False,
            },
            {
                "name": "🧪 Trigger Manual Board Scan",
                "value": "🔍 [**Tap to run an instant +EV board scan**](https://prizepicks-bot.onrender.com/test-scan)",
                "inline": False,
            },
        ],
        "footer": {"text": "PrizePicks +EV Engine • Quick Links"},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    return post_to_discord([embed], content="📌 **PrizePicks Engine Control & Telemetry Hub**")
