import requests

webhook_url = "https://discord.com/api/webhooks/1565586149504712724/Uja5Yqm8hALt6l2hs1H-3VIc_4Q-XDY90iycgPzefKCkOIQQJfo4PaSpuS7FaOWui2fd"

embed_summary = {
    "title": "📊 PRIZEPICKS +EV BOT — RECALIBRATED DASHBOARD",
    "description": "Updated with **official app payout multipliers** (Flex 3: 3.0X / 0.75X | Flex 5: 10X / 2X / 0.4X | Flex 6: 25X / 2X / 0.4X).",
    "color": 0x9B59B6,
    "fields": [
        {
            "name": "💰 Cumulative Bankroll",
            "value": "**Total Staked:** $20.00\n**Total Returned:** $30.00\n**Net Profit:** `+$10.00` 🟢\n**Strategy ROI:** `+50.0%` 🔥",
            "inline": True
        },
        {
            "name": "🎯 Accuracy & Record",
            "value": "**Slip Record:** 1 - 1 (50.0%)\n**Decided Legs:** 6 Hits / 3 Misses\n**Leg Hit Rate:** `66.67%` 🎯\n*(PrizePicks Hurdle: 54.21%)*",
            "inline": True
        }
    ],
    "footer": {"text": "Autonomous Sports Betting Engine • Bankroll Synced"}
}

embed_slip2 = {
    "title": "🏆 Slip #5842aae6 — Updated Payout Recap (Oct 29, 2026)",
    "description": "**Type:** 6-Pick Flex ➔ Downgraded to 3-Pick Flex (3 Pushes)\n**Stake:** $10.00 | **Payout:** **$30.00 (3.0X)** | **Net Profit:** `+$20.00` 🟢",
    "color": 0x2ECC71,
    "fields": [
        {
            "name": "✅ Winning Legs (3 Hits - 100% on Decided)",
            "value": "• **Ty Jerome** (POR): U 6.0 Ast ➔ **3.0 Ast** (Hit)\n• **Donovan Clingan** (POR): U 12.5 Pts+Ast ➔ **12.0** (Hit)\n• **Cedric Coward** (POR): O 12.5 Pts ➔ **17.0 Pts** (Hit)",
            "inline": False
        },
        {
            "name": "🛡️ Push Protected Legs (3 Pushes - Zero Losses)",
            "value": "• **De'Andre Hunter** (NOP): U 5.0 Reb ➔ **5.0 Reb** (Push)\n• **Dyson Daniels** (ATL): U 6.0 Reb ➔ **6.0 Reb** (Push)\n• **Jrue Holiday** (POR): U 4.0 Ast ➔ **4.0 Ast** (Push)",
            "inline": False
        }
    ],
    "footer": {"text": "Official 3-Flex Payout: 3/3 pays 3.0X (+$20.00 Net)"}
}

payload = {
    "username": "PrizePicks +EV Engine",
    "avatar_url": "https://i.imgur.com/8Q7Zg7M.png",
    "embeds": [embed_summary, embed_slip2]
}

r = requests.post(webhook_url, json=payload, headers={"Content-Type": "application/json"}, timeout=10)
print("Discord Webhook Status:", r.status_code)
