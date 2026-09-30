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


# ============================================================
# WEBSITE
# ============================================================

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
    --bg:#06110a;
    --panel:rgba(12,30,17,.90);
    --panel2:rgba(17,39,22,.96);
    --line:rgba(116,188,91,.20);
    --line2:rgba(160,220,120,.30);
    --text:#f4ffe9;
    --muted:#8fa58b;
    --green:#69e85d;
    --lime:#b8ff72;
    --red:#ff5b62;
    --blue:#72c7ff;
    --purple:#c084ff;
    --gold:#ffd75a;
}

*{box-sizing:border-box}

html{background:#06110a}

body{
    margin:0;
    min-height:100vh;
    color:var(--text);
    font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    background:
        radial-gradient(circle at 12% 8%,rgba(104,232,93,.16),transparent 24%),
        radial-gradient(circle at 88% 18%,rgba(184,255,114,.10),transparent 22%),
        radial-gradient(circle at 50% 100%,rgba(46,189,85,.13),transparent 35%),
        linear-gradient(180deg,#07150a 0%,#06110a 55%,#040b06 100%);
    overflow-x:hidden;
}

body::before,
body::after{
    content:"";
    position:fixed;
    z-index:-1;
    width:180px;
    height:225px;
    border-radius:52% 48% 50% 50% / 58% 58% 42% 42%;
    background:
        radial-gradient(circle at 34% 28%,rgba(255,255,255,.85) 0 3%,transparent 4%),
        radial-gradient(circle at 42% 35%,rgba(255,255,255,.18) 0 10%,transparent 11%),
        linear-gradient(145deg,#f6ffe8,#ccecae 48%,#82c968);
    opacity:.045;
}

body::before{
    top:70px;
    left:-65px;
    transform:rotate(-18deg);
}

body::after{
    right:-70px;
    bottom:40px;
    transform:rotate(24deg) scale(.8);
}

.wrapper{
    position:relative;
    width:min(1450px,94%);
    margin:0 auto;
    padding:30px 0 45px;
}

.wrapper::before{
    content:"";
    position:absolute;
    left:0;
    right:0;
    top:0;
    height:3px;
    border-radius:99px;
    background:linear-gradient(90deg,transparent,var(--green),var(--lime),var(--green),transparent);
    box-shadow:0 0 24px rgba(105,232,93,.45);
}

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
    gap:14px;
}

.logo{
    width:50px;
    height:50px;
    border-radius:16px 16px 19px 19px;
    display:grid;
    place-items:center;
    background:linear-gradient(145deg,#f7ffe9,#bfe99d 55%,#68bd5e);
    border:2px solid rgba(196,255,142,.65);
    box-shadow:0 0 0 4px rgba(105,232,93,.08),0 8px 25px rgba(0,0,0,.35),0 0 28px rgba(105,232,93,.16);
    color:#163315;
    font-size:25px;
    transform:rotate(-5deg);
}

.logo::after{
    content:"🎲";
}

h1{
    margin:0;
    font-size:24px;
    letter-spacing:-.7px;
    text-shadow:0 2px 18px rgba(105,232,93,.14);
}

.subtitle{
    color:var(--muted);
    font-size:13px;
    margin-top:4px;
}

.live{
    display:flex;
    align-items:center;
    gap:8px;
    padding:10px 15px;
    border:1px solid var(--line2);
    background:rgba(12,30,17,.78);
    border-radius:999px;
    color:#b9cbb4;
    font-size:12px;
}

.live-dot{
    width:8px;
    height:8px;
    border-radius:50%;
    background:var(--green);
    box-shadow:0 0 8px var(--green),0 0 16px rgba(105,232,93,.45);
    animation:pulse 1.7s infinite;
}

.stats{
    display:grid;
    grid-template-columns:repeat(3,1fr);
    gap:13px;
    margin-bottom:18px;
}

.stat{
    position:relative;
    overflow:hidden;
    background:linear-gradient(145deg,rgba(22,51,27,.90),rgba(8,25,13,.92));
    border:1px solid var(--line);
    border-radius:17px;
    padding:18px 20px;
    box-shadow:0 15px 40px rgba(0,0,0,.20);
}

.stat-label{
    color:#91aa8c;
    font-size:11px;
    margin-bottom:7px;
    text-transform:uppercase;
    letter-spacing:.8px;
    font-weight:700;
}

.stat-value{
    font-size:27px;
    font-weight:800;
    color:#f3ffe8;
}

.table{
    border:1px solid var(--line);
    background:var(--panel);
    border-radius:18px;
    overflow:hidden;
    box-shadow:0 25px 70px rgba(0,0,0,.32),0 0 0 1px rgba(105,232,93,.025);
    backdrop-filter:blur(12px);
}

.table-scroll{
    overflow-x:auto;
}

.table-head,
.account{
    display:grid;
    grid-template-columns:minmax(210px,2fr) 120px minmax(180px,1.7fr) 110px;
    align-items:center;
    min-width:760px;
}

.table-head{
    height:50px;
    padding:0 18px;
    color:#769072;
    background:linear-gradient(180deg,rgba(20,45,24,.96),rgba(9,23,13,.96));
    border-bottom:1px solid var(--line);
    font-size:10px;
    font-weight:800;
    text-transform:uppercase;
    letter-spacing:1px;
}

.account{
    padding:14px 18px;
    min-height:76px;
    border-bottom:1px solid rgba(105,160,88,.12);
    transition:background .18s ease;
}

.account:last-child{
    border-bottom:0;
}

.account:hover{
    background:linear-gradient(90deg,rgba(72,154,66,.12),rgba(105,232,93,.035));
}

.user{
    display:flex;
    align-items:center;
    gap:11px;
    min-width:0;
}

.avatar{
    width:37px;
    height:37px;
    flex:0 0 37px;
    border-radius:13px;
    display:grid;
    place-items:center;
    background:linear-gradient(145deg,#eaffd4,#94d477 65%,#4a9c4d);
    border:1px solid rgba(202,255,158,.55);
    color:#163b19;
    font-size:12px;
    font-weight:900;
    box-shadow:0 5px 14px rgba(0,0,0,.25);
}

.username{
    overflow:hidden;
    text-overflow:ellipsis;
    white-space:nowrap;
    font-weight:700;
    font-size:14px;
}

.userid{
    color:#637a61;
    font-size:10px;
    margin-top:2px;
}

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
    box-shadow:0 0 8px var(--green),0 0 14px rgba(105,232,93,.35);
}

.online .status{color:#bfeeb6}

.offline .status-dot{
    background:var(--red);
    box-shadow:0 0 7px rgba(255,91,98,.35);
}

.offline .status{color:#788276}

.inventory-summary{
    display:flex;
    gap:7px;
    flex-wrap:wrap;
}

.category-pill{
    border:1px solid rgba(112,178,92,.22);
    background:rgba(18,40,22,.8);
    border-radius:999px;
    padding:6px 9px;
    font-size:10px;
    font-weight:800;
    color:#dcebd7;
}

.category-pill.units{
    color:#b8ff72;
}

.category-pill.gear{
    color:#c084ff;
}

.category-pill.items{
    color:#72c7ff;
}

.inventory-btn{
    border:1px solid rgba(112,178,92,.25);
    background:linear-gradient(135deg,rgba(28,59,31,.85),rgba(12,31,17,.9));
    color:#e4f4dc;
    border-radius:10px;
    padding:9px 12px;
    cursor:pointer;
    font:inherit;
    transition:.15s ease;
    width:100%;
}

.inventory-btn:hover{
    background:linear-gradient(135deg,rgba(48,93,48,.9),rgba(15,39,20,.95));
    border-color:rgba(184,255,114,.45);
    transform:translateY(-1px);
}

.inventory-preview{
    color:#748b70;
    font-size:10px;
    margin-top:4px;
    white-space:nowrap;
    overflow:hidden;
    text-overflow:ellipsis;
}

.age{
    color:#70836d;
    font-size:12px;
}

.modal{
    position:fixed;
    inset:0;
    z-index:1000;
    display:none;
    align-items:center;
    justify-content:center;
    padding:20px;
    background:rgba(1,8,3,.78);
    backdrop-filter:blur(9px);
}

.modal.open{display:flex}

.modal-card{
    width:min(900px,96vw);
    max-height:88vh;
    overflow:hidden;
    background:linear-gradient(145deg,#102616,#08170c);
    border:1px solid rgba(133,210,104,.28);
    border-radius:18px;
    box-shadow:0 30px 90px rgba(0,0,0,.65),0 0 45px rgba(72,154,66,.10);
}

.modal-head{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:15px;
    padding:16px 18px;
    border-bottom:1px solid var(--line);
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
    border:1px solid rgba(130,184,112,.25);
    background:#14291a;
    color:#dfeeda;
    border-radius:9px;
    width:34px;
    height:34px;
    cursor:pointer;
    font-size:18px;
}

.inventory-list{
    max-height:70vh;
    overflow:auto;
    padding:12px;
}

.section{
    margin-bottom:18px;
    border:1px solid rgba(105,160,88,.12);
    border-radius:13px;
    overflow:hidden;
    background:rgba(7,20,10,.38);
}

.section-title{
    padding:12px 13px;
    font-size:12px;
    font-weight:900;
    letter-spacing:.7px;
    text-transform:uppercase;
    background:rgba(18,40,22,.75);
    border-bottom:1px solid rgba(105,160,88,.12);
}

.section-title.units{color:var(--lime)}
.section-title.gear{color:var(--purple)}
.section-title.items{color:var(--blue)}

.inventory-item{
    display:grid;
    grid-template-columns:minmax(180px,1fr) 90px minmax(150px,1fr);
    gap:12px;
    align-items:center;
    padding:11px 13px;
    border-bottom:1px solid rgba(105,160,88,.10);
}

.inventory-item:last-child{border-bottom:0}

.item-name{
    font-weight:800;
    font-size:12px;
}

.item-meta{
    color:#70836d;
    font-size:10px;
    margin-top:3px;
}

.item-amount{
    color:#eaffdc;
    font-size:12px;
    font-weight:900;
    text-align:center;
}

.item-details{
    color:#9cb297;
    font-size:10px;
    text-align:right;
}

.empty{
    padding:70px 20px;
    text-align:center;
    color:var(--muted);
}

.footer{
    padding:14px 18px;
    color:#61745e;
    font-size:11px;
    border-top:1px solid var(--line);
    background:rgba(7,20,10,.85);
}

@keyframes pulse{
    0%,100%{transform:scale(1);opacity:1}
    50%{transform:scale(1.18);opacity:.75}
}

@media(max-width:800px){
    .wrapper{width:96%;padding-top:20px}
    .stats{grid-template-columns:1fr}
    .header{align-items:flex-start}
    .live{display:none}
}

@media(max-width:500px){
    .wrapper{width:94%}
    h1{font-size:20px}
    .subtitle{font-size:12px}
    .logo{width:44px;height:44px;font-size:21px}
    .stat{padding:15px 16px}
    .stat-value{font-size:24px}
}
</style>
</head>

<body>
<div class="wrapper">

    <div class="header">
        <div class="brand">
            <div class="logo"></div>
            <div>
                <h1>KYOSH ANIME DICE MONITOR</h1>
                <div class="subtitle">Live Anime Dice account and inventory monitor</div>
            </div>
        </div>

        <div class="live">
            <span class="live-dot"></span>
            LIVE MONITOR
        </div>
    </div>

    <div class="stats">
        <div class="stat">
            <div class="stat-label">ONLINE</div>
            <div class="stat-value" id="online">0</div>
        </div>

        <div class="stat">
            <div class="stat-label">OFFLINE</div>
            <div class="stat-value" id="offline">0</div>
        </div>

        <div class="stat">
            <div class="stat-label">TOTAL ACCOUNTS</div>
            <div class="stat-value" id="total">0</div>
        </div>
    </div>

    <div class="table">
        <div class="table-scroll">
            <div class="table-head">
                <div>ACCOUNT</div>
                <div>STATUS</div>
                <div>INVENTORY</div>
                <div>LAST SEEN</div>
            </div>

            <div id="accounts">
                <div class="empty">Loading accounts...</div>
            </div>
        </div>

        <div class="footer">
            Auto-refreshing every 2 seconds • KYOSH Anime Dice Monitor
        </div>
    </div>

    <div class="modal" id="inventoryModal">
        <div class="modal-card" onclick="event.stopPropagation()">
            <div class="modal-head">
                <div>
                    <div class="modal-title" id="modalTitle">Inventory</div>
                    <div class="modal-subtitle" id="modalSubtitle"></div>
                </div>
                <button class="modal-close" onclick="closeInventory()">×</button>
            </div>

            <div class="inventory-list" id="inventoryList"></div>
        </div>
    </div>

</div>

<script>
function esc(value){
    return String(value ?? "")
        .replaceAll("&","&amp;")
        .replaceAll("<","&lt;")
        .replaceAll(">","&gt;")
        .replaceAll('"',"&quot;")
        .replaceAll("'","&#039;");
}

const accountCache = {};

function getInventory(account){
    const inv = account.inventory || {};

    return {
        units: Array.isArray(inv.units) ? inv.units : [],
        gear: Array.isArray(inv.gear) ? inv.gear : [],
        items: Array.isArray(inv.items) ? inv.items : []
    };
}

function getAmount(item){
    const amount = Number(item?.amount ?? item?.quantity ?? 1);

    if(!Number.isFinite(amount)){
        return 1;
    }

    return Math.max(0, amount);
}

function formatAmount(value){
    return Number(value || 0).toLocaleString();
}

function unitDetails(item){
    const details = [];

    if(item.rarity){
        details.push("Rarity: " + esc(item.rarity));
    }

    if(item.level){
        details.push("Level: " + esc(item.level));
    }

    if(item.income){
        details.push("Income: " + esc(item.income));
    }

    if(item.chance){
        details.push("Chance: " + esc(item.chance));
    }

    if(item.grade){
        details.push("Grade: " + esc(item.grade));
    }

    if(item.trait){
        details.push("Trait: " + esc(item.trait));
    }

    return details.join(" • ") || "Unit";
}

function gearDetails(item){
    const a = item.attributes || {};
    const details = [];

    if(item.slot){
        details.push("Slot: " + esc(item.slot));
    }

    if(a.slot){
        details.push("Slot: " + esc(a.slot));
    }

    if(item.rarity){
        details.push("Rarity: " + esc(item.rarity));
    }

    return details.join(" • ") || "Gear";
}

function itemDetails(item){
    const details = [];

    if(item.category){
        details.push(esc(item.category));
    }

    if(item.rarity){
        details.push("Rarity: " + esc(item.rarity));
    }

    if(item.description){
        details.push(esc(item.description));
    }

    return details.join(" • ") || "Item";
}

function inventoryPreview(account){
    const inv = getInventory(account);

    const names = [
        ...inv.units.slice(0,1).map(x => "Unit: " + (x.name || "Unknown")),
        ...inv.gear.slice(0,1).map(x => "Gear: " + (x.name || "Unknown")),
        ...inv.items.slice(0,1).map(x => "Item: " + (x.name || "Unknown"))
    ];

    if(!names.length){
        return "No inventory items";
    }

    return names.join(" • ");
}

function renderAccounts(data){
    const root = document.getElementById("accounts");

    document.getElementById("online").textContent = data.online;
    document.getElementById("offline").textContent = data.offline;
    document.getElementById("total").textContent = data.total;

    if(!data.accounts.length){
        root.innerHTML =
            '<div class="empty">No accounts have sent a heartbeat yet.</div>';
        return;
    }

    root.innerHTML = data.accounts.map(account => {
        const online = account.online;
        const name = account.playerName || account.userId || "?";

        const initial = esc(
            String(name).charAt(0).toUpperCase()
        );

        const inv = getInventory(account);

        accountCache[String(account.userId)] = account;

        return `
        <div class="account">
            <div class="user">
                <div class="avatar">${initial}</div>
                <div>
                    <div class="username">${esc(name)}</div>
                    <div class="userid">
                        ID ${esc(account.userId || "—")}
                    </div>
                </div>
            </div>

            <div class="${online ? "online" : "offline"}">
                <span class="status">
                    <span class="status-dot"></span>
                    ${online ? "Online" : "Offline"}
                </span>
            </div>

            <div>
                <button
                    class="inventory-btn"
                    data-userid="${esc(account.userId || "")}"
                    onclick="openInventory(this.dataset.userid)"
                >
                    <div class="inventory-summary">
                        <span class="category-pill units">
                            Units ${formatAmount(inv.units.length)}
                        </span>

                        <span class="category-pill gear">
                            Gear ${formatAmount(inv.gear.length)}
                        </span>

                        <span class="category-pill items">
                            Items ${formatAmount(inv.items.length)}
                        </span>
                    </div>

                    <div class="inventory-preview">
                        ${esc(inventoryPreview(account))}
                    </div>
                </button>
            </div>

            <div class="age">
                ${esc(account.lastSeenText || "—")}
            </div>
        </div>`;
    }).join("");
}

function renderSection(title, className, items, detailFunction){
    if(!items.length){
        return `
            <div class="section">
                <div class="section-title ${className}">
                    ${title} <span style="opacity:.5">(0)</span>
                </div>
                <div class="item-meta" style="padding:14px">
                    No ${title.toLowerCase()} reported.
                </div>
            </div>
        `;
    }

    return `
        <div class="section">
            <div class="section-title ${className}">
                ${title} <span style="opacity:.5">(${items.length})</span>
            </div>

            ${items.map(item => {
                const amount = getAmount(item);
                const name = item.name || "Unknown";

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

function openInventory(userId){
    const account = accountCache[String(userId)];

    if(!account) return;

    const inv = getInventory(account);

    const unitCount = inv.units.length;
    const gearCount = inv.gear.length;
    const itemCount = inv.items.length;

    document.getElementById("modalTitle").textContent =
        `${account.playerName || account.userId} Inventory`;

    document.getElementById("modalSubtitle").textContent =
        `${unitCount} Units • ${gearCount} Gear • ${itemCount} Items`;

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

    document.getElementById("inventoryList").innerHTML = html;
    document.getElementById("inventoryModal").classList.add("open");
}

function closeInventory(){
    document.getElementById("inventoryModal").classList.remove("open");
}

document.getElementById("inventoryModal")
    .addEventListener("click", closeInventory);

document.addEventListener("keydown", event => {
    if(event.key === "Escape"){
        closeInventory();
    }
});

async function refresh(){
    try{
        const response = await fetch("/api/accounts", {
            cache:"no-store"
        });

        if(!response.ok){
            throw new Error("Request failed");
        }

        const data = await response.json();
        renderAccounts(data);

    }catch(error){
        document.getElementById("accounts").innerHTML =
            '<div class="empty">Unable to load account data.</div>';
    }
}

refresh();
setInterval(refresh, 2000);
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
