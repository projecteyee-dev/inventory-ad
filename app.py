import os
import time
import json
import threading

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

    online = sum(
        1 for account in items
        if account_is_online(account)
    )

    offline = len(items) - online

    return online, offline, len(items)


def clean_inventory(inventory):
    """
    Normalizes Anime Dice inventory.

    Expected:

    {
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
    inventory = clean_inventory(
        account.get("inventory")
    )

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
                print(
                    "❌ Discord PATCH error:",
                    response.text[:1000]
                )
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

            print(
                "✅ Discord monitor message created/updated"
            )

        else:
            print(
                "❌ Discord POST error:",
                response.text[:1000]
            )

    except Exception as error:
        print("❌ Discord connection error:")
        print(repr(error))


def monitor_loop():
    print("🚀 Discord Anime Dice monitor loop started")

    while True:

        try:
            update_discord()

        except Exception as error:
            print(
                "❌ Monitor loop error:",
                repr(error)
            )

        time.sleep(DISCORD_UPDATE_INTERVAL)


# ============================================================
# WEBSITE
# ============================================================

@app.get("/")
def home():

    return """<!doctype html>
<html lang="en">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>KYOSH Anime Dice Monitor</title>

<style>

/* =========================================================
   ANIME DICE THEME
   ========================================================= */

:root{

    --bg:#02040a;

    --panel:rgba(7,12,23,.78);
    --panel2:rgba(11,18,34,.88);

    --line:rgba(118,184,255,.16);
    --line2:rgba(137,210,255,.28);

    --text:#f4f8ff;
    --muted:#7f8da5;

    --green:#73ffb0;
    --lime:#c4ff79;

    --blue:#63c9ff;
    --cyan:#58f2ff;

    --purple:#a879ff;
    --pink:#ff76d9;

    --gold:#ffd76a;

    --red:#ff5f73;
}


/* =========================================================
   RESET
   ========================================================= */

*{
    box-sizing:border-box;
}

html{
    min-height:100%;
    background:#02040a;
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
            rgba(83,180,255,.18),
            transparent 27%
        ),

        radial-gradient(
            circle at 85% 15%,
            rgba(170,91,255,.16),
            transparent 26%
        ),

        radial-gradient(
            circle at 50% 95%,
            rgba(48,255,171,.10),
            transparent 35%
        ),

        linear-gradient(
            180deg,
            #030711 0%,
            #02050d 50%,
            #010308 100%
        );

    overflow-x:hidden;
}


/* =========================================================
   ANIME ENERGY BACKGROUND
   ========================================================= */

.background{

    position:fixed;

    inset:0;

    z-index:-10;

    overflow:hidden;

    pointer-events:none;

    background:

        linear-gradient(
            rgba(255,255,255,.018) 1px,
            transparent 1px
        ),

        linear-gradient(
            90deg,
            rgba(255,255,255,.018) 1px,
            transparent 1px
        );

    background-size:
        60px 60px;

    mask-image:
        linear-gradient(
            to bottom,
            black,
            rgba(0,0,0,.35)
        );
}


/* large anime aura */

.aura{

    position:absolute;

    width:700px;
    height:700px;

    border-radius:50%;

    filter:blur(75px);

    opacity:.13;

    animation:
        auraMove 16s ease-in-out infinite alternate;
}

.aura.one{

    top:-330px;
    left:-250px;

    background:
        radial-gradient(
            circle,
            #4dd8ff 0%,
            #5266ff 35%,
            transparent 70%
        );
}

.aura.two{

    right:-350px;
    top:90px;

    background:
        radial-gradient(
            circle,
            #a855ff 0%,
            #ff4dc4 35%,
            transparent 70%
        );

    animation-delay:-5s;
}

.aura.three{

    bottom:-420px;
    left:25%;

    background:
        radial-gradient(
            circle,
            #38ffb0 0%,
            #3b82f6 35%,
            transparent 70%
        );

    animation-delay:-9s;
}


/* =========================================================
   DICE
   ========================================================= */

.dice{

    position:absolute;

    width:78px;
    height:78px;

    border-radius:18px;

    border:1px solid
        rgba(147,221,255,.25);

    background:

        linear-gradient(
            145deg,
            rgba(104,185,255,.13),
            rgba(147,87,255,.08)
        );

    box-shadow:

        inset 0 0 25px
        rgba(100,180,255,.08),

        0 0 30px
        rgba(90,180,255,.08);

    backdrop-filter:blur(4px);

    display:grid;

    place-items:center;

    color:rgba(185,232,255,.45);

    font-size:25px;

    transform:rotate(22deg);

    animation:
        floatDice 13s ease-in-out infinite;
}

.dice::before{

    content:"✦";

    text-shadow:
        0 0 12px currentColor,
        0 0 28px currentColor;
}

.dice.d1{

    left:6%;
    top:22%;

    transform:rotate(18deg)
        scale(.75);

    animation-duration:14s;
}

.dice.d2{

    right:8%;
    top:30%;

    transform:rotate(-22deg)
        scale(1.15);

    animation-duration:18s;

    animation-delay:-6s;
}

.dice.d3{

    left:13%;
    bottom:15%;

    transform:rotate(-17deg)
        scale(.58);

    animation-duration:16s;

    animation-delay:-3s;
}

.dice.d4{

    right:19%;
    bottom:12%;

    transform:rotate(30deg)
        scale(.68);

    animation-duration:20s;

    animation-delay:-10s;
}

.dice.d5{

    left:46%;
    top:14%;

    transform:rotate(12deg)
        scale(.45);

    animation-duration:12s;

    animation-delay:-4s;
}


/* =========================================================
   PARTICLES
   ========================================================= */

.particles{

    position:absolute;

    inset:0;
}

.particle{

    position:absolute;

    width:3px;
    height:3px;

    border-radius:50%;

    background:#bcecff;

    box-shadow:
        0 0 8px #66d9ff,
        0 0 18px rgba(88,242,255,.65);

    opacity:.45;

    animation:
        particleFloat linear infinite;
}

.p1{left:5%;top:16%;animation-duration:9s}
.p2{left:17%;top:55%;animation-duration:13s}
.p3{left:28%;top:30%;animation-duration:11s}
.p4{left:41%;top:70%;animation-duration:15s}
.p5{left:54%;top:24%;animation-duration:12s}
.p6{left:66%;top:62%;animation-duration:10s}
.p7{left:78%;top:42%;animation-duration:14s}
.p8{left:91%;top:75%;animation-duration:12s}
.p9{left:35%;top:88%;animation-duration:17s}
.p10{left:72%;top:12%;animation-duration:9s}


/* =========================================================
   ENERGY LINES
   ========================================================= */

.energy-line{

    position:absolute;

    height:1px;

    width:55%;

    background:
        linear-gradient(
            90deg,
            transparent,
            rgba(100,216,255,.32),
            rgba(177,110,255,.35),
            transparent
        );

    filter:
        drop-shadow(
            0 0 8px
            rgba(100,216,255,.25)
        );

    transform:rotate(-25deg);

    animation:
        lineMove 10s linear infinite;
}

.energy-line.l1{
    top:28%;
    left:-10%;
}

.energy-line.l2{
    top:67%;
    right:-12%;

    transform:
        rotate(23deg);

    animation-delay:-4s;
}


/* =========================================================
   MAIN WRAPPER
   ========================================================= */

.wrapper{

    position:relative;

    width:min(1450px,94%);

    margin:0 auto;

    padding:
        30px 0 45px;
}


/* top energy bar */

.wrapper::before{

    content:"";

    position:absolute;

    left:0;
    right:0;
    top:0;

    height:2px;

    border-radius:99px;

    background:

        linear-gradient(
            90deg,
            transparent,
            var(--cyan),
            var(--purple),
            var(--green),
            transparent
        );

    box-shadow:

        0 0 18px
        rgba(88,242,255,.45);
}


/* =========================================================
   HEADER
   ========================================================= */

.header{

    display:flex;

    align-items:center;

    justify-content:space-between;

    gap:20px;

    margin-bottom:24px;

    padding-top:10px;
}

.brand{

    display:flex;

    align-items:center;

    gap:15px;
}


/* anime dice logo */

.logo{

    position:relative;

    width:55px;
    height:55px;

    flex:0 0 55px;

    display:grid;

    place-items:center;

    border-radius:17px;

    background:

        linear-gradient(
            145deg,
            rgba(111,226,255,.25),
            rgba(157,92,255,.24)
        );

    border:
        1px solid
        rgba(145,220,255,.5);

    box-shadow:

        inset 0 0 20px
        rgba(95,200,255,.12),

        0 0 0 5px
        rgba(91,193,255,.035),

        0 0 35px
        rgba(86,188,255,.17);

    transform:rotate(-7deg);

    animation:
        logoFloat 5s ease-in-out infinite;
}

.logo::before{

    content:"";

    width:31px;
    height:31px;

    border-radius:8px;

    border:
        2px solid
        rgba(206,244,255,.85);

    background:

        radial-gradient(
            circle at 30% 28%,
            #e7fbff 0 7%,
            transparent 8%
        ),

        radial-gradient(
            circle at 70% 70%,
            #b4ebff 0 7%,
            transparent 8%
        ),

        linear-gradient(
            145deg,
            rgba(89,204,255,.55),
            rgba(137,79,255,.55)
        );

    box-shadow:
        0 0 18px
        rgba(88,242,255,.35);
}

.logo::after{

    content:"";

    position:absolute;

    inset:-9px;

    border-radius:22px;

    border:
        1px solid
        rgba(135,217,255,.14);

    animation:
        logoRing 4s linear infinite;
}


/* =========================================================
   TITLE
   ========================================================= */

h1{

    margin:0;

    font-size:25px;

    letter-spacing:-.8px;

    background:

        linear-gradient(
            90deg,
            #ffffff,
            #a8eaff 42%,
            #d4a7ff 78%,
            #b7ffcf
        );

    -webkit-background-clip:text;
    background-clip:text;

    color:transparent;

    text-shadow:
        0 0 25px
        rgba(97,199,255,.12);
}

.subtitle{

    color:#78869c;

    font-size:13px;

    margin-top:5px;
}


/* =========================================================
   LIVE
   ========================================================= */

.live{

    display:flex;

    align-items:center;

    gap:8px;

    padding:
        10px 15px;

    border:
        1px solid
        rgba(115,255,176,.22);

    background:
        rgba(7,16,25,.64);

    border-radius:999px;

    color:#a6b7c8;

    font-size:11px;

    letter-spacing:.8px;

    font-weight:700;

    box-shadow:
        0 0 25px
        rgba(91,255,176,.05);

    backdrop-filter:blur(10px);
}

.live-dot{

    width:8px;
    height:8px;

    border-radius:50%;

    background:var(--green);

    box-shadow:
        0 0 8px var(--green),
        0 0 18px rgba(115,255,176,.55);

    animation:
        pulse 1.7s infinite;
}


/* =========================================================
   STATS
   ========================================================= */

.stats{

    display:grid;

    grid-template-columns:
        repeat(3,1fr);

    gap:13px;

    margin-bottom:18px;
}

.stat{

    position:relative;

    overflow:hidden;

    background:

        linear-gradient(
            145deg,
            rgba(12,24,42,.80),
            rgba(5,11,21,.84)
        );

    border:
        1px solid
        var(--line);

    border-radius:17px;

    padding:18px 20px;

    box-shadow:
        0 18px 50px
        rgba(0,0,0,.30),

        inset 0 0 25px
        rgba(94,177,255,.025);

    backdrop-filter:blur(14px);

    transition:
        transform .2s ease,
        border-color .2s ease;
}

.stat:hover{

    transform:
        translateY(-3px);

    border-color:
        rgba(109,207,255,.30);
}

.stat::after{

    content:"";

    position:absolute;

    width:120px;
    height:120px;

    right:-65px;
    top:-70px;

    border-radius:50%;

    background:
        radial-gradient(
            circle,
            rgba(92,205,255,.14),
            transparent 68%
        );
}

.stat-label{

    color:#708097;

    font-size:10px;

    margin-bottom:7px;

    text-transform:uppercase;

    letter-spacing:1px;

    font-weight:800;
}

.stat-value{

    font-size:28px;

    font-weight:850;

    color:#f2f8ff;

    text-shadow:
        0 0 18px
        rgba(114,205,255,.08);
}


/* =========================================================
   TABLE
   ========================================================= */

.table{

    border:
        1px solid
        var(--line);

    background:
        rgba(4,9,17,.72);

    border-radius:19px;

    overflow:hidden;

    box-shadow:

        0 30px 90px
        rgba(0,0,0,.42),

        0 0 0 1px
        rgba(93,187,255,.025);

    backdrop-filter:
        blur(18px);
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

    height:50px;

    padding:
        0 18px;

    color:#65748b;

    background:
        linear-gradient(
            180deg,
            rgba(14,27,45,.90),
            rgba(5,12,22,.90)
        );

    border-bottom:
        1px solid
        var(--line);

    font-size:10px;

    font-weight:800;

    text-transform:uppercase;

    letter-spacing:1px;
}

.account{

    padding:
        14px 18px;

    min-height:76px;

    border-bottom:
        1px solid
        rgba(105,160,210,.09);

    transition:
        background .18s ease,
        transform .18s ease;
}

.account:last-child{
    border-bottom:0;
}

.account:hover{

    background:

        linear-gradient(
            90deg,
            rgba(73,163,255,.07),
            rgba(167,97,255,.045),
            transparent
        );
}


/* =========================================================
   ACCOUNT
   ========================================================= */

.user{

    display:flex;

    align-items:center;

    gap:11px;

    min-width:0;
}

.avatar{

    width:38px;
    height:38px;

    flex:0 0 38px;

    border-radius:13px;

    display:grid;

    place-items:center;

    background:

        linear-gradient(
            145deg,
            rgba(100,221,255,.32),
            rgba(143,86,255,.28)
        );

    border:
        1px solid
        rgba(145,220,255,.38);

    color:#dff7ff;

    font-size:12px;

    font-weight:900;

    box-shadow:
        0 0 18px
        rgba(83,186,255,.10);
}

.username{

    overflow:hidden;

    text-overflow:ellipsis;

    white-space:nowrap;

    font-weight:700;

    font-size:14px;
}

.userid{

    color:#536279;

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

    font-weight:700;
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
        0 0 15px rgba(115,255,176,.4);
}

.online .status{
    color:#a8f7c5;
}

.offline .status-dot{

    background:var(--red);

    box-shadow:
        0 0 8px rgba(255,95,115,.35);
}

.offline .status{
    color:#667286;
}


/* =========================================================
   INVENTORY
   ========================================================= */

.inventory-summary{

    display:flex;

    gap:7px;

    flex-wrap:wrap;
}

.category-pill{

    border:
        1px solid
        rgba(120,180,220,.16);

    background:
        rgba(12,24,39,.72);

    border-radius:999px;

    padding:
        6px 9px;

    font-size:10px;

    font-weight:800;

    color:#d6e1ed;
}

.category-pill.units{
    color:var(--lime);
}

.category-pill.gear{
    color:var(--purple);
}

.category-pill.items{
    color:var(--blue);
}

.inventory-btn{

    border:
        1px solid
        rgba(115,184,235,.17);

    background:

        linear-gradient(
            135deg,
            rgba(18,36,58,.78),
            rgba(8,17,29,.84)
        );

    color:#e5eff9;

    border-radius:11px;

    padding:
        9px 12px;

    cursor:pointer;

    font:inherit;

    transition:
        .15s ease;

    width:100%;

    text-align:left;

    box-shadow:
        inset 0 0 20px
        rgba(89,190,255,.02);
}

.inventory-btn:hover{

    background:

        linear-gradient(
            135deg,
            rgba(29,60,91,.85),
            rgba(14,25,43,.92)
        );

    border-color:
        rgba(124,216,255,.40);

    transform:
        translateY(-1px);

    box-shadow:
        0 0 20px
        rgba(81,184,255,.08);
}

.inventory-preview{

    color:#64758b;

    font-size:10px;

    margin-top:4px;

    white-space:nowrap;

    overflow:hidden;

    text-overflow:ellipsis;
}


/* =========================================================
   AGE
   ========================================================= */

.age{

    color:#69778b;

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
        rgba(0,3,9,.82);

    backdrop-filter:
        blur(13px);
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
            rgba(13,27,46,.96),
            rgba(4,11,21,.97)
        );

    border:
        1px solid
        rgba(110,196,255,.25);

    border-radius:19px;

    box-shadow:

        0 35px 100px
        rgba(0,0,0,.72),

        0 0 50px
        rgba(73,180,255,.08);
}

.modal-head{

    display:flex;

    align-items:center;

    justify-content:space-between;

    gap:15px;

    padding:
        16px 18px;

    border-bottom:
        1px solid
        var(--line);
}

.modal-title{

    font-weight:800;

    font-size:16px;
}

.modal-subtitle{

    color:var(--muted);

    font-size:11px;

    margin-top:3px;
}

.modal-close{

    border:
        1px solid
        rgba(130,184,235,.22);

    background:
        rgba(17,33,52,.85);

    color:#dfeaf5;

    border-radius:9px;

    width:34px;
    height:34px;

    cursor:pointer;

    font-size:18px;

    transition:.15s;
}

.modal-close:hover{

    border-color:
        rgba(105,211,255,.45);

    background:
        rgba(27,53,78,.95);
}


/* =========================================================
   INVENTORY LIST
   ========================================================= */

.inventory-list{

    max-height:70vh;

    overflow:auto;

    padding:12px;
}

.section{

    margin-bottom:18px;

    border:
        1px solid
        rgba(105,160,210,.12);

    border-radius:13px;

    overflow:hidden;

    background:
        rgba(3,10,18,.40);
}

.section-title{

    padding:
        12px 13px;

    font-size:12px;

    font-weight:900;

    letter-spacing:.7px;

    text-transform:uppercase;

    background:
        rgba(15,30,48,.74);

    border-bottom:
        1px solid
        rgba(105,160,210,.11);
}

.section-title.units{
    color:var(--lime);
}

.section-title.gear{
    color:var(--purple);
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
        11px 13px;

    border-bottom:
        1px solid
        rgba(105,160,210,.08);
}

.inventory-item:last-child{
    border-bottom:0;
}

.item-name{

    font-weight:800;

    font-size:12px;
}

.item-meta{

    color:#64758a;

    font-size:10px;

    margin-top:3px;
}

.item-amount{

    color:#e9f5ff;

    font-size:12px;

    font-weight:900;

    text-align:center;
}

.item-details{

    color:#91a3b6;

    font-size:10px;

    text-align:right;
}


/* =========================================================
   EMPTY / FOOTER
   ========================================================= */

.empty{

    padding:70px 20px;

    text-align:center;

    color:var(--muted);
}

.footer{

    padding:
        14px 18px;

    color:#56657a;

    font-size:11px;

    border-top:
        1px solid
        var(--line);

    background:
        rgba(3,9,16,.78);
}


/* =========================================================
   SCROLLBAR
   ========================================================= */

::-webkit-scrollbar{
    width:7px;
    height:7px;
}

::-webkit-scrollbar-track{
    background:#030711;
}

::-webkit-scrollbar-thumb{

    background:
        linear-gradient(
            180deg,
            #3d7da0,
            #624b8e
        );

    border-radius:99px;
}


/* =========================================================
   ANIMATIONS
   ========================================================= */

@keyframes pulse{

    0%,100%{
        transform:scale(1);
        opacity:1;
    }

    50%{
        transform:scale(1.25);
        opacity:.72;
    }
}

@keyframes auraMove{

    0%{
        transform:
            translate3d(-20px,0,0)
            scale(1);
    }

    50%{
        transform:
            translate3d(80px,50px,0)
            scale(1.08);
    }

    100%{
        transform:
            translate3d(-40px,100px,0)
            scale(.96);
    }
}

@keyframes floatDice{

    0%{
        margin-top:0;
    }

    50%{
        margin-top:-28px;
    }

    100%{
        margin-top:0;
    }
}

@keyframes particleFloat{

    0%{
        transform:
            translateY(30px)
            scale(.7);

        opacity:0;
    }

    20%{
        opacity:.55;
    }

    50%{
        transform:
            translateY(-50px)
            scale(1.2);

        opacity:.8;
    }

    80%{
        opacity:.35;
    }

    100%{
        transform:
            translateY(-110px)
            scale(.5);

        opacity:0;
    }
}

@keyframes lineMove{

    0%{
        opacity:0;
        transform:
            translateX(-30%)
            rotate(-25deg);
    }

    20%{
        opacity:.7;
    }

    80%{
        opacity:.4;
    }

    100%{
        opacity:0;
        transform:
            translateX(100%)
            rotate(-25deg);
    }
}

@keyframes logoFloat{

    0%,100%{
        transform:
            rotate(-7deg)
            translateY(0);
    }

    50%{
        transform:
            rotate(-2deg)
            translateY(-5px);
    }
}

@keyframes logoRing{

    from{
        transform:rotate(0deg);
    }

    to{
        transform:rotate(360deg);
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

    .dice.d5{
        display:none;
    }
}

@media(max-width:500px){

    .wrapper{
        width:94%;
    }

    h1{
        font-size:20px;
    }

    .subtitle{
        font-size:11px;
    }

    .logo{
        width:46px;
        height:46px;
        flex-basis:46px;
    }

    .stat{
        padding:15px 16px;
    }

    .stat-value{
        font-size:24px;
    }

    .dice{
        opacity:.45;
    }
}

</style>

</head>


<body>


<!-- ========================================================
     ANIME BACKGROUND
     ======================================================== -->

<div class="background">

    <div class="aura one"></div>
    <div class="aura two"></div>
    <div class="aura three"></div>

    <div class="energy-line l1"></div>
    <div class="energy-line l2"></div>


    <div class="dice d1"></div>
    <div class="dice d2"></div>
    <div class="dice d3"></div>
    <div class="dice d4"></div>
    <div class="dice d5"></div>


    <div class="particles">

        <div class="particle p1"></div>
        <div class="particle p2"></div>
        <div class="particle p3"></div>
        <div class="particle p4"></div>
        <div class="particle p5"></div>
        <div class="particle p6"></div>
        <div class="particle p7"></div>
        <div class="particle p8"></div>
        <div class="particle p9"></div>
        <div class="particle p10"></div>

    </div>

</div>


<div class="wrapper">


    <!-- ====================================================
         HEADER
         ==================================================== -->

    <div class="header">

        <div class="brand">

            <div class="logo"></div>

            <div>

                <h1>
                    KYOSH ANIME DICE MONITOR
                </h1>

                <div class="subtitle">
                    Live Anime Dice account and inventory monitor
                </div>

            </div>

        </div>


        <div class="live">

            <span class="live-dot"></span>

            LIVE MONITOR

        </div>

    </div>


    <!-- ====================================================
         STATS
         ==================================================== -->

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


    <!-- ====================================================
         ACCOUNT TABLE
         ==================================================== -->

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
            • KYOSH Anime Dice Monitor

        </div>

    </div>


    <!-- ====================================================
         INVENTORY MODAL
         ==================================================== -->

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

/* ==========================================================
   SECURITY ESCAPER
   ========================================================== */

function esc(value){

    return String(value ?? "")

        .replaceAll("&","&amp;")
        .replaceAll("<","&lt;")
        .replaceAll(">","&gt;")
        .replaceAll('"',"&quot;")
        .replaceAll("'","&#039;");
}


/* ==========================================================
   ACCOUNT CACHE
   ========================================================== */

const accountCache = {};


/* ==========================================================
   INVENTORY
   ========================================================== */

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


/* ==========================================================
   AMOUNT
   ========================================================== */

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

    return Math.max(0,amount);
}


function formatAmount(value){

    return Number(value || 0)
        .toLocaleString();
}


/* ==========================================================
   UNIT DETAILS
   ========================================================== */

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


/* ==========================================================
   GEAR DETAILS
   ========================================================== */

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


/* ==========================================================
   ITEM DETAILS
   ========================================================== */

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


/* ==========================================================
   INVENTORY PREVIEW
   ========================================================== */

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


/* ==========================================================
   RENDER ACCOUNTS
   ========================================================== */

function renderAccounts(data){

    const root =
        document.getElementById("accounts");

    document.getElementById("online")
        .textContent =
        data.online;

    document.getElementById("offline")
        .textContent =
        data.offline;

    document.getElementById("total")
        .textContent =
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
                        class="${
                            online
                                ? "online"
                                : "offline"
                        }"
                    >

                        <span class="status">

                            <span
                                class="status-dot"
                            ></span>

                            ${
                                online
                                    ? "Online"
                                    : "Offline"
                            }

                        </span>

                    </div>


                    <div>

                        <button
                            class="inventory-btn"
                            data-userid="${
                                esc(
                                    account.userId ||
                                    ""
                                )
                            }"
                            onclick="
                                openInventory(
                                    this.dataset.userid
                                )
                            "
                        >

                            <div
                                class="inventory-summary"
                            >

                                <span
                                    class="
                                        category-pill
                                        units
                                    "
                                >
                                    Units
                                    ${
                                        formatAmount(
                                            inv.units.length
                                        )
                                    }
                                </span>


                                <span
                                    class="
                                        category-pill
                                        gear
                                    "
                                >
                                    Gear
                                    ${
                                        formatAmount(
                                            inv.gear.length
                                        )
                                    }
                                </span>


                                <span
                                    class="
                                        category-pill
                                        items
                                    "
                                >
                                    Items
                                    ${
                                        formatAmount(
                                            inv.items.length
                                        )
                                    }
                                </span>

                            </div>


                            <div
                                class="inventory-preview"
                            >
                                ${
                                    esc(
                                        inventoryPreview(
                                            account
                                        )
                                    )
                                }
                            </div>

                        </button>

                    </div>


                    <div class="age">

                        ${
                            esc(
                                account.lastSeenText ||
                                "—"
                            )
                        }

                    </div>

                </div>

                `;
            }
        ).join("");
}


/* ==========================================================
   INVENTORY SECTION
   ========================================================== */

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
                    class="
                        section-title
                        ${className}
                    "
                >

                    ${title}

                    <span
                        style="opacity:.5"
                    >
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
                class="
                    section-title
                    ${className}
                "
            >

                ${title}

                <span
                    style="opacity:.5"
                >
                    (${items.length})
                </span>

            </div>


            ${
                items.map(
                    item => {

                        const amount =
                            getAmount(item);

                        const name =
                            item.name ||
                            "Unknown";


                        return `

                        <div
                            class="inventory-item"
                        >

                            <div>

                                <div
                                    class="item-name"
                                >
                                    ${esc(name)}
                                </div>

                                <div
                                    class="item-meta"
                                >
                                    ${
                                        detailFunction(
                                            item
                                        )
                                    }
                                </div>

                            </div>


                            <div
                                class="item-amount"
                            >
                                ×
                                ${
                                    formatAmount(
                                        amount
                                    )
                                }
                            </div>


                            <div
                                class="item-details"
                            >
                                ${
                                    detailFunction(
                                        item
                                    )
                                }
                            </div>

                        </div>

                        `;
                    }
                ).join("")
            }

        </div>

    `;
}


/* ==========================================================
   OPEN INVENTORY
   ========================================================== */

function openInventory(userId){

    const account =
        accountCache[String(userId)];

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
        `${
            account.playerName ||
            account.userId
        } Inventory`;


    document.getElementById(
        "modalSubtitle"
    ).textContent =
        `${
            unitCount
        } Units • ${
            gearCount
        } Gear • ${
            itemCount
        } Items`;


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
    ).innerHTML = html;


    document.getElementById(
        "inventoryModal"
    ).classList.add("open");
}


/* ==========================================================
   CLOSE INVENTORY
   ========================================================== */

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


/* ==========================================================
   REFRESH
   ========================================================== */

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
        key=lambda x:
            (x.get("playerName") or "").lower()
    ):

        online =
            account_is_online(account)

        inventory =
            clean_inventory(
                account.get("inventory")
            )


        result.append({

            "userId":
                account.get(
                    "userId",
                    ""
                ),

            "playerName":
                account.get(
                    "playerName",
                    ""
                ),

            "displayName":
                account.get(
                    "displayName",
                    ""
                ),

            "online":
                online,

            "inventory":
                inventory,

            "lastSeenText":
                format_age(
                    time.time() -
                    float(
                        account.get(
                            "lastSeen",
                            time.time()
                        )
                    )
                )

        })


    online =
        sum(
            1
            for x in result
            if x["online"]
        )

    offline =
        len(result) - online


    return jsonify({

        "ok": True,

        "online":
            online,

        "offline":
            offline,

        "total":
            len(result),

        "accounts":
            result

    })


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    online, offline, total =
        dashboard_stats()


    return jsonify({

        "ok":
            True,

        "game":
            "Anime Dice",

        "online":
            online,

        "offline":
            offline,

        "accounts":
            total,

        "discord_message_id_exists":
            discord_message_id is not None

    })


# ============================================================
# HEARTBEAT
# ============================================================

@app.post("/heartbeat")
def heartbeat():

    if not check_key(request):

        print(
            "❌ Unauthorized heartbeat request"
        )

        return jsonify({

            "ok":
                False,

            "error":
                "Unauthorized"

        }), 401


    data =
        request.get_json(
            silent=True
        ) or {}


    user_id =
        str(
            data.get(
                "userId",
                ""
            )
        ).strip()


    if not user_id:

        return jsonify({

            "ok":
                False,

            "error":
                "Missing userId"

        }), 400


    player_name =
        str(
            data.get(
                "playerName"
            ) or user_id
        )


    display_name =
        str(
            data.get(
                "displayName"
            ) or ""
        )


    incoming_inventory =
        data.get("inventory")


    if incoming_inventory is not None:

        inventory =
            clean_inventory(
                incoming_inventory
            )

    else:

        with state_lock:

            old =
                accounts.get(
                    user_id,
                    {}
                )


        inventory =
            clean_inventory(
                old.get(
                    "inventory"
                )
            )


    print(
        "========== ANIME DICE HEARTBEAT =========="
    )

    print(
        "PLAYER:",
        player_name
    )

    print(
        "UNITS:",
        len(
            inventory["units"]
        )
    )

    print(
        "GEAR:",
        len(
            inventory["gear"]
        )
    )

    print(
        "ITEMS:",
        len(
            inventory["items"]
        )
    )

    print(
        "==========================================="
    )


    with state_lock:

        old =
            accounts.get(
                user_id,
                {}
            )


        accounts[user_id] = {

            "userId":
                user_id,

            "playerName":
                player_name,

            "displayName":
                display_name,

            "inventory":
                inventory,

            "lastSeen":
                time.time()

        }


    save_accounts()


    threading.Thread(
        target=update_discord,
        daemon=True
    ).start()


    return jsonify({

        "ok":
            True,

        "message":
            "Anime Dice heartbeat received"

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

    port =
        int(
            os.environ.get(
                "PORT",
                "10000"
            )
        )


    app.run(

        host="0.0.0.0",

        port=port

    )


