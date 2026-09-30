import os
import time
import json
import threading
from html import escape

from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

API_KEY = os.environ.get("API_KEY", "CHANGE_THIS_SECRET_KEY")
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

HEARTBEAT_TIMEOUT = int(os.environ.get("HEARTBEAT_TIMEOUT", "30"))
DISCORD_UPDATE_INTERVAL = int(os.environ.get("DISCORD_UPDATE_INTERVAL", "5"))

ACCOUNTS_FILE = "accounts.json"
MESSAGE_FILE = "discord_message.json"

accounts = {}
state_lock = threading.Lock()
discord_message_id = None


# ============================================================
# SAVE / LOAD
# ============================================================

def load_accounts():
    global accounts

    if not os.path.exists(ACCOUNTS_FILE):
        print("📁 No previous accounts file found")
        return

    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, dict):
            accounts = data
            print(f"📂 Loaded {len(accounts)} saved account(s)")

    except Exception as error:
        print("❌ Could not load accounts:")
        print(repr(error))


def save_accounts():
    try:
        with state_lock:
            data = accounts.copy()

        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)

    except Exception as error:
        print("❌ Could not save accounts:")
        print(repr(error))


def load_discord_message():
    global discord_message_id

    if not os.path.exists(MESSAGE_FILE):
        return

    try:
        with open(MESSAGE_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)

        discord_message_id = data.get("message_id")

    except Exception as error:
        print("❌ Could not load Discord message ID:")
        print(repr(error))


def save_discord_message():
    try:
        with open(MESSAGE_FILE, "w", encoding="utf-8") as file:
            json.dump({"message_id": discord_message_id}, file)

    except Exception as error:
        print("❌ Could not save Discord message ID:")
        print(repr(error))


# ============================================================
# HELPERS
# ============================================================

def check_key(req):
    return req.headers.get("X-API-Key") == API_KEY


def account_is_online(account):
    last_seen = float(account.get("lastSeen", 0))
    return (time.time() - last_seen) <= HEARTBEAT_TIMEOUT


def format_age(seconds):
    try:
        seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        return "—"

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours:
        return f"{hours}h {minutes:02d}m"

    if minutes:
        return f"{minutes}m {secs:02d}s"

    return f"{secs}s"


def get_accounts_snapshot():
    with state_lock:
        return [dict(account) for account in accounts.values()]


def dashboard_stats():
    items = get_accounts_snapshot()

    online = sum(1 for account in items if account_is_online(account))
    offline = len(items) - online

    return online, offline, len(items)


def clean_inventory(inventory):
    """
    Normalizes the Anime Dice inventory sent by Roblox.

    Expected structure:

    inventory = {
        "units": [...],
        "gear": [...],
        "items": [...]
    }
    """

    if not isinstance(inventory, dict):
        return {
            "units": [],
            "gear": [],
            "items": []
        }

    units = inventory.get("units", [])
    gear = inventory.get("gear", [])
    items = inventory.get("items", [])

    if not isinstance(units, list):
        units = []

    if not isinstance(gear, list):
        gear = []

    if not isinstance(items, list):
        items = []

    return {
        "units": units[:5000],
        "gear": gear[:5000],
        "items": items[:5000]
    }


def inventory_counts(account):
    inventory = clean_inventory(account.get("inventory"))

    return {
        "units": len(inventory["units"]),
        "gear": len(inventory["gear"]),
        "items": len(inventory["items"])
    }


# ============================================================
# DISCORD
# ============================================================

def dashboard_text():
    items = get_accounts_snapshot()

    lines = []
    online = 0
    offline = 0

    for account in sorted(
        items,
        key=lambda x: (x.get("playerName") or "").lower()
    ):
        is_online = account_is_online(account)

        if is_online:
            online += 1
            icon = "🟢 ONLINE"
        else:
            offline += 1
            icon = "🔴 OFFLINE"

        name = account.get("playerName") or account.get("userId")
        counts = inventory_counts(account)

        lines.append(
            f"{icon} **{name}** — "
            f"Units: **{counts['units']}** | "
            f"Gear: **{counts['gear']}** | "
            f"Items: **{counts['items']}**"
        )

    if not lines:
        body = "No accounts have sent a heartbeat yet."
    else:
        body = "\n".join(lines)

    if len(body) > 3900:
        body = body[:3860] + "\n…more accounts not shown"

    return body, online, offline, len(items)


def discord_payload():
    body, online, offline, total = dashboard_text()

    return {
        "embeds": [{
            "title": "🎲 KYOSH ANIME DICE MONITOR",
            "description": body,
            "color": 5763719 if offline == 0 else 15158332,
            "footer": {
                "text": (
                    f"Online: {online} | "
                    f"Offline: {offline} | "
                    f"Total: {total}"
                )
            },
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ",
                time.gmtime()
            )
        }]
    }


def update_discord():
    global discord_message_id

    if not DISCORD_WEBHOOK_URL:
        return

    payload = discord_payload()

    try:
        if discord_message_id:
            url = (
                f"{DISCORD_WEBHOOK_URL}"
                f"/messages/{discord_message_id}"
            )

            response = requests.patch(
                url,
                json=payload,
                timeout=15
            )

            if 200 <= response.status_code < 300:
                return

            if response.status_code == 404:
                discord_message_id = None
                save_discord_message()
            else:
                print("❌ Discord PATCH error:", response.text[:1000])
                return

        url = DISCORD_WEBHOOK_URL + "?wait=true"

        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if 200 <= response.status_code < 300:
            data = response.json()
            discord_message_id = data.get("id")
            save_discord_message()
            print("✅ Discord monitor message created/updated")
        else:
            print("❌ Discord POST error:", response.text[:1000])

    except Exception as error:
        print("❌ Discord connection error:")
        print(repr(error))


def monitor_loop():
    print("🚀 Discord Anime Dice monitor loop started")

    while True:
        try:
            update_discord()
        except Exception as error:
            print("❌ Monitor loop error:", repr(error))

        time.sleep(DISCORD_UPDATE_INTERVAL)

@app.get("/")
def home():
    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KYOSH Anime Dice Monitor</title>

<style>
:root{
    --bg:#070512;
    --bg2:#0d0820;
    --panel:rgba(18,12,38,.88);
    --panel2:rgba(25,16,50,.94);

    --purple:#9d6cff;
    --purple2:#c59cff;
    --pink:#ff65d8;
    --blue:#62cfff;
    --cyan:#72f4ff;
    --gold:#ffd76a;

    --green:#69f6a1;
    --red:#ff5d7a;

    --text:#f8f4ff;
    --muted:#928aa8;

    --line:rgba(157,108,255,.20);
    --line2:rgba(197,156,255,.40);
}

*{
    box-sizing:border-box;
}

html{
    background:#070512;
}

body{
    margin:0;
    min-height:100vh;
    color:var(--text);
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
            rgba(157,108,255,.20),
            transparent 28%
        ),
        radial-gradient(
            circle at 85% 15%,
            rgba(255,101,216,.14),
            transparent 25%
        ),
        radial-gradient(
            circle at 50% 80%,
            rgba(98,207,255,.10),
            transparent 32%
        ),
        linear-gradient(
            135deg,
            #05030d 0%,
            #0b0619 45%,
            #100821 100%
        );

    overflow-x:hidden;
}

/* =========================================================
   ANIME ENERGY BACKGROUND
   ========================================================= */

body::before{
    content:"";
    position:fixed;
    inset:0;
    z-index:-5;

    background:
        linear-gradient(
            115deg,
            transparent 0%,
            rgba(157,108,255,.025) 35%,
            rgba(255,101,216,.035) 50%,
            transparent 75%
        );

    pointer-events:none;
}

body::after{
    content:"";
    position:fixed;
    inset:-50%;

    z-index:-4;

    background:
        conic-gradient(
            from 180deg,
            transparent,
            rgba(157,108,255,.045),
            transparent,
            rgba(98,207,255,.035),
            transparent
        );

    animation:backgroundRotate 25s linear infinite;

    pointer-events:none;
}

@keyframes backgroundRotate{
    from{
        transform:rotate(0deg);
    }

    to{
        transform:rotate(360deg);
    }
}

/* =========================================================
   FLOATING DICE
   ========================================================= */

.dice{
    position:fixed;
    z-index:-2;

    width:58px;
    height:58px;

    display:grid;
    place-items:center;

    color:rgba(255,255,255,.13);

    font-size:30px;
    font-weight:900;

    border:1px solid rgba(197,156,255,.13);
    border-radius:14px;

    background:
        linear-gradient(
            145deg,
            rgba(157,108,255,.08),
            rgba(255,101,216,.035)
        );

    box-shadow:
        0 0 25px rgba(157,108,255,.08),
        inset 0 0 20px rgba(255,255,255,.025);

    backdrop-filter:blur(5px);

    pointer-events:none;
}

.dice.one{
    top:15%;
    left:5%;
    transform:rotate(-18deg);
    animation:floatOne 7s ease-in-out infinite;
}

.dice.two{
    top:28%;
    right:5%;
    transform:rotate(17deg);
    animation:floatTwo 9s ease-in-out infinite;
}

.dice.three{
    bottom:20%;
    left:8%;
    transform:rotate(13deg);
    animation:floatThree 8s ease-in-out infinite;
}

.dice.four{
    bottom:10%;
    right:10%;
    transform:rotate(-12deg);
    animation:floatFour 10s ease-in-out infinite;
}

@keyframes floatOne{
    0%,100%{
        transform:translateY(0) rotate(-18deg);
    }

    50%{
        transform:translateY(-25px) rotate(-8deg);
    }
}

@keyframes floatTwo{
    0%,100%{
        transform:translateY(0) rotate(17deg);
    }

    50%{
        transform:translateY(30px) rotate(27deg);
    }
}

@keyframes floatThree{
    0%,100%{
        transform:translateY(0) rotate(13deg);
    }

    50%{
        transform:translateY(-20px) rotate(25deg);
    }
}

@keyframes floatFour{
    0%,100%{
        transform:translateY(0) rotate(-12deg);
    }

    50%{
        transform:translateY(25px) rotate(-25deg);
    }
}

/* =========================================================
   ENERGY PARTICLES
   ========================================================= */

.particles{
    position:fixed;
    inset:0;
    z-index:-3;
    pointer-events:none;
    overflow:hidden;
}

.particle{
    position:absolute;

    width:3px;
    height:3px;

    border-radius:50%;

    background:var(--purple2);

    box-shadow:
        0 0 8px var(--purple),
        0 0 15px rgba(157,108,255,.45);

    animation:
        particleFloat
        var(--duration)
        linear infinite;

    opacity:0;
}

@keyframes particleFloat{
    0%{
        transform:translateY(110vh) scale(.5);
        opacity:0;
    }

    15%{
        opacity:.7;
    }

    80%{
        opacity:.45;
    }

    100%{
        transform:translateY(-10vh) scale(1);
        opacity:0;
    }
}

/* =========================================================
   MAIN
   ========================================================= */

.wrapper{
    position:relative;

    width:min(1450px,94%);

    margin:0 auto;

    padding:
        35px
        0
        55px;
}

.wrapper::before{
    content:"";

    position:absolute;

    left:0;
    right:0;
    top:0;

    height:2px;

    background:
        linear-gradient(
            90deg,
            transparent,
            var(--purple),
            var(--pink),
            var(--blue),
            var(--purple),
            transparent
        );

    box-shadow:
        0 0 15px var(--purple),
        0 0 35px rgba(157,108,255,.35);

    border-radius:999px;
}

/* =========================================================
   HEADER
   ========================================================= */

.header{
    display:flex;

    align-items:center;
    justify-content:space-between;

    gap:20px;

    margin-bottom:25px;

    padding-top:10px;
}

.brand{
    display:flex;

    align-items:center;

    gap:15px;
}

.logo{
    position:relative;

    width:58px;
    height:58px;

    display:grid;
    place-items:center;

    border-radius:17px;

    background:
        linear-gradient(
            145deg,
            #24134d,
            #130c2c
        );

    border:
        1px solid
        rgba(197,156,255,.65);

    box-shadow:
        0 0 0 4px rgba(157,108,255,.06),
        0 0 20px rgba(157,108,255,.30),
        0 0 45px rgba(255,101,216,.10),
        inset 0 0 20px rgba(157,108,255,.10);

    transform:rotate(-5deg);

    color:white;

    font-size:27px;
}

.logo::before{
    content:"";

    position:absolute;

    inset:-3px;

    border-radius:19px;

    border:
        1px solid
        rgba(255,101,216,.20);

    animation:
        logoGlow
        2.5s ease-in-out
        infinite;
}

.logo::after{
    content:"🎲";

    filter:
        drop-shadow(
            0 0 8px
            rgba(197,156,255,.7)
        );
}

@keyframes logoGlow{
    0%,100%{
        opacity:.3;
    }

    50%{
        opacity:1;
    }
}

h1{
    margin:0;

    font-size:25px;

    letter-spacing:-.7px;

    font-weight:900;

    background:
        linear-gradient(
            90deg,
            #ffffff,
            #cdaeff,
            #ff9ce6,
            #8feaff
        );

    -webkit-background-clip:text;
    background-clip:text;

    color:transparent;

    text-shadow:
        0 0 30px rgba(157,108,255,.18);
}

.subtitle{
    color:var(--muted);

    font-size:13px;

    margin-top:5px;

    letter-spacing:.2px;
}

/* =========================================================
   LIVE
   ========================================================= */

.live{
    display:flex;

    align-items:center;

    gap:9px;

    padding:
        10px
        16px;

    border:
        1px solid
        rgba(105,246,161,.25);

    background:
        rgba(16,29,35,.65);

    border-radius:999px;

    color:#b5c9c0;

    font-size:11px;

    font-weight:800;

    letter-spacing:.6px;

    box-shadow:
        0 0 20px rgba(105,246,161,.05);
}

.live-dot{
    width:8px;
    height:8px;

    border-radius:50%;

    background:var(--green);

    box-shadow:
        0 0 8px var(--green),
        0 0 18px rgba(105,246,161,.55);

    animation:
        pulse
        1.5s
        infinite;
}

/* =========================================================
   STATS
   ========================================================= */

.stats{
    display:grid;

    grid-template-columns:
        repeat(3,1fr);

    gap:14px;

    margin-bottom:18px;
}

.stat{
    position:relative;

    overflow:hidden;

    background:
        linear-gradient(
            145deg,
            rgba(26,16,52,.90),
            rgba(10,7,24,.92)
        );

    border:
        1px solid
        var(--line);

    border-radius:18px;

    padding:
        19px
        21px;

    box-shadow:
        0 18px 50px rgba(0,0,0,.30),
        inset 0 0 25px rgba(157,108,255,.025);

    backdrop-filter:blur(12px);

    transition:
        transform .2s ease,
        border-color .2s ease,
        box-shadow .2s ease;
}

.stat::before{
    content:"";

    position:absolute;

    top:0;
    left:-100%;

    width:100%;
    height:1px;

    background:
        linear-gradient(
            90deg,
            transparent,
            var(--purple2),
            transparent
        );

    animation:
        statScan
        4s
        linear
        infinite;
}

.stat:hover{
    transform:translateY(-3px);

    border-color:
        rgba(197,156,255,.35);

    box-shadow:
        0 22px 55px rgba(0,0,0,.35),
        0 0 25px rgba(157,108,255,.07);
}

@keyframes statScan{
    0%{
        left:-100%;
    }

    50%,100%{
        left:100%;
    }
}

.stat-label{
    color:#817994;

    font-size:10px;

    margin-bottom:7px;

    text-transform:uppercase;

    letter-spacing:1.2px;

    font-weight:900;
}

.stat-value{
    font-size:28px;

    font-weight:900;

    color:#fff;

    text-shadow:
        0 0 20px rgba(157,108,255,.15);
}

/* =========================================================
   TABLE
   ========================================================= */

.table{
    border:
        1px solid
        var(--line);

    background:
        var(--panel);

    border-radius:20px;

    overflow:hidden;

    box-shadow:
        0 30px 90px rgba(0,0,0,.45),
        0 0 40px rgba(157,108,255,.035);

    backdrop-filter:blur(16px);
}

.table-scroll{
    overflow-x:auto;
}

.table-head,
.account{
    display:grid;

    grid-template-columns:
        minmax(210px,2fr)
        120px
        minmax(180px,1.7fr)
        110px;

    align-items:center;

    min-width:760px;
}

.table-head{
    height:52px;

    padding:
        0
        18px;

    color:#827995;

    background:
        linear-gradient(
            180deg,
            rgba(31,19,59,.95),
            rgba(13,8,28,.96)
        );

    border-bottom:
        1px solid
        var(--line);

    font-size:10px;

    font-weight:900;

    text-transform:uppercase;

    letter-spacing:1.1px;
}

.account{
    padding:
        14px
        18px;

    min-height:78px;

    border-bottom:
        1px solid
        rgba(157,108,255,.09);

    transition:
        background .2s ease;
}

.account:last-child{
    border-bottom:0;
}

.account:hover{
    background:
        linear-gradient(
            90deg,
            rgba(157,108,255,.10),
            rgba(255,101,216,.035),
            transparent
        );
}

/* =========================================================
   USER
   ========================================================= */

.user{
    display:flex;

    align-items:center;

    gap:11px;

    min-width:0;
}

.avatar{
    width:39px;
    height:39px;

    flex:0 0 39px;

    display:grid;
    place-items:center;

    border-radius:13px;

    background:
        linear-gradient(
            145deg,
            #3a236b,
            #17102f
        );

    border:
        1px solid
        rgba(197,156,255,.45);

    color:#e9dfff;

    font-size:13px;

    font-weight:900;

    box-shadow:
        0 0 18px rgba(157,108,255,.13),
        inset 0 0 14px rgba(197,156,255,.06);
}

.username{
    overflow:hidden;

    text-overflow:ellipsis;

    white-space:nowrap;

    font-weight:800;

    font-size:14px;
}

.userid{
    color:#625b71;

    font-size:10px;

    margin-top:2px;
}

/* =========================================================
   STATUS
   ========================================================= */

.status{
    display:inline-flex;

    align-items:center;

    gap:7px;

    font-size:12px;

    font-weight:800;
}

.status-dot{
    width:7px;
    height:7px;

    border-radius:50%;
}

.online .status-dot{
    background:var(--green);

    box-shadow:
        0 0 8px var(--green),
        0 0 15px rgba(105,246,161,.40);
}

.online .status{
    color:#9df8c6;
}

.offline .status-dot{
    background:var(--red);

    box-shadow:
        0 0 8px rgba(255,93,122,.40);
}

.offline .status{
    color:#716a78;
}

/* =========================================================
   INVENTORY
   ========================================================= */

.inventory-summary{
    display:flex;

    gap:6px;

    flex-wrap:wrap;
}

.category-pill{
    border:
        1px solid
        rgba(157,108,255,.18);

    background:
        rgba(20,13,40,.80);

    border-radius:999px;

    padding:
        6px
        9px;

    font-size:10px;

    font-weight:900;
}

.category-pill.units{
    color:#c59cff;

    border-color:
        rgba(197,156,255,.22);
}

.category-pill.gear{
    color:#ff91e5;

    border-color:
        rgba(255,101,216,.20);
}

.category-pill.items{
    color:#72dcff;

    border-color:
        rgba(98,207,255,.20);
}

.inventory-btn{
    border:
        1px solid
        rgba(157,108,255,.22);

    background:
        linear-gradient(
            135deg,
            rgba(35,22,67,.85),
            rgba(13,8,28,.92)
        );

    color:#eee7ff;

    border-radius:11px;

    padding:
        9px
        12px;

    cursor:pointer;

    font:inherit;

    transition:
        .18s ease;

    width:100%;
}

.inventory-btn:hover{
    background:
        linear-gradient(
            135deg,
            rgba(61,36,108,.90),
            rgba(20,11,42,.95)
        );

    border-color:
        rgba(197,156,255,.50);

    box-shadow:
        0 0 20px rgba(157,108,255,.09);

    transform:translateY(-1px);
}

.inventory-preview{
    color:#746d82;

    font-size:10px;

    margin-top:4px;

    white-space:nowrap;

    overflow:hidden;

    text-overflow:ellipsis;
}

.age{
    color:#6f687a;

    font-size:12px;
}

/* =========================================================
   MODAL
   ========================================================= */

.modal{
    position:fixed;

    inset:0;

    z-index:1000;

    display:none;

    align-items:center;

    justify-content:center;

    padding:20px;

    background:
        rgba(3,1,10,.82);

    backdrop-filter:
        blur(12px);
}

.modal.open{
    display:flex;
}

.modal-card{
    width:min(900px,96vw);

    max-height:88vh;

    overflow:hidden;

    background:
        linear-gradient(
            145deg,
            #1a1034,
            #090516
        );

    border:
        1px solid
        rgba(197,156,255,.30);

    border-radius:19px;

    box-shadow:
        0 30px 100px rgba(0,0,0,.70),
        0 0 50px rgba(157,108,255,.10);
}

.modal-head{
    display:flex;

    align-items:center;

    justify-content:space-between;

    gap:15px;

    padding:
        17px
        19px;

    border-bottom:
        1px solid
        var(--line);
}

.modal-title{
    font-weight:900;

    font-size:16px;

    background:
        linear-gradient(
            90deg,
            #fff,
            #c59cff,
            #ff9ce6
        );

    -webkit-background-clip:text;
    background-clip:text;

    color:transparent;
}

.modal-subtitle{
    color:var(--muted);

    font-size:11px;

    margin-top:3px;
}

.modal-close{
    border:
        1px solid
        rgba(197,156,255,.20);

    background:
        #17102c;

    color:#e9e0fa;

    border-radius:9px;

    width:35px;
    height:35px;

    cursor:pointer;

    font-size:18px;

    transition:.15s;
}

.modal-close:hover{
    background:#28174b;

    border-color:
        rgba(197,156,255,.45);

    transform:rotate(5deg);
}

.inventory-list{
    max-height:70vh;

    overflow:auto;

    padding:12px;
}

/* =========================================================
   INVENTORY SECTIONS
   ========================================================= */

.section{
    margin-bottom:18px;

    border:
        1px solid
        rgba(157,108,255,.12);

    border-radius:13px;

    overflow:hidden;

    background:
        rgba(7,4,18,.38);
}

.section-title{
    padding:
        12px
        13px;

    font-size:12px;

    font-weight:900;

    letter-spacing:.7px;

    text-transform:uppercase;

    background:
        rgba(28,17,53,.75);

    border-bottom:
        1px solid
        rgba(157,108,255,.10);
}

.section-title.units{
    color:var(--purple2);
}

.section-title.gear{
    color:var(--pink);
}

.section-title.items{
    color:var(--blue);
}

.inventory-item{
    display:grid;

    grid-template-columns:
        minmax(180px,1fr)
        90px
        minmax(150px,1fr);

    gap:12px;

    align-items:center;

    padding:
        11px
        13px;

    border-bottom:
        1px solid
        rgba(157,108,255,.08);
}

.inventory-item:last-child{
    border-bottom:0;
}

.item-name{
    font-weight:900;

    font-size:12px;
}

.item-meta{
    color:#746c80;

    font-size:10px;

    margin-top:3px;
}

.item-amount{
    color:#f3eaff;

    font-size:12px;

    font-weight:900;

    text-align:center;
}

.item-details{
    color:#9b91a8;

    font-size:10px;

    text-align:right;
}

.empty{
    padding:70px 20px;

    text-align:center;

    color:var(--muted);
}

.footer{
    padding:
        14px
        18px;

    color:#625b70;

    font-size:11px;

    border-top:
        1px solid
        var(--line);

    background:
        rgba(7,4,17,.85);
}

@keyframes pulse{
    0%,100%{
        transform:scale(1);
        opacity:1;
    }

    50%{
        transform:scale(1.2);
        opacity:.75;
    }
}

/* =========================================================
   MOBILE
   ========================================================= */

@media(max-width:800px){

    .wrapper{
        width:96%;

        padding-top:20px;
    }

    .stats{
        grid-template-columns:1fr;
    }

    .header{
        align-items:flex-start;
    }

    .live{
        display:none;
    }

    .dice{
        opacity:.45;
        transform:scale(.75);
    }
}

@media(max-width:500px){

    .wrapper{
        width:94%;
    }

    h1{
        font-size:19px;
    }

    .subtitle{
        font-size:11px;
    }

    .logo{
        width:46px;
        height:46px;

        font-size:22px;
    }

    .stat{
        padding:
            15px
            16px;
    }

    .stat-value{
        font-size:24px;
    }

    .dice{
        display:none;
    }
}
</style>
</head>

<body>

<!-- FLOATING DICE -->

<div class="dice one">⚄</div>
<div class="dice two">⚂</div>
<div class="dice three">⚅</div>
<div class="dice four">⚁</div>

<!-- PARTICLES -->

<div class="particles" id="particles"></div>

<div class="wrapper">

    <!-- HEADER -->

    <div class="header">

        <div class="brand">

            <div class="logo"></div>

            <div>

                <h1>
                    KYOSH ANIME DICE MONITOR
                </h1>

                <div class="subtitle">
                    Live Anime Dice account & inventory monitor
                </div>

            </div>

        </div>

        <div class="live">

            <span class="live-dot"></span>

            LIVE MONITOR

        </div>

    </div>


    <!-- STATS -->

    <div class="stats">

        <div class="stat">

            <div class="stat-label">
                ONLINE
            </div>

            <div
                class="stat-value"
                id="online"
            >
                0
            </div>

        </div>


        <div class="stat">

            <div class="stat-label">
                OFFLINE
            </div>

            <div
                class="stat-value"
                id="offline"
            >
                0
            </div>

        </div>


        <div class="stat">

            <div class="stat-label">
                TOTAL ACCOUNTS
            </div>

            <div
                class="stat-value"
                id="total"
            >
                0
            </div>

        </div>

    </div>


    <!-- ACCOUNT TABLE -->

    <div class="table">

        <div class="table-scroll">

            <div class="table-head">

                <div>
                    ACCOUNT
                </div>

                <div>
                    STATUS
                </div>

                <div>
                    INVENTORY
                </div>

                <div>
                    LAST SEEN
                </div>

            </div>


            <div id="accounts">

                <div class="empty">
                    Loading accounts...
                </div>

            </div>

        </div>


        <div class="footer">

            ✦ Auto-refreshing every 2 seconds
            •
            KYOSH Anime Dice Monitor
            ✦

        </div>

    </div>


    <!-- INVENTORY MODAL -->

    <div
        class="modal"
        id="inventoryModal"
    >

        <div
            class="modal-card"
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

                    <div
                        class="modal-subtitle"
                        id="modalSubtitle"
                    ></div>

                </div>


                <button
                    class="modal-close"
                    onclick="closeInventory()"
                >
                    ×
                </button>

            </div>


            <div
                class="inventory-list"
                id="inventoryList"
            ></div>

        </div>

    </div>

</div>


<script>

/* =========================================================
   PARTICLES
   ========================================================= */

const particleContainer =
    document.getElementById("particles");

for(let i = 0; i < 45; i++){

    const particle =
        document.createElement("div");

    particle.className =
        "particle";

    particle.style.left =
        Math.random() * 100 + "%";

    particle.style.setProperty(
        "--duration",
        (8 + Math.random() * 14) + "s"
    );

    particle.style.animationDelay =
        (-Math.random() * 15) + "s";

    particle.style.opacity =
        Math.random() * .7;

    particleContainer.appendChild(
        particle
    );
}


/* =========================================================
   ESCAPE HTML
   ========================================================= */

function esc(value){

    return String(value ?? "")
        .replaceAll("&","&amp;")
        .replaceAll("<","&lt;")
        .replaceAll(">","&gt;")
        .replaceAll('"',"&quot;")
        .replaceAll("'","&#039;");
}


/* =========================================================
   ACCOUNT CACHE
   ========================================================= */

const accountCache = {};


/* =========================================================
   INVENTORY
   ========================================================= */

function getInventory(account){

    const inv =
        account.inventory || {};

    return {

        units:
            Array.isArray(inv.units)
                ? inv.units
                : [],

        gear:
            Array.isArray(inv.gear)
                ? inv.gear
                : [],

        items:
            Array.isArray(inv.items)
                ? inv.items
                : []

    };
}


function getAmount(item){

    const amount =
        Number(
            item?.amount ??
            item?.quantity ??
            1
        );

    if(!Number.isFinite(amount)){
        return 1;
    }

    return Math.max(
        0,
        amount
    );
}


function formatAmount(value){

    return Number(
        value || 0
    ).toLocaleString();

}


/* =========================================================
   INVENTORY DETAILS
   ========================================================= */

function unitDetails(item){

    const details = [];

    if(item.rarity){
        details.push(
            "Rarity: " +
            esc(item.rarity)
        );
    }

    if(item.level){
        details.push(
            "Level: " +
            esc(item.level)
        );
    }

    if(item.income){
        details.push(
            "Income: " +
            esc(item.income)
        );
    }

    if(item.chance){
        details.push(
            "Chance: " +
            esc(item.chance)
        );
    }

    if(item.grade){
        details.push(
            "Grade: " +
            esc(item.grade)
        );
    }

    if(item.trait){
        details.push(
            "Trait: " +
            esc(item.trait)
        );
    }

    return (
        details.join(" • ") ||
        "Unit"
    );
}


function gearDetails(item){

    const a =
        item.attributes || {};

    const details = [];

    if(item.slot){
        details.push(
            "Slot: " +
            esc(item.slot)
        );
    }

    if(a.slot){
        details.push(
            "Slot: " +
            esc(a.slot)
        );
    }

    if(item.rarity){
        details.push(
            "Rarity: " +
            esc(item.rarity)
        );
    }

    return (
        details.join(" • ") ||
        "Gear"
    );
}


function itemDetails(item){

    const details = [];

    if(item.category){
        details.push(
            esc(item.category)
        );
    }

    if(item.rarity){
        details.push(
            "Rarity: " +
            esc(item.rarity)
        );
    }

    if(item.description){
        details.push(
            esc(item.description)
        );
    }

    return (
        details.join(" • ") ||
        "Item"
    );
}


/* =========================================================
   INVENTORY PREVIEW
   ========================================================= */

function inventoryPreview(account){

    const inv =
        getInventory(account);

    const names = [

        ...inv.units
            .slice(0,1)
            .map(
                x =>
                    "Unit: " +
                    (x.name || "Unknown")
            ),

        ...inv.gear
            .slice(0,1)
            .map(
                x =>
                    "Gear: " +
                    (x.name || "Unknown")
            ),

        ...inv.items
            .slice(0,1)
            .map(
                x =>
                    "Item: " +
                    (x.name || "Unknown")
            )

    ];

    if(!names.length){

        return "No inventory items";

    }

    return names.join(" • ");
}


/* =========================================================
   RENDER ACCOUNTS
   ========================================================= */

function renderAccounts(data){

    const root =
        document.getElementById(
            "accounts"
        );

    document.getElementById(
        "online"
    ).textContent =
        data.online;

    document.getElementById(
        "offline"
    ).textContent =
        data.offline;

    document.getElementById(
        "total"
    ).textContent =
        data.total;


    if(!data.accounts.length){

        root.innerHTML =
            '<div class="empty">' +
            'No accounts have sent a heartbeat yet.' +
            '</div>';

        return;
    }


    root.innerHTML =
        data.accounts.map(
            account => {

                const online =
                    account.online;

                const name =
                    account.playerName ||
                    account.userId ||
                    "?";


                const initial =
                    esc(
                        String(name)
                            .charAt(0)
                            .toUpperCase()
                    );


                const inv =
                    getInventory(account);


                accountCache[
                    String(account.userId)
                ] = account;


                return `

                <div class="account">

                    <div class="user">

                        <div class="avatar">
                            ${initial}
                        </div>

                        <div>

                            <div class="username">
                                ${esc(name)}
                            </div>

                            <div class="userid">
                                ID
                                ${esc(
                                    account.userId ||
                                    "—"
                                )}
                            </div>

                        </div>

                    </div>


                    <div
                        class="${online
                            ? "online"
                            : "offline"}"
                    >

                        <span class="status">

                            <span
                                class="status-dot"
                            ></span>

                            ${online
                                ? "Online"
                                : "Offline"}

                        </span>

                    </div>


                    <div>

                        <button
                            class="inventory-btn"
                            data-userid="${esc(
                                account.userId || ""
                            )}"
                            onclick="openInventory(
                                this.dataset.userid
                            )"
                        >

                            <div
                                class="inventory-summary"
                            >

                                <span
                                    class="category-pill units"
                                >
                                    Units
                                    ${formatAmount(
                                        inv.units.length
                                    )}
                                </span>

                                <span
                                    class="category-pill gear"
                                >
                                    Gear
                                    ${formatAmount(
                                        inv.gear.length
                                    )}
                                </span>

                                <span
                                    class="category-pill items"
                                >
                                    Items
                                    ${formatAmount(
                                        inv.items.length
                                    )}
                                </span>

                            </div>


                            <div
                                class="inventory-preview"
                            >
                                ${esc(
                                    inventoryPreview(
                                        account
                                    )
                                )}
                            </div>

                        </button>

                    </div>


                    <div class="age">

                        ${esc(
                            account.lastSeenText ||
                            "—"
                        )}

                    </div>

                </div>

                `;

            }
        ).join("");

}


/* =========================================================
   INVENTORY SECTION
   ========================================================= */

function renderSection(
    title,
    className,
    items,
    detailFunction
){

    if(!items.length){

        return `

            <div class="section">

                <div
                    class="section-title ${className}"
                >
                    ${title}
                    <span style="opacity:.5">
                        (0)
                    </span>
                </div>

                <div
                    class="item-meta"
                    style="padding:14px"
                >
                    No
                    ${title.toLowerCase()}
                    reported.
                </div>

            </div>

        `;
    }


    return `

        <div class="section">

            <div
                class="section-title ${className}"
            >
                ${title}

                <span style="opacity:.5">
                    (${items.length})
                </span>

            </div>


            ${items.map(item => {

                const amount =
                    getAmount(item);

                const name =
                    item.name ||
                    "Unknown";


                return `

                <div class="inventory-item">

                    <div>

                        <div class="item-name">
                            ${esc(name)}
                        </div>

                        <div class="item-meta">
                            ${detailFunction(item)}
                        </div>

                    </div>


                    <div class="item-amount">

                        ×${formatAmount(amount)}

                    </div>


                    <div class="item-details">

                        ${detailFunction(item)}

                    </div>

                </div>

                `;

            }).join("")}

        </div>

    `;
}


/* =========================================================
   OPEN INVENTORY
   ========================================================= */

function openInventory(userId){

    const account =
        accountCache[
            String(userId)
        ];

    if(!account){
        return;
    }


    const inv =
        getInventory(account);


    const unitCount =
        inv.units.length;

    const gearCount =
        inv.gear.length;

    const itemCount =
        inv.items.length;


    document.getElementById(
        "modalTitle"
    ).textContent =
        `${account.playerName ||
            account.userId}
         Inventory`;


    document.getElementById(
        "modalSubtitle"
    ).textContent =
        `${unitCount} Units •
         ${gearCount} Gear •
         ${itemCount} Items`;


    let html = "";


    html += renderSection(
        "Units",
        "units",
        inv.units,
        unitDetails
    );


    html += renderSection(
        "Gear",
        "gear",
        inv.gear,
        gearDetails
    );


    html += renderSection(
        "Items",
        "items",
        inv.items,
        itemDetails
    );


    document.getElementById(
        "inventoryList"
    ).innerHTML =
        html;


    document.getElementById(
        "inventoryModal"
    ).classList.add("open");

}


/* =========================================================
   CLOSE INVENTORY
   ========================================================= */

function closeInventory(){

    document.getElementById(
        "inventoryModal"
    ).classList.remove("open");

}


document.getElementById(
    "inventoryModal"
).addEventListener(
    "click",
    closeInventory
);


document.addEventListener(
    "keydown",
    event => {

        if(event.key === "Escape"){

            closeInventory();

        }

    }
);


/* =========================================================
   REFRESH
   ========================================================= */

async function refresh(){

    try{

        const response =
            await fetch(
                "/api/accounts",
                {
                    cache:"no-store"
                }
            );


        if(!response.ok){

            throw new Error(
                "Request failed"
            );

        }


        const data =
            await response.json();


        renderAccounts(data);

    }

    catch(error){

        document.getElementById(
            "accounts"
        ).innerHTML =

            '<div class="empty">' +
            'Unable to load account data.' +
            '</div>';

    }

}


refresh();

setInterval(
    refresh,
    2000
);

</script>

</body>
</html>"""



# ============================================================
# API FOR LIVE WEBSITE
# ============================================================

@app.get("/api/accounts")
def api_accounts():
    items = get_accounts_snapshot()

    result = []

    for account in sorted(
        items,
        key=lambda x: (x.get("playerName") or "").lower()
    ):
        online = account_is_online(account)
        inventory = clean_inventory(account.get("inventory"))

        result.append({
            "userId": account.get("userId", ""),
            "playerName": account.get("playerName", ""),
            "displayName": account.get("displayName", ""),
            "online": online,
            "inventory": inventory,
            "lastSeenText": format_age(
                time.time() -
                float(account.get("lastSeen", time.time()))
            )
        })

    online = sum(1 for x in result if x["online"])
    offline = len(result) - online

    return jsonify({
        "ok": True,
        "online": online,
        "offline": offline,
        "total": len(result),
        "accounts": result
    })


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():
    online, offline, total = dashboard_stats()

    return jsonify({
        "ok": True,
        "game": "Anime Dice",
        "online": online,
        "offline": offline,
        "accounts": total,
        "discord_message_id_exists": discord_message_id is not None
    })


# ============================================================
# HEARTBEAT
# ============================================================

@app.post("/heartbeat")
def heartbeat():
    if not check_key(request):
        print("❌ Unauthorized heartbeat request")

        return jsonify({
            "ok": False,
            "error": "Unauthorized"
        }), 401

    data = request.get_json(silent=True) or {}

    user_id = str(data.get("userId", "")).strip()

    if not user_id:
        return jsonify({
            "ok": False,
            "error": "Missing userId"
        }), 400

    player_name = str(
        data.get("playerName") or user_id
    )

    display_name = str(
        data.get("displayName") or ""
    )

    incoming_inventory = data.get("inventory")

    if incoming_inventory is not None:
        inventory = clean_inventory(incoming_inventory)
    else:
        with state_lock:
            old = accounts.get(user_id, {})

        inventory = clean_inventory(
            old.get("inventory")
        )

    print("========== ANIME DICE HEARTBEAT ==========")
    print("PLAYER:", player_name)
    print("UNITS:", len(inventory["units"]))
    print("GEAR:", len(inventory["gear"]))
    print("ITEMS:", len(inventory["items"]))
    print("===========================================")

    with state_lock:
        old = accounts.get(user_id, {})

        accounts[user_id] = {
            "userId": user_id,
            "playerName": player_name,
            "displayName": display_name,
            "inventory": inventory,
            "lastSeen": time.time()
        }

    save_accounts()

    threading.Thread(
        target=update_discord,
        daemon=True
    ).start()

    return jsonify({
        "ok": True,
        "message": "Anime Dice heartbeat received"
    })


# ============================================================
# STARTUP
# ============================================================

load_accounts()
load_discord_message()

threading.Thread(
    target=monitor_loop,
    daemon=True
).start()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port
    )
