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
            }
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

    if status == "WON":
        color = COLOR_WIN
        status_text = f"🏆 **SLIP WON (+${net:.2f})**"
    elif status == "LOST":
        color = COLOR_LOSS
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
            f"**Stake:** `${stake:.2f}` | **Payout:** `${payout:.2f}` | **Net Profit:** `+${net:.2f}`"
        ),
        "color": color,
        "fields": fields,
        "footer": {"text": "Official ESPN Box Scores Verified"},
    }

    dash_embed = {
        "title": "📊 OVERALL PERFORMANCE DASHBOARD",
        "color": COLOR_SUMMARY,
        "fields": [
            {
                "name": "💰 Bankroll Summary",
                "value": (
                    f"**Total Staked:** `${dashboard_summary.get('total_staked', 0.0):.2f}`\n"
                    f"**Total Returned:** `${dashboard_summary.get('total_returned', 0.0):.2f}`\n"
                    f"**Net Profit:** `+${dashboard_summary.get('net_profit', 0.0):.2f}`\n"
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
