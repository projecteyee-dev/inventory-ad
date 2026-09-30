import os
import time
import json
import threading

from flask import Flask, request, jsonify
import requests


# ============================================================
# KYOSH ANIME DICE MONITOR
# Complete Flask application
# ============================================================

app = Flask(__name__)

# ============================================================
# CONFIG
# ============================================================

API_KEY = os.environ.get("API_KEY", "CHANGE_THIS_SECRET_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "30"))
DISCORD_UPDATE_INTERVAL = int(os.environ.get("DISCORD_UPDATE_INTERVAL", "5"))

ACCOUNTS_FILE = "accounts.json"
MESSAGE_FILE = "discord_message.json"


# ============================================================
# GLOBAL STATE
# ============================================================

accounts = {}
state_lock = threading.Lock()
discord_message_id = None


# ============================================================
# FILE HELPERS
# ============================================================

def load_accounts():
    global accounts

    if not os.path.exists(ACCOUNTS_FILE):
        accounts = {}
        return

    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict):
            accounts = data
        else:
            accounts = {}

    except Exception as error:
        print("[KYOSH] Failed to load accounts:", error)
        accounts = {}


def save_accounts():
    try:
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as file:
            json.dump(accounts, file, indent=2)

    except Exception as error:
        print("[KYOSH] Failed to save accounts:", error)


def load_discord_message():
    global discord_message_id

    if not os.path.exists(MESSAGE_FILE):
        discord_message_id = None
        return

    try:
        with open(MESSAGE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict):
            discord_message_id = data.get("message_id")
        else:
            discord_message_id = None

    except Exception as error:
        print("[KYOSH] Failed to load Discord message:", error)
        discord_message_id = None


def save_discord_message():
    try:
        with open(MESSAGE_FILE, "w", encoding="utf-8") as file:
            json.dump(
                {
                    "message_id": discord_message_id
                },
                file,
                indent=2
            )

    except Exception as error:
        print("[KYOSH] Failed to save Discord message:", error)


# ============================================================
# AUTH
# ============================================================

def check_key():
    provided_key = request.headers.get("X-API-Key")

    if not provided_key:
        provided_key = request.args.get("key")

    return provided_key == API_KEY


# ============================================================
# ACCOUNT HELPERS
# ============================================================

def account_is_online(account):
    if not isinstance(account, dict):
        return False

    heartbeat = account.get("last_heartbeat", 0)

    try:
        heartbeat = float(heartbeat)
    except (TypeError, ValueError):
        return False

    return (time.time() - heartbeat) <= HEARTBEAT_TIMEOUT


def format_age(timestamp):
    if not timestamp:
        return "Never"

    try:
        age = max(0, int(time.time() - float(timestamp)))
    except (TypeError, ValueError):
        return "Unknown"

    if age < 60:
        return f"{age}s ago"

    if age < 3600:
        return f"{age // 60}m ago"

    if age < 86400:
        return f"{age // 3600}h ago"

    return f"{age // 86400}d ago"


# ============================================================
# INVENTORY CLEANING
# ============================================================

def clean_inventory(inventory):
    """
    Keeps inventory data predictable and prevents huge payloads.

    Expected structure:

    {
        "units": [],
        "gear": [],
        "items": []
    }
    """

    if not isinstance(inventory, dict):
        inventory = {}

    cleaned = {
        "units": [],
        "gear": [],
        "items": []
    }

    for category in cleaned:
        value = inventory.get(category, [])

        if not isinstance(value, list):
            value = []

        cleaned[category] = value[:5000]

    return cleaned


def inventory_counts(inventory):
    inventory = clean_inventory(inventory)

    return {
        "units": len(inventory["units"]),
        "gear": len(inventory["gear"]),
        "items": len(inventory["items"])
    }


# ============================================================
# SNAPSHOT
# ============================================================

def get_accounts_snapshot():
    result = []

    with state_lock:
        for account_id, account in accounts.items():
            if not isinstance(account, dict):
                continue

            account_copy = dict(account)

            inventory = clean_inventory(
                account_copy.get("inventory", {})
            )

            account_copy["inventory"] = inventory
            account_copy["inventory_counts"] = inventory_counts(
                inventory
            )

            account_copy["online"] = account_is_online(
                account_copy
            )

            account_copy["age"] = format_age(
                account_copy.get("last_heartbeat")
            )

            account_copy["account_id"] = account_id

            result.append(account_copy)

    result.sort(
        key=lambda item: (
            not item.get("online", False),
            str(item.get("username", "")).lower()
        )
    )

    return result


def dashboard_stats():
    snapshot = get_accounts_snapshot()

    total = len(snapshot)
    online = sum(
        1 for account in snapshot
        if account.get("online")
    )

    offline = total - online

    units = 0
    gear = 0
    items = 0

    for account in snapshot:
        counts = account.get("inventory_counts", {})

        units += int(counts.get("units", 0) or 0)
        gear += int(counts.get("gear", 0) or 0)
        items += int(counts.get("items", 0) or 0)

    return {
        "total": total,
        "online": online,
        "offline": offline,
        "units": units,
        "gear": gear,
        "items": items
    }


# ============================================================
# DISCORD
# ============================================================

def dashboard_text():
    stats = dashboard_stats()

    lines = [
        "🎲 **KYOSH // ANIME DICE MONITOR**",
        "",
        f"🟢 Online: **{stats['online']}**",
        f"🔴 Offline: **{stats['offline']}**",
        f"👥 Accounts: **{stats['total']}**",
        "",
        "📦 **Inventory**",
        f"⚔️ Units: **{stats['units']}**",
        f"🛡️ Gear: **{stats['gear']}**",
        f"🎁 Items: **{stats['items']}**",
        "",
        f"⏱️ Updated: <t:{int(time.time())}:R>"
    ]

    return "\n".join(lines)


def discord_payload():
    stats = dashboard_stats()

    return {
        "embeds": [
            {
                "title": "🎲 KYOSH // ANIME DICE MONITOR",
                "description": "Live account and inventory monitor.",
                "color": 0x6C5CE7,
                "fields": [
                    {
                        "name": "🟢 Online",
                        "value": str(stats["online"]),
                        "inline": True
                    },
                    {
                        "name": "🔴 Offline",
                        "value": str(stats["offline"]),
                        "inline": True
                    },
                    {
                        "name": "👥 Accounts",
                        "value": str(stats["total"]),
                        "inline": True
                    },
                    {
                        "name": "⚔️ Units",
                        "value": str(stats["units"]),
                        "inline": True
                    },
                    {
                        "name": "🛡️ Gear",
                        "value": str(stats["gear"]),
                        "inline": True
                    },
                    {
                        "name": "🎁 Items",
                        "value": str(stats["items"]),
                        "inline": True
                    }
                ],
                "footer": {
                    "text": "KYOSH Anime Dice Monitor"
                },
                "timestamp": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime()
                )
            }
        ]
    }


def update_discord():
    global discord_message_id

    if not DISCORD_WEBHOOK_URL:
        return

    payload = discord_payload()

    try:
        if discord_message_id:
            edit_url = (
                f"{DISCORD_WEBHOOK_URL}"
                f"/messages/{discord_message_id}"
            )

            response = requests.patch(
                edit_url,
                json=payload,
                timeout=10
            )

            if response.status_code in (200, 204):
                return

            discord_message_id = None
            save_discord_message()

        response = requests.post(
            DISCORD_WEBHOOK_URL,
            params={"wait": "true"},
            json=payload,
            timeout=10
        )

        if response.status_code in (200, 201):
            try:
                data = response.json()
                discord_message_id = data.get("id")
                save_discord_message()
            except Exception:
                pass

        else:
            print(
                "[KYOSH] Discord update failed:",
                response.status_code,
                response.text[:500]
            )

    except Exception as error:
        print("[KYOSH] Discord error:", error)


def monitor_loop():
    while True:
        try:
            update_discord()
        except Exception as error:
            print("[KYOSH] Monitor error:", error)

        time.sleep(DISCORD_UPDATE_INTERVAL)


# ============================================================
# HTML DASHBOARD
# ============================================================

HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta
    name="viewport"
    content="width=device-width, initial-scale=1.0"
>

<title>KYOSH // Anime Dice Monitor</title>

<style>

* {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

:root {
    --bg: #050611;
    --panel: rgba(12, 15, 35, 0.78);
    --panel-2: rgba(20, 23, 50, 0.68);
    --border: rgba(130, 105, 255, 0.24);

    --purple: #8b5cf6;
    --blue: #38bdf8;
    --cyan: #22d3ee;
    --green: #4ade80;
    --red: #fb7185;
    --yellow: #facc15;

    --text: #f5f7ff;
    --muted: #8991ae;
}

html,
body {
    width: 100%;
    min-height: 100%;
}

body {
    font-family:
        Inter,
        ui-sans-serif,
        system-ui,
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        sans-serif;

    background:
        radial-gradient(
            circle at 15% 10%,
            rgba(124, 58, 237, 0.22),
            transparent 30%
        ),
        radial-gradient(
            circle at 85% 20%,
            rgba(14, 165, 233, 0.16),
            transparent 28%
        ),
        radial-gradient(
            circle at 50% 100%,
            rgba(34, 211, 238, 0.10),
            transparent 35%
        ),
        #050611;

    color: var(--text);
    overflow-x: hidden;
}

/* ============================================================
   ANIMATED BACKGROUND
   ============================================================ */

.background {
    position: fixed;
    inset: 0;
    z-index: -10;
    overflow: hidden;
    pointer-events: none;
}

.background::before {
    content: "";
    position: absolute;
    inset: -50%;
    background:
        linear-gradient(
            115deg,
            transparent 40%,
            rgba(139, 92, 246, 0.06) 50%,
            transparent 60%
        );
    animation: backgroundMove 16s linear infinite;
}

.background::after {
    content: "";
    position: absolute;
    inset: 0;
    background-image:
        linear-gradient(
            rgba(255,255,255,0.018) 1px,
            transparent 1px
        ),
        linear-gradient(
            90deg,
            rgba(255,255,255,0.018) 1px,
            transparent 1px
        );
    background-size: 70px 70px;
    mask-image: linear-gradient(
        to bottom,
        transparent,
        black 20%,
        black 80%,
        transparent
    );
}

@keyframes backgroundMove {
    from {
        transform: rotate(0deg);
    }

    to {
        transform: rotate(360deg);
    }
}

.orb {
    position: absolute;
    border-radius: 50%;
    filter: blur(70px);
    opacity: 0.35;
    animation: floatOrb 10s ease-in-out infinite;
}

.orb.one {
    width: 260px;
    height: 260px;
    background: #7c3aed;
    top: 5%;
    left: -80px;
}

.orb.two {
    width: 230px;
    height: 230px;
    background: #0284c7;
    right: -70px;
    top: 30%;
    animation-delay: -3s;
}

.orb.three {
    width: 300px;
    height: 300px;
    background: #16a34a;
    left: 35%;
    bottom: -180px;
    animation-delay: -6s;
}

@keyframes floatOrb {
    0%,
    100% {
        transform: translate(0, 0) scale(1);
    }

    50% {
        transform: translate(40px, -35px) scale(1.15);
    }
}

/* ============================================================
   FLOATING DICE
   ============================================================ */

.dice {
    position: fixed;
    z-index: -5;
    color: rgba(255,255,255,0.045);
    font-size: 100px;
    user-select: none;
    pointer-events: none;
    animation: diceFloat 12s ease-in-out infinite;
}

.dice.d1 {
    left: 4%;
    top: 25%;
    transform: rotate(-15deg);
}

.dice.d2 {
    right: 5%;
    top: 12%;
    font-size: 140px;
    animation-delay: -4s;
}

.dice.d3 {
    left: 45%;
    bottom: 4%;
    font-size: 120px;
    animation-delay: -8s;
}

@keyframes diceFloat {
    0%,
    100% {
        transform: translateY(0) rotate(-10deg);
    }

    50% {
        transform: translateY(-35px) rotate(15deg);
    }
}

/* ============================================================
   HEADER
   ============================================================ */

.header {
    width: min(1400px, calc(100% - 30px));
    margin: 20px auto 0;

    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;

    padding: 18px 22px;

    border: 1px solid var(--border);
    border-radius: 22px;

    background:
        linear-gradient(
            135deg,
            rgba(20, 22, 48, 0.88),
            rgba(8, 10, 25, 0.74)
        );

    backdrop-filter: blur(20px);

    box-shadow:
        0 20px 70px rgba(0, 0, 0, 0.35),
        inset 0 1px rgba(255,255,255,0.05);
}

.brand {
    display: flex;
    align-items: center;
    gap: 14px;
}

.logo {
    width: 48px;
    height: 48px;

    display: flex;
    align-items: center;
    justify-content: center;

    border-radius: 15px;

    font-size: 25px;

    background:
        linear-gradient(
            135deg,
            #7c3aed,
            #2563eb
        );

    box-shadow:
        0 0 25px rgba(124, 58, 237, 0.5);

    transform: rotate(-6deg);
}

.brand-text h1 {
    font-size: 18px;
    letter-spacing: 1.5px;
}

.brand-text p {
    color: var(--muted);
    font-size: 12px;
    margin-top: 3px;
}

.live {
    display: flex;
    align-items: center;
    gap: 8px;

    padding: 9px 13px;

    border-radius: 999px;

    border: 1px solid rgba(74, 222, 128, 0.25);

    background: rgba(74, 222, 128, 0.08);

    color: #86efac;

    font-size: 12px;
    font-weight: 700;
}

.live-dot {
    width: 8px;
    height: 8px;

    border-radius: 50%;

    background: var(--green);

    box-shadow:
        0 0 8px var(--green);

    animation: pulse 1.6s infinite;
}

@keyframes pulse {
    0%,
    100% {
        opacity: 1;
        transform: scale(1);
    }

    50% {
        opacity: 0.45;
        transform: scale(0.75);
    }
}

/* ============================================================
   MAIN
   ============================================================ */

.container {
    width: min(1400px, calc(100% - 30px));
    margin: 24px auto 50px;
}

/* ============================================================
   STATS
   ============================================================ */

.stats {
    display: grid;
    grid-template-columns:
        repeat(6, minmax(0, 1fr));

    gap: 14px;

    margin-bottom: 18px;
}

.stat {
    position: relative;
    overflow: hidden;

    padding: 18px;

    border: 1px solid var(--border);
    border-radius: 18px;

    background:
        linear-gradient(
            145deg,
            rgba(20, 23, 50, 0.84),
            rgba(8, 10, 24, 0.72)
        );

    backdrop-filter: blur(15px);

    box-shadow:
        0 15px 40px rgba(0,0,0,0.2);
}

.stat::after {
    content: "";
    position: absolute;

    width: 80px;
    height: 80px;

    right: -30px;
    bottom: -30px;

    border-radius: 50%;

    background: var(--purple);
    filter: blur(35px);

    opacity: 0.15;
}

.stat-label {
    color: var(--muted);
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
}

.stat-value {
    margin-top: 8px;

    font-size: 28px;
    font-weight: 800;
}

.stat.online .stat-value {
    color: #4ade80;
}

.stat.offline .stat-value {
    color: #fb7185;
}

.stat.units .stat-value {
    color: #a78bfa;
}

.stat.gear .stat-value {
    color: #38bdf8;
}

.stat.items .stat-value {
    color: #facc15;
}

/* ============================================================
   SECTION HEADER
   ============================================================ */

.section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;

    margin: 22px 3px 12px;
}

.section-title {
    font-size: 14px;
    font-weight: 800;
    letter-spacing: 1px;
}

.refresh {
    color: var(--muted);
    font-size: 11px;
}

/* ============================================================
   ACCOUNT GRID
   ============================================================ */

.accounts {
    display: grid;
    grid-template-columns:
        repeat(auto-fill, minmax(300px, 1fr));

    gap: 15px;
}

.account {
    position: relative;
    overflow: hidden;

    border: 1px solid var(--border);
    border-radius: 20px;

    background:
        linear-gradient(
            145deg,
            rgba(19, 22, 48, 0.88),
            rgba(8, 10, 25, 0.78)
        );

    backdrop-filter: blur(16px);

    box-shadow:
        0 20px 50px rgba(0,0,0,0.22);

    transition:
        transform 0.25s ease,
        border-color 0.25s ease,
        box-shadow 0.25s ease;
}

.account:hover {
    transform: translateY(-4px);

    border-color:
        rgba(139, 92, 246, 0.55);

    box-shadow:
        0 25px 70px rgba(0,0,0,0.32),
        0 0 35px rgba(124,58,237,0.08);
}

.account::before {
    content: "";

    position: absolute;
    left: 0;
    top: 0;
    right: 0;

    height: 2px;

    background:
        linear-gradient(
            90deg,
            transparent,
            var(--purple),
            var(--cyan),
            transparent
        );
}

.account-body {
    padding: 18px;
}

.account-top {
    display: flex;
    align-items: center;
    justify-content: space-between;

    gap: 10px;
}

.account-name {
    display: flex;
    align-items: center;
    gap: 11px;
}

.avatar {
    width: 42px;
    height: 42px;

    display: flex;
    align-items: center;
    justify-content: center;

    border-radius: 14px;

    font-weight: 800;

    background:
        linear-gradient(
            135deg,
            rgba(124,58,237,0.35),
            rgba(14,165,233,0.25)
        );

    border:
        1px solid rgba(139,92,246,0.3);
}

.username {
    font-weight: 800;
    font-size: 14px;
}

.account-id {
    color: var(--muted);
    font-size: 10px;
    margin-top: 3px;
}

.status {
    padding: 6px 9px;

    border-radius: 999px;

    font-size: 10px;
    font-weight: 800;

    text-transform: uppercase;
}

.status.online {
    color: #86efac;
    background: rgba(74,222,128,0.1);
    border: 1px solid rgba(74,222,128,0.18);
}

.status.offline {
    color: #fda4af;
    background: rgba(251,113,133,0.1);
    border: 1px solid rgba(251,113,133,0.18);
}

.meta {
    margin-top: 15px;

    display: flex;
    align-items: center;
    justify-content: space-between;

    color: var(--muted);
    font-size: 11px;
}

.inventory-preview {
    display: grid;
    grid-template-columns:
        repeat(3, 1fr);

    gap: 8px;

    margin-top: 14px;
}

.inv-card {
    padding: 11px 8px;

    text-align: center;

    border-radius: 13px;

    background:
        rgba(255,255,255,0.025);

    border:
        1px solid rgba(255,255,255,0.06);
}

.inv-icon {
    font-size: 15px;
}

.inv-number {
    margin-top: 4px;

    font-size: 16px;
    font-weight: 800;
}

.inv-label {
    margin-top: 2px;

    color: var(--muted);

    font-size: 9px;
    text-transform: uppercase;
}

.view-btn {
    width: 100%;

    margin-top: 14px;

    padding: 11px;

    border: 1px solid rgba(139,92,246,0.25);
    border-radius: 12px;

    color: white;

    background:
        linear-gradient(
            135deg,
            rgba(124,58,237,0.18),
            rgba(14,165,233,0.10)
        );

    cursor: pointer;

    font-size: 11px;
    font-weight: 800;

    transition: 0.2s;
}

.view-btn:hover {
    border-color: rgba(139,92,246,0.6);

    background:
        linear-gradient(
            135deg,
            rgba(124,58,237,0.3),
            rgba(14,165,233,0.18)
        );
}

/* ============================================================
   EMPTY
   ============================================================ */

.empty {
    padding: 70px 20px;

    text-align: center;

    border: 1px dashed rgba(139,92,246,0.22);
    border-radius: 20px;

    background: rgba(255,255,255,0.018);
}

.empty-icon {
    font-size: 45px;
    margin-bottom: 12px;
}

.empty-title {
    font-weight: 800;
}

.empty-text {
    color: var(--muted);
    font-size: 12px;
    margin-top: 6px;
}

/* ============================================================
   MODAL
   ============================================================ */

.modal {
    position: fixed;
    inset: 0;

    display: none;
    align-items: center;
    justify-content: center;

    padding: 20px;

    background:
        rgba(2, 3, 12, 0.78);

    backdrop-filter: blur(12px);

    z-index: 100;
}

.modal.show {
    display: flex;
}

.modal-box {
    width: min(1000px, 100%);

    max-height: 90vh;

    display: flex;
    flex-direction: column;

    border:
        1px solid rgba(139,92,246,0.35);

    border-radius: 24px;

    background:
        linear-gradient(
            145deg,
            rgba(18,21,48,0.97),
            rgba(6,8,22,0.97)
        );

    box-shadow:
        0 30px 100px rgba(0,0,0,0.6),
        0 0 60px rgba(124,58,237,0.08);

    overflow: hidden;
}

.modal-head {
    display: flex;
    align-items: center;
    justify-content: space-between;

    padding: 18px 20px;

    border-bottom:
        1px solid rgba(255,255,255,0.06);
}

.modal-title {
    font-weight: 800;
}

.close {
    width: 34px;
    height: 34px;

    border: 0;
    border-radius: 10px;

    color: white;

    background: rgba(255,255,255,0.06);

    cursor: pointer;

    font-size: 18px;
}

.close:hover {
    background: rgba(251,113,133,0.15);
}

.tabs {
    display: flex;
    gap: 8px;

    padding: 14px 20px;

    border-bottom:
        1px solid rgba(255,255,255,0.05);
}

.tab {
    padding: 8px 13px;

    border: 1px solid transparent;
    border-radius: 10px;

    background: transparent;

    color: var(--muted);

    cursor: pointer;

    font-size: 11px;
    font-weight: 700;
}

.tab.active {
    color: white;

    border-color:
        rgba(139,92,246,0.3);

    background:
        rgba(124,58,237,0.15);
}

.modal-content {
    padding: 18px 20px;

    overflow-y: auto;
}

.inventory-grid {
    display: grid;

    grid-template-columns:
        repeat(auto-fill, minmax(160px, 1fr));

    gap: 10px;
}

.item {
    min-height: 90px;

    padding: 13px;

    border:
        1px solid rgba(255,255,255,0.07);

    border-radius: 14px;

    background:
        rgba(255,255,255,0.025);

    transition: 0.2s;
}

.item:hover {
    transform: translateY(-2px);

    border-color:
        rgba(139,92,246,0.35);

    background:
        rgba(124,58,237,0.07);
}

.item-name {
    font-size: 12px;
    font-weight: 800;

    word-break: break-word;
}

.item-detail {
    color: var(--muted);

    font-size: 10px;

    margin-top: 7px;

    line-height: 1.5;
}

.no-items {
    padding: 40px;

    text-align: center;

    color: var(--muted);

    font-size: 12px;
}

/* ============================================================
   RESPONSIVE
   ============================================================ */

@media (max-width: 1100px) {
    .stats {
        grid-template-columns:
            repeat(3, 1fr);
    }
}

@media (max-width: 700px) {
    .header {
        margin-top: 10px;
        padding: 14px;
    }

    .container {
        width: min(100% - 20px, 1400px);
        margin-top: 15px;
    }

    .stats {
        grid-template-columns:
            repeat(2, 1fr);
    }

    .accounts {
        grid-template-columns:
            1fr;
    }

    .brand-text h1 {
        font-size: 14px;
    }

    .live {
        padding: 7px 9px;
    }

    .inventory-grid {
        grid-template-columns:
            repeat(2, 1fr);
    }
}

@media (max-width: 430px) {
    .stats {
        gap: 8px;
    }

    .stat {
        padding: 13px;
    }

    .stat-value {
        font-size: 22px;
    }

    .logo {
        width: 42px;
        height: 42px;
    }

    .live {
        font-size: 9px;
    }
}

</style>
</head>

<body>

<div class="background">
    <div class="orb one"></div>
    <div class="orb two"></div>
    <div class="orb three"></div>

    <div class="dice d1">⚄</div>
    <div class="dice d2">⚅</div>
    <div class="dice d3">⚂</div>
</div>

<header class="header">

    <div class="brand">

        <div class="logo">
            🎲
        </div>

        <div class="brand-text">
            <h1>KYOSH // ANIME DICE</h1>
            <p>Live Account & Inventory Monitor</p>
        </div>

    </div>

    <div class="live">
        <span class="live-dot"></span>
        SYSTEM ONLINE
    </div>

</header>

<main class="container">

    <section class="stats">

        <div class="stat">
            <div class="stat-label">
                Accounts
            </div>

            <div
                class="stat-value"
                id="statTotal"
            >
                0
            </div>
        </div>

        <div class="stat online">
            <div class="stat-label">
                Online
            </div>

            <div
                class="stat-value"
                id="statOnline"
            >
                0
            </div>
        </div>

        <div class="stat offline">
            <div class="stat-label">
                Offline
            </div>

            <div
                class="stat-value"
                id="statOffline"
            >
                0
            </div>
        </div>

        <div class="stat units">
            <div class="stat-label">
                Units
            </div>

            <div
                class="stat-value"
                id="statUnits"
            >
                0
            </div>
        </div>

        <div class="stat gear">
            <div class="stat-label">
                Gear
            </div>

            <div
                class="stat-value"
                id="statGear"
            >
                0
            </div>
        </div>

        <div class="stat items">
            <div class="stat-label">
                Items
            </div>

            <div
                class="stat-value"
                id="statItems"
            >
                0
            </div>
        </div>

    </section>

    <div class="section-header">

        <div class="section-title">
            🎲 MONITORED ACCOUNTS
        </div>

        <div
            class="refresh"
            id="refreshText"
        >
            Updating...
        </div>

    </div>

    <section
        class="accounts"
        id="accounts"
    ></section>

</main>


<!-- ==========================================================
     INVENTORY MODAL
     ========================================================== -->

<div
    class="modal"
    id="inventoryModal"
    onclick="closeModalOutside(event)"
>

    <div
        class="modal-box"
        onclick="event.stopPropagation()"
    >

        <div class="modal-head">

            <div>
                <div
                    class="modal-title"
                    id="modalTitle"
                >
                    Inventory
                </div>
            </div>

            <button
                class="close"
                onclick="closeInventory()"
            >
                ×
            </button>

        </div>

        <div class="tabs">

            <button
                class="tab active"
                data-category="units"
                onclick="switchCategory('units')"
            >
                ⚔️ Units
            </button>

            <button
                class="tab"
                data-category="gear"
                onclick="switchCategory('gear')"
            >
                🛡️ Gear
            </button>

            <button
                class="tab"
                data-category="items"
                onclick="switchCategory('items')"
            >
                🎁 Items
            </button>

        </div>

        <div
            class="modal-content"
            id="modalContent"
        ></div>

    </div>

</div>


<script>

let accountsData = [];
let selectedAccount = null;
let selectedCategory = "units";


function escapeHtml(value) {
    if (value === null || value === undefined) {
        return "";
    }

    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


function getAmount(item) {

    if (!item || typeof item !== "object") {
        return 1;
    }

    const possibleValues = [
        item.amount,
        item.Amount,
        item.count,
        item.Count,
        item.quantity,
        item.Quantity,
        item.qty,
        item.Qty
    ];

    for (const value of possibleValues) {
        const number = Number(value);

        if (Number.isFinite(number)) {
            return number;
        }
    }

    return 1;
}


function getItemName(item) {

    if (typeof item === "string") {
        return item;
    }

    if (!item || typeof item !== "object") {
        return "Unknown";
    }

    const possibleNames = [
        item.name,
        item.Name,
        item.displayName,
        item.DisplayName,
        item.itemName,
        item.ItemName,
        item.unitName,
        item.UnitName,
        item.gearName,
        item.GearName,
        item.title,
        item.Title,
        item.id,
        item.Id
    ];

    for (const value of possibleNames) {
        if (
            value !== undefined &&
            value !== null &&
            String(value).trim() !== ""
        ) {
            return String(value);
        }
    }

    return "Unknown";
}


function getItemDetail(item) {

    if (!item || typeof item !== "object") {
        return "";
    }

    const details = [];

    const rarity = item.rarity ?? item.Rarity;

    if (rarity !== undefined && rarity !== null) {
        details.push("Rarity: " + rarity);
    }

    const level = item.level ?? item.Level;

    if (level !== undefined && level !== null) {
        details.push("Level: " + level);
    }

    const id = item.id ?? item.Id;

    if (
        id !== undefined &&
        id !== null &&
        String(id) !== getItemName(item)
    ) {
        details.push("ID: " + id);
    }

    return details.join(" • ");
}


function renderInventory(category) {

    if (!selectedAccount) {
        return;
    }

    selectedCategory = category;

    const inventory = selectedAccount.inventory || {};

    let items = inventory[category];

    if (!Array.isArray(items)) {
        items = [];
    }

    const content = document.getElementById("modalContent");

    if (items.length === 0) {
        content.innerHTML = `
            <div class="no-items">
                No ${escapeHtml(category)} found.
            </div>
        `;

        return;
    }

    content.innerHTML = `
        <div class="inventory-grid">
            ${items.map((item) => {

                const name = getItemName(item);
                const amount = getAmount(item);
                const detail = getItemDetail(item);

                return `
                    <div class="item">

                        <div class="item-name">
                            ${escapeHtml(name)}
                        </div>

                        <div class="item-detail">
                            Amount: ${escapeHtml(amount)}
                        </div>

                        ${
                            detail
                            ? `
                                <div class="item-detail">
                                    ${escapeHtml(detail)}
                                </div>
                            `
                            : ""
                        }

                    </div>
                `;

            }).join("")}
        </div>
    `;
}


function switchCategory(category) {

    document
        .querySelectorAll(".tab")
        .forEach((tab) => {

            tab.classList.toggle(
                "active",
                tab.dataset.category === category
            );

        });

    renderInventory(category);
}


function openInventory(accountIndex) {

    selectedAccount = accountsData[accountIndex];

    if (!selectedAccount) {
        return;
    }

    const username =
        selectedAccount.username ||
        selectedAccount.name ||
        selectedAccount.account_id ||
        "Account";

    document.getElementById("modalTitle").textContent =
        username + " // Inventory";

    document
        .getElementById("inventoryModal")
        .classList.add("show");

    switchCategory("units");
}


function closeInventory() {

    document
        .getElementById("inventoryModal")
        .classList.remove("show");

    selectedAccount = null;
}


function closeModalOutside(event) {

    if (event.target.id === "inventoryModal") {
        closeInventory();
    }
}


function renderAccounts(data) {

    accountsData = Array.isArray(data)
        ? data
        : [];

    const container =
        document.getElementById("accounts");

    if (accountsData.length === 0) {

        container.innerHTML = `
            <div class="empty">

                <div class="empty-icon">
                    🎲
                </div>

                <div class="empty-title">
                    No accounts detected
                </div>

                <div class="empty-text">
                    Waiting for an account heartbeat...
                </div>

            </div>
        `;

        return;
    }

    container.innerHTML = accountsData
        .map((account, index) => {

            const username =
                account.username ||
                account.name ||
                account.account_id ||
                "Unknown Account";

            const accountId =
                account.account_id ||
                "unknown";

            const online =
                Boolean(account.online);

            const counts =
                account.inventory_counts || {};

            return `
                <article class="account">

                    <div class="account-body">

                        <div class="account-top">

                            <div class="account-name">

                                <div class="avatar">
                                    ${escapeHtml(
                                        username
                                            .charAt(0)
                                            .toUpperCase()
                                    )}
                                </div>

                                <div>

                                    <div class="username">
                                        ${escapeHtml(username)}
                                    </div>

                                    <div class="account-id">
                                        ${escapeHtml(accountId)}
                                    </div>

                                </div>

                            </div>

                            <div
                                class="status ${
                                    online
                                        ? "online"
                                        : "offline"
                                }"
                            >
                                ${
                                    online
                                        ? "ONLINE"
                                        : "OFFLINE"
                                }
                            </div>

                        </div>

                        <div class="meta">

                            <span>
                                Last heartbeat
                            </span>

                            <span>
                                ${escapeHtml(
                                    account.age || "Unknown"
                                )}
                            </span>

                        </div>

                        <div class="inventory-preview">

                            <div class="inv-card">

                                <div class="inv-icon">
                                    ⚔️
                                </div>

                                <div class="inv-number">
                                    ${Number(
                                        counts.units || 0
                                    ).toLocaleString()}
                                </div>

                                <div class="inv-label">
                                    Units
                                </div>

                            </div>

                            <div class="inv-card">

                                <div class="inv-icon">
                                    🛡️
                                </div>

                                <div class="inv-number">
                                    ${Number(
                                        counts.gear || 0
                                    ).toLocaleString()}
                                </div>

                                <div class="inv-label">
                                    Gear
                                </div>

                            </div>

                            <div class="inv-card">

                                <div class="inv-icon">
                                    🎁
                                </div>

                                <div class="inv-number">
                                    ${Number(
                                        counts.items || 0
                                    ).toLocaleString()}
                                </div>

                                <div class="inv-label">
                                    Items
                                </div>

                            </div>

                        </div>

                        <button
                            class="view-btn"
                            onclick="openInventory(${index})"
                        >
                            VIEW INVENTORY
                        </button>

                    </div>

                </article>
            `;

        })
        .join("");
}


function updateStats(stats) {

    if (!stats) {
        return;
    }

    document.getElementById("statTotal").textContent =
        Number(stats.total || 0).toLocaleString();

    document.getElementById("statOnline").textContent =
        Number(stats.online || 0).toLocaleString();

    document.getElementById("statOffline").textContent =
        Number(stats.offline || 0).toLocaleString();

    document.getElementById("statUnits").textContent =
        Number(stats.units || 0).toLocaleString();

    document.getElementById("statGear").textContent =
        Number(stats.gear || 0).toLocaleString();

    document.getElementById("statItems").textContent =
        Number(stats.items || 0).toLocaleString();
}


async function refreshDashboard() {

    try {

        const response =
            await fetch("/api/accounts", {
                cache: "no-store"
            });

        if (!response.ok) {
            throw new Error(
                "HTTP " + response.status
            );
        }

        const data =
            await response.json();

        if (Array.isArray(data)) {

            renderAccounts(data);

            const stats = {
                total: data.length,

                online: data.filter(
                    (account) => account.online
                ).length,

                offline: data.filter(
                    (account) => !account.online
                ).length,

                units: data.reduce(
                    (total, account) =>
                        total +
                        Number(
                            account.inventory_counts?.units || 0
                        ),
                    0
                ),

                gear: data.reduce(
                    (total, account) =>
                        total +
                        Number(
                            account.inventory_counts?.gear || 0
                        ),
                    0
                ),

                items: data.reduce(
                    (total, account) =>
                        total +
                        Number(
                            account.inventory_counts?.items || 0
                        ),
                    0
                )
            };

            updateStats(stats);
        }

        document.getElementById("refreshText").textContent =
            "Updated just now";

    } catch (error) {

        console.error(
            "[KYOSH] Dashboard refresh failed:",
            error
        );

        document.getElementById("refreshText").textContent =
            "Connection error";
    }
}


refreshDashboard();

setInterval(
    refreshDashboard,
    2000
);


document.addEventListener(
    "keydown",
    function(event) {

        if (event.key === "Escape") {
            closeInventory();
        }

    }
);

</script>

</body>
</html>
"""


# ============================================================
# ROUTES
# ============================================================

@app.route("/")
def home():
    return HTML


@app.route("/api/accounts", methods=["GET"])
def api_accounts():

    if not check_key():
        return jsonify(
            {
                "error": "Unauthorized"
            }
        ), 401

    return jsonify(
        get_accounts_snapshot()
    )


@app.route("/health", methods=["GET"])
def health():
    return jsonify(
        {
            "ok": True,
            "service": "KYOSH Anime Dice Monitor",
            "accounts": len(accounts),
            "timestamp": int(time.time())
        }
    )


@app.route("/heartbeat", methods=["POST"])
def heartbeat():

    if not check_key():
        return jsonify(
            {
                "error": "Unauthorized"
            }
        ), 401

    data = request.get_json(
        silent=True
    )

    if not isinstance(data, dict):
        return jsonify(
            {
                "error": "JSON body required"
            }
        ), 400

    account_id = data.get("account_id")

    if account_id is None:
        account_id = data.get("username")

    if account_id is None:
        account_id = data.get("name")

    if account_id is None:
        return jsonify(
            {
                "error": "account_id is required"
            }
        ), 400

    account_id = str(account_id)

    now = time.time()

    with state_lock:

        previous = accounts.get(
            account_id,
            {}
        )

        if not isinstance(previous, dict):
            previous = {}

        account = dict(previous)

        account.update(data)

        account["account_id"] = account_id
        account["last_heartbeat"] = now

        if "inventory" in data:
            account["inventory"] = clean_inventory(
                data.get("inventory")
            )
        else:
            account["inventory"] = clean_inventory(
                account.get("inventory", {})
            )

        accounts[account_id] = account

        save_accounts()

    return jsonify(
        {
            "ok": True,
            "message": "Anime Dice heartbeat received",
            "account_id": account_id,
            "online": True
        }
    )


# ============================================================
# STARTUP
# ============================================================

load_accounts()
load_discord_message()


monitor_thread = threading.Thread(
    target=monitor_loop,
    daemon=True
)

monitor_thread.start()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
