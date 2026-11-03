import os
import uuid
import json
import time
import socket
import random
import urllib.request
from contextvars import ContextVar
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
from jinja2 import ChoiceLoader, FileSystemLoader

# ==============================================================================
# 1. FLASK & SOCKET.IO INITIALIZATION (NGROK & LOCAL READY)
# ==============================================================================
app = Flask(__name__, template_folder=".")
app.config["SECRET_KEY"] = "silicon-valley-startup-battle-secret-2026"

# Support templates in both project root (./) and ./templates/
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(os.path.dirname(os.path.abspath(__file__))),
    FileSystemLoader(os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")),
])

# cors_allowed_origins="*" ensures 100% plug-and-play compatibility with ngrok tunnels
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ==============================================================================
# 2. ROLE DEFINITIONS & METADATA
# ==============================================================================
ROLES_META = {
    "UNASSIGNED": {
        "title": "Очікування розподілу",
        "badge": "Учасник лобі",
        "team": "NEUTRAL",
        "is_shadow": False,
        "description": "Очікуйте, поки ведучий розподілить ролі між стартапами Кремнієвої Долини.",
    },
    "STARTUP": {
        "title": "Етичний Стартапер",
        "badge": "🌱 Етичний Стартапер",
        "team": "LEGAL",
        "is_shadow": False,
        "description": (
            "Ви будуєте прозорий інноваційний бізнес. Вночі ваша компанія відпочиває, "
            "а вдень ви презентуєте свій продукт (Pitch) і голосуєте на фінансовому аудиті, "
            "щоб викрити Рейдерів та Інсайдерів."
        ),
    },
    "AUDITOR": {
        "title": "Податковий Аудитор",
        "badge": "🔍 Податковий Аудитор",
        "team": "LEGAL",
        "is_shadow": False,
        "description": (
            "Вночі ви перевіряєте KYC-досьє одного зі стартапів і миттєво дізнаєтесь, "
            "чи це «Легальний бізнес», чи «Тіньова схема». Допоможіть ринку знайти шахраїв!"
        ),
    },
    "BANKER": {
        "title": "Венчурний Банкір",
        "badge": "🏦 Венчурний Банкір",
        "team": "LEGAL",
        "is_shadow": False,
        "description": (
            "Вночі ви виділяєте антикризовий грант / страховий щит одній компанії. "
            "Якщо Рейдери атакують цю компанію, ваш грант повністю рятує її від втрати 100 🪙 SC!"
        ),
    },
    "RAIDER": {
        "title": "Рейдер / Шахрай",
        "badge": "🏴‍☠️ Рейдер / Шахрай",
        "team": "CARTEL",
        "is_shadow": True,
        "description": (
            "Ви очолюєте тіньовий картель. Вночі обирайте компанію для ворожого поглинання "
            "(-100 🪙 SC). Вдень прикидайтесь чесним підприємцем та уникайте податкового аудиту."
        ),
    },
    "INSIDER": {
        "title": "Інсайдер Картелю",
        "badge": "🕵️ Інсайдер Картелю",
        "team": "CARTEL",
        "is_shadow": True,
        "description": (
            "Ви координуєте цілі тіньового картелю разом із Рейдером та полюєте на Податкового Аудитора. "
            "Якщо Рейдера викриють, ви перебираєте керування нічними поглинаннями."
        ),
    },
}

SAMPLE_BOTS = [
    ("Олександр (AI)", "GreenCharge AI"),
    ("Софія (AI)", "FinPay Junior"),
    ("Максим (AI)", "AgroDrone Tech"),
    ("Дарина (AI)", "CyberShield UA"),
    ("Артем (AI)", "SolarGrid Box"),
    ("Вікторія (AI)", "EduVerse Lab"),
    ("Богдан (AI)", "BioMed Sensor"),
    ("Анна (AI)", "CleanWater Nano"),
    ("Денис (AI)", "NovaRobotics"),
    ("Катерина (AI)", "CloudMint Fin"),
    ("Ілля (AI)", "QuantumPay"),
    ("Юлія (AI)", "EcoSmart Grid"),
]

# ==============================================================================
# 3. MULTI-ROOM IN-MEMORY GAME STATE ENGINE (MAX 25 CONCURRENT SCHOOL SESSIONS)
# ==============================================================================
MAX_ROOMS = 25
ROOM_TTL_SECONDS = 3 * 3600  # Auto-expire rooms inactive for > 3 hours

ROOMS: dict[str, dict] = {}
SID_TO_ROOM: dict[str, str] = {}
_CURRENT_ROOM_CODE: ContextVar[str] = ContextVar("current_room_code", default="")


def make_fresh_room_state(room_code: str) -> dict:
    return {
        "room_code": room_code,
        "created_at": time.time(),
        "last_active": time.time(),
        "phase": "LOBBY",  # LOBBY | NIGHT | DAY | VOTING | RESOLVE
        "round": 0,
        "headline": "Ласкаво просимо до «Кремнієвої Долини: Битва Стартапів»",
        "subheadline": f"Підключайтеся через смартфон або ноутбук (Код кімнати: #{room_code}). Ведучий готує відкриття сесії.",
        "ticker_items": [
            f"🔑 КОД ІГРОВОЇ СЕСІЇ: #{room_code} • Стартовий капітал кожного засновника становить 100 🪙 SC",
            "🛡️ НАГАДУВАННЯ: Венчурний Банкір може захистити один стартап щоночі від ворожого поглинання",
            "🔍 ФІНАНСОВИЙ МОНІТОРИНГ: Викриття Рейдера приносить +20 🪙 SC кожному легальному бізнесу",
            "💡 ТИЖДЕНЬ ФІНАНСОВОЇ ГРАМОТНОСТІ: Інвестуйте розумно та перевіряйте репутацію партнерів (KYC)!",
        ],
        "players": {},  # player_id -> dict
        "night_actions": {
            "raider_target": None,
            "insider_target": None,
            "banker_target": None,
            "auditor_target": None,
            "acted_ids": [],
        },
        "voting_resolved": False,
        "winner": None,
        "cartel_chat": [],
        "logs": [
            f"[{datetime.now().strftime('%H:%M:%S')}] Створено ігрову сесію #{room_code}. Лобі відкрито для реєстрації стартапів."
        ],
    }


_FALLBACK_STATE = make_fresh_room_state("0000")


class _RoomStateProxy:
    """Transparently proxies GAME_STATE[...] operations to the active room dictionary."""

    def _state(self) -> dict:
        code = _CURRENT_ROOM_CODE.get()
        if code and code in ROOMS:
            ROOMS[code]["last_active"] = time.time()
            return ROOMS[code]
        return _FALLBACK_STATE

    def __getitem__(self, key):
        return self._state()[key]

    def __setitem__(self, key, value):
        self._state()[key] = value

    def get(self, key, default=None):
        return self._state().get(key, default)

    def __contains__(self, key):
        return key in self._state()


GAME_STATE = _RoomStateProxy()


def cleanup_expired_rooms():
    """Removes abandoned rooms that have been inactive for over ROOM_TTL_SECONDS."""
    now = time.time()
    expired = [
        code for code, st in list(ROOMS.items())
        if now - st.get("last_active", now) > ROOM_TTL_SECONDS
    ]
    for code in expired:
        ROOMS.pop(code, None)


def generate_unique_room_code() -> str:
    """Generates a unique 4-digit PIN code (1000-9999)."""
    for _ in range(200):
        code = str(random.randint(1000, 9999))
        if code not in ROOMS:
            return code
    return str(random.randint(10000, 99999))


def reset_in_memory_state():
    """Resets only the currently active room without affecting other schools' sessions."""
    code = _CURRENT_ROOM_CODE.get()
    if code and code in ROOMS:
        custom_url = ROOMS[code].get("custom_share_url")
        ROOMS[code] = make_fresh_room_state(code)
        if custom_url:
            ROOMS[code]["custom_share_url"] = custom_url


def activate_room_context(data=None) -> str:
    """Resolves and activates the room_code for the current socket request."""
    cleanup_expired_rooms()
    code = ""
    if isinstance(data, dict) and data.get("room_code"):
        code = str(data.get("room_code")).strip()
    if not code:
        code = SID_TO_ROOM.get(request.sid, "")
    if code and code in ROOMS:
        SID_TO_ROOM[request.sid] = code
        _CURRENT_ROOM_CODE.set(code)
        ROOMS[code]["last_active"] = time.time()
        return code
    return ""


def add_log(message: str):
    timestamp = datetime.now().strftime("%H:%M:%S")
    GAME_STATE["logs"].insert(0, f"[{timestamp}] {message}")
    GAME_STATE["logs"] = GAME_STATE["logs"][:60]


def add_ticker(news: str):
    GAME_STATE["ticker_items"].insert(0, news)
    GAME_STATE["ticker_items"] = GAME_STATE["ticker_items"][:10]


_TUNNEL_CACHE = {"url": None, "ts": 0.0}


def detect_share_play_url() -> str:
    """Automatically detects active ngrok public tunnel URL (via 127.0.0.1:4040) or local LAN Wi-Fi IP."""
    room_code = GAME_STATE.get("room_code", "")
    suffix = f"?room={room_code}" if room_code and room_code != "0000" else ""

    custom = GAME_STATE.get("custom_share_url")
    if custom:
        if suffix and "room=" not in custom:
            return f"{custom}{suffix}"
        return custom

    now = time.time()
    base_url = None
    if _TUNNEL_CACHE["url"] and (now - _TUNNEL_CACHE["ts"] < 4.0):
        base_url = _TUNNEL_CACHE["url"]

    if not base_url:
        # 1. Try local ngrok agent API (port 4040)
        try:
            req = urllib.request.Request("http://127.0.0.1:4040/api/tunnels")
            with urllib.request.urlopen(req, timeout=0.25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                tunnels = data.get("tunnels", [])
                for t in tunnels:
                    pub = t.get("public_url", "")
                    if pub.startswith("https://"):
                        base_url = f"{pub.rstrip('/')}/play"
                        break
                if not base_url and tunnels and tunnels[0].get("public_url"):
                    base_url = f"{tunnels[0]['public_url'].rstrip('/')}/play"
        except Exception:
            pass

    if not base_url:
        # 2. Fallback to local Wi-Fi / LAN IP so smartphones on the same network can scan QR
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.1)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
            base_url = f"http://{lan_ip}:5000/play"
        except Exception:
            base_url = "http://127.0.0.1:5000/play"

    _TUNNEL_CACHE["url"] = base_url
    _TUNNEL_CACHE["ts"] = now
    return f"{base_url}{suffix}"


# ==============================================================================
# 4. ZERO-LEAKAGE STATE SERIALIZERS & ROOM DISPATCH
# ==============================================================================
def get_public_state():
    """Returns strictly public information safe for /display and general viewing."""
    public_players = []
    for pid, p in GAME_STATE["players"].items():
        public_players.append({
            "id": p["id"],
            "name": p["name"],
            "company": p["company"],
            "coins": p["coins"],
            "status": p["status"],  # ACTIVE | UNDER_AUDIT | BANKRUPT
            "status_label": (
                "Працює" if p["status"] == "ACTIVE"
                else ("Під Аудитом" if p["status"] == "UNDER_AUDIT" else "Збанкрутував")
            ),
            "votes_received": p["votes_received"],
            "has_voted": p["voted_for"] is not None,
            "online": p["online"],
            "is_bot": p.get("is_bot", False),
        })

    return {
        "room_code": GAME_STATE.get("room_code", ""),
        "active_rooms_count": len(ROOMS),
        "max_rooms": MAX_ROOMS,
        "phase": GAME_STATE["phase"],
        "round": GAME_STATE["round"],
        "headline": GAME_STATE["headline"],
        "subheadline": GAME_STATE["subheadline"],
        "ticker_items": GAME_STATE["ticker_items"],
        "players": public_players,
        "share_url": detect_share_play_url(),
    }


def get_player_private_state(player_id: str):
    """Returns public state + strictly private data for a single student."""
    base = get_public_state()
    p = GAME_STATE["players"].get(player_id)
    if not p:
        base["me"] = None
        return base

    role_code = p["role"]
    meta = ROLES_META.get(role_code, ROLES_META["UNASSIGNED"])

    # Cartel teammates & private encrypted chat only visible to RAIDER and INSIDER
    cartel_teammates = []
    cartel_chat = []
    if role_code in ("RAIDER", "INSIDER"):
        cartel_chat = GAME_STATE.get("cartel_chat", [])
        for other_id, other in GAME_STATE["players"].items():
            if other["role"] in ("RAIDER", "INSIDER"):
                cartel_teammates.append({
                    "id": other["id"],
                    "name": other["name"],
                    "company": other["company"],
                    "role_title": ROLES_META[other["role"]]["title"],
                })

    base["me"] = {
        "id": p["id"],
        "name": p["name"],
        "company": p["company"],
        "coins": p["coins"],
        "status": p["status"],
        "role": role_code,
        "role_title": meta["title"],
        "role_badge": meta["badge"],
        "role_description": meta["description"],
        "is_shadow": meta["is_shadow"],
        "voted_for": p["voted_for"],
        "night_acted": player_id in GAME_STATE["night_actions"]["acted_ids"],
        "night_target": p.get("last_night_target"),
        "audit_reports": p.get("audit_reports", []),
        "cartel_teammates": cartel_teammates,
        "cartel_chat": cartel_chat,
    }
    return base


def get_admin_state():
    """Returns full omniscient state exclusively for the /admin teacher desk."""
    admin_players = []
    for pid, p in GAME_STATE["players"].items():
        meta = ROLES_META.get(p["role"], ROLES_META["UNASSIGNED"])
        admin_players.append({
            "id": p["id"],
            "name": p["name"],
            "company": p["company"],
            "coins": p["coins"],
            "status": p["status"],
            "role": p["role"],
            "role_title": meta["title"],
            "role_badge": meta["badge"],
            "is_shadow": meta["is_shadow"],
            "votes_received": p["votes_received"],
            "voted_for": p["voted_for"],
            "has_voted": p["voted_for"] is not None,
            "online": p["online"],
            "is_bot": p.get("is_bot", False),
            "night_acted": pid in GAME_STATE["night_actions"]["acted_ids"],
        })

    # Resolve target names for admin preview
    def target_info(tid):
        if not tid or tid not in GAME_STATE["players"]:
            return None
        tp = GAME_STATE["players"][tid]
        return f"{tp['company']} ({tp['name']})"

    na = GAME_STATE["night_actions"]
    return {
        "room_code": GAME_STATE.get("room_code", ""),
        "active_rooms_count": len(ROOMS),
        "max_rooms": MAX_ROOMS,
        "phase": GAME_STATE["phase"],
        "round": GAME_STATE["round"],
        "headline": GAME_STATE["headline"],
        "subheadline": GAME_STATE["subheadline"],
        "ticker_items": GAME_STATE["ticker_items"],
        "players": admin_players,
        "night_summary": {
            "raider_target_id": na["raider_target"],
            "raider_target_name": target_info(na["raider_target"]),
            "insider_target_id": na["insider_target"],
            "insider_target_name": target_info(na["insider_target"]),
            "banker_target_id": na["banker_target"],
            "banker_target_name": target_info(na["banker_target"]),
            "auditor_target_id": na["auditor_target"],
            "auditor_target_name": target_info(na["auditor_target"]),
            "acted_count": len(na["acted_ids"]),
        },
        "cartel_chat": GAME_STATE.get("cartel_chat", []),
        "logs": GAME_STATE["logs"],
        "share_url": detect_share_play_url(),
    }


def broadcast_all():
    """Synchronizes all connected clients in the current room without leaking private data."""
    room_code = _CURRENT_ROOM_CODE.get()
    if not room_code or room_code not in ROOMS:
        return

    # 1. Update projector display room for this session
    socketio.emit("public_state", get_public_state(), to=f"display_{room_code}")

    # 2. Update teacher admin room for this session
    socketio.emit("admin_state", get_admin_state(), to=f"admin_{room_code}")

    # 3. Update each individual player in this session via their private SID
    for pid, p in GAME_STATE["players"].items():
        if p.get("sid") and p.get("online"):
            socketio.emit("player_state", get_player_private_state(pid), to=p["sid"])


# ==============================================================================
# 5. HTTP ROUTES (/admin, /play, /display)
# ==============================================================================
@app.route("/")
def index():
    return redirect(url_for("play_route"))


@app.route("/admin")
def admin_route():
    return render_template("admin.html")


@app.route("/play")
def play_route():
    return render_template("play.html")


@app.route("/display")
def display_route():
    return render_template("display.html")


@app.route("/rules")
def rules_route():
    return render_template("rules_and_cards.html")


# ==============================================================================
# 6. SOCKET.IO CONNECTION & PLAYER HANDLERS
# ==============================================================================
@socketio.on("connect")
def handle_connect():
    pass


@socketio.on("disconnect")
def handle_disconnect():
    sid = request.sid
    room_code = SID_TO_ROOM.pop(sid, "")
    if room_code and room_code in ROOMS:
        _CURRENT_ROOM_CODE.set(room_code)
        for pid, p in GAME_STATE["players"].items():
            if p.get("sid") == sid:
                p["online"] = False
                add_log(f"Учасник {p['company']} ({p['name']}) тимчасово відключився.")
                broadcast_all()
                break


@socketio.on("join_admin")
def handle_join_admin(data=None):
    cleanup_expired_rooms()
    req_code = ""
    force_new = False
    if isinstance(data, dict):
        req_code = str(data.get("room_code") or "").strip()
        force_new = bool(data.get("create_new"))

    prev_code = SID_TO_ROOM.get(request.sid, "")
    if prev_code:
        leave_room(f"admin_{prev_code}")

    # 1. If valid existing room requested and not forcing a brand new session
    if req_code and req_code in ROOMS and not force_new:
        SID_TO_ROOM[request.sid] = req_code
        _CURRENT_ROOM_CODE.set(req_code)
        join_room(f"admin_{req_code}")
        emit("admin_state", get_admin_state())
        return

    # 2. Need to create a new session — check the 25-room capacity limit!
    if len(ROOMS) >= MAX_ROOMS:
        emit("room_capacity_full", {
            "ok": False,
            "active_rooms": len(ROOMS),
            "max_rooms": MAX_ROOMS,
            "message": (
                f"⚠️ Немає вільних місць для нової ігрової сесії! "
                f"Зараз зайнято всі {MAX_ROOMS} із {MAX_ROOMS} одночасних шкільних кімнат. "
                f"Будь ласка, зачекайте завершення однієї з поточних ігор або підключіться до існуючої сесії за її PIN-кодом."
            ),
        })
        return

    new_code = req_code if (req_code.isdigit() and len(req_code) == 4 and req_code not in ROOMS) else generate_unique_room_code()
    ROOMS[new_code] = make_fresh_room_state(new_code)
    SID_TO_ROOM[request.sid] = new_code
    _CURRENT_ROOM_CODE.set(new_code)
    join_room(f"admin_{new_code}")
    emit("admin_toast", {"ok": True, "message": f"🎉 Створено ігрову сесію #{new_code} (Зайнято слотів: {len(ROOMS)}/{MAX_ROOMS})"})
    emit("admin_state", get_admin_state())


@socketio.on("join_display")
def handle_join_display(data=None):
    cleanup_expired_rooms()
    req_code = ""
    if isinstance(data, dict):
        req_code = str(data.get("room_code") or "").strip()

    # If no code passed and exactly 1 room is active on server, auto-attach for convenience
    if not req_code and len(ROOMS) == 1:
        req_code = next(iter(ROOMS.keys()))

    if not req_code or req_code not in ROOMS:
        emit("display_room_required", {
            "active_rooms_count": len(ROOMS),
            "max_rooms": MAX_ROOMS,
            "message": "Введіть 4-значний PIN-код ігрової сесії вчителя:" if ROOMS else "Наразі немає активних ігрових сесій. Відкрийте /admin, щоб створити гру."
        })
        return

    prev_code = SID_TO_ROOM.get(request.sid, "")
    if prev_code:
        leave_room(f"display_{prev_code}")

    SID_TO_ROOM[request.sid] = req_code
    _CURRENT_ROOM_CODE.set(req_code)
    join_room(f"display_{req_code}")
    emit("public_state", get_public_state())


@socketio.on("register_player")
def handle_register_player(data):
    cleanup_expired_rooms()
    player_id = data.get("player_id")
    room_code = str(data.get("room_code") or "").strip()
    name = (data.get("name") or "").strip()
    company = (data.get("company") or "").strip()

    # If reconnecting with a saved player_id and room_code wasn't provided, find which room holds player_id
    if not room_code and player_id:
        for rcode, rstate in ROOMS.items():
            if player_id in rstate["players"]:
                room_code = rcode
                break

    # If still no room_code and only 1 active room exists, auto-select it
    if not room_code and len(ROOMS) == 1:
        room_code = next(iter(ROOMS.keys()))

    if not room_code or room_code not in ROOMS:
        if not name and not company:
            emit("registration_required")
            return
        if len(ROOMS) >= MAX_ROOMS:
            emit("registration_error", {
                "message": f"⚠️ Немає вільних місць для ігрової сесії ({MAX_ROOMS}/{MAX_ROOMS} зайнято), а кімнату «{room_code or '—'}» не знайдено!"
            })
        elif len(ROOMS) == 0:
            emit("registration_error", {
                "message": "⚠️ Жодна ігрова сесія ще не відкрита вчителем! Спочатку ведучий має відкрити /admin."
            })
        else:
            emit("registration_error", {
                "message": f"❌ Кімнату з PIN-кодом «{room_code or '—'}» не знайдено! Перевірте 4 цифри коду на екрані вчителя."
            })
        return

    SID_TO_ROOM[request.sid] = room_code
    _CURRENT_ROOM_CODE.set(room_code)

    # Reconnect existing player if token matches
    if player_id and player_id in GAME_STATE["players"]:
        p = GAME_STATE["players"][player_id]
        p["sid"] = request.sid
        p["online"] = True
        if name:
            p["name"] = name
        if company:
            p["company"] = company
        join_room(f"players_{room_code}")
        emit("player_state", get_player_private_state(player_id))
        broadcast_all()
        return

    # If client only sent a reconnect check without name/company and wasn't found
    if not name or not company:
        emit("registration_required")
        return

    new_id = player_id or f"plr_{uuid.uuid4().hex[:8]}"
    GAME_STATE["players"][new_id] = {
        "id": new_id,
        "sid": request.sid,
        "name": name,
        "company": company,
        "role": "UNASSIGNED",
        "coins": 100,
        "status": "ACTIVE",
        "votes_received": 0,
        "voted_for": None,
        "online": True,
        "is_bot": False,
        "last_night_target": None,
        "audit_reports": [],
    }
    join_room(f"players_{room_code}")
    add_log(f"Зареєстровано стартап «{company}» (Засновник: {name}).")
    add_ticker(f"🚀 НОВИЙ РЕЗИДЕНТ ДОЛИНИ: Стартап «{company}» ({name}) вийшов на ринок із капіталом 100 🪙 SC!")
    emit("registered_success", {"player_id": new_id, "room_code": room_code})
    broadcast_all()


@socketio.on("submit_night_action")
def handle_night_action(data):
    if not activate_room_context(data):
        return
    player_id = data.get("player_id")
    target_id = data.get("target_id")

    if GAME_STATE["phase"] != "NIGHT":
        emit("action_feedback", {"ok": False, "message": "Нічна фаза вже завершена!"})
        return

    player = GAME_STATE["players"].get(player_id)
    target = GAME_STATE["players"].get(target_id)

    if not player or player["status"] == "BANKRUPT":
        emit("action_feedback", {"ok": False, "message": "Збанкрутілі компанії не можуть діяти вночі."})
        return

    if not target or target["status"] == "BANKRUPT":
        emit("action_feedback", {"ok": False, "message": "Оберіть активну компанію зі списку."})
        return

    role = player["role"]
    if role != "BANKER" and player_id == target_id:
        emit("action_feedback", {"ok": False, "message": "Ви не можете обрати власну компанію!"})
        return

    if role in ("RAIDER", "INSIDER") and target["role"] in ("RAIDER", "INSIDER"):
        emit("action_feedback", {"ok": False, "message": "Не можна атакувати спільника по Тіньовому Картелю!"})
        return

    na = GAME_STATE["night_actions"]
    player["last_night_target"] = target_id

    if player_id not in na["acted_ids"]:
        na["acted_ids"].append(player_id)

    if role == "RAIDER":
        na["raider_target"] = target_id
        GAME_STATE["cartel_chat"].append({
            "time": datetime.now().strftime("%H:%M"),
            "sender": f"🏴‍☠️ {player['company']}",
            "text": f"Обрано мішень для поглинання: «{target['company']}» (-100 🪙 SC)",
            "is_system": True,
        })
        add_log(f"🌙 [Ніч] Рейдер «{player['company']}» обрав мішенню «{target['company']}».")
        emit("action_feedback", {
            "ok": True,
            "message": f"Ціль ворожого поглинання зафіксована: «{target['company']}» (-100 🪙 SC)."
        })

    elif role == "INSIDER":
        na["insider_target"] = target_id
        if not na["raider_target"]:
            na["raider_target"] = target_id
        GAME_STATE["cartel_chat"].append({
            "time": datetime.now().strftime("%H:%M"),
            "sender": f"🕵️ {player['company']}",
            "text": f"Пропоную атакувати компанію «{target['company']}»!",
            "is_system": True,
        })
        add_log(f"🌙 [Ніч] Інсайдер «{player['company']}» скоординував ціль на «{target['company']}».")
        emit("action_feedback", {
            "ok": True,
            "message": f"Координацію картелю передано: мішень «{target['company']}»."
        })

    elif role == "BANKER":
        na["banker_target"] = target_id
        add_log(f"🌙 [Ніч] Венчурний Банкір «{player['company']}» надав грантовий щит «{target['company']}».")
        emit("action_feedback", {
            "ok": True,
            "message": f"Антикризовий страховий грант видано для «{target['company']}»."
        })

    elif role == "AUDITOR":
        na["auditor_target"] = target_id
        is_shadow = ROLES_META.get(target["role"], {}).get("is_shadow", False)
        verdict = "Тіньова схема" if is_shadow else "Легальний бізнес"
        report_entry = {
            "round": GAME_STATE["round"],
            "company": target["company"],
            "founder": target["name"],
            "verdict": verdict,
            "is_shadow": is_shadow,
        }
        # Avoid duplicate report in same round
        player["audit_reports"] = [r for r in player["audit_reports"] if r["round"] != GAME_STATE["round"]]
        player["audit_reports"].insert(0, report_entry)

        add_log(f"🌙 [Ніч] Податковий Аудитор перевірив «{target['company']}» ➔ {verdict}.")
        emit("auditor_kyc_result", report_entry)
        emit("action_feedback", {
            "ok": True,
            "message": f"Аудит «{target['company']}» завершено! Результат: {verdict}."
        })

    broadcast_all()


@socketio.on("submit_vote")
def handle_submit_vote(data):
    if not activate_room_context(data):
        return
    player_id = data.get("player_id")
    target_id = data.get("target_id")

    if GAME_STATE["phase"] != "VOTING" or GAME_STATE.get("voting_resolved"):
        emit("action_feedback", {"ok": False, "message": "Голосування наразі закрите."})
        return

    if player_id == target_id:
        emit("action_feedback", {"ok": False, "message": "Не можна голосувати проти власного стартапу!"})
        return

    voter = GAME_STATE["players"].get(player_id)
    target = GAME_STATE["players"].get(target_id)

    if not voter or voter["status"] == "BANKRUPT":
        emit("action_feedback", {"ok": False, "message": "Збанкрутілі компанії втратили право голосу."})
        return

    if not target or target["status"] == "BANKRUPT":
        emit("action_feedback", {"ok": False, "message": "Неможливо голосувати проти неактивної компанії."})
        return

    # Remove previous vote if changing mind
    prev_target_id = voter.get("voted_for")
    if prev_target_id and prev_target_id in GAME_STATE["players"]:
        GAME_STATE["players"][prev_target_id]["votes_received"] = max(
            0, GAME_STATE["players"][prev_target_id]["votes_received"] - 1
        )

    voter["voted_for"] = target_id
    target["votes_received"] += 1

    add_log(f"🗳️ «{voter['company']}» проголосував за аудит компанії «{target['company']}».")
    emit("action_feedback", {
        "ok": True,
        "message": f"Ваш голос проти «{target['company']}» зараховано!"
    })
    broadcast_all()


@socketio.on("send_cartel_message")
def handle_send_cartel_message(data):
    """Encrypted private chat strictly between RAIDER and INSIDER roles."""
    if not activate_room_context(data):
        return
    player_id = data.get("player_id")
    text = (data.get("text") or "").strip()

    if not text:
        return

    sender = GAME_STATE["players"].get(player_id)
    if not sender or sender["role"] not in ("RAIDER", "INSIDER") or sender["status"] == "BANKRUPT":
        return

    icon = "🏴‍☠️" if sender["role"] == "RAIDER" else "🕵️"
    msg_entry = {
        "time": datetime.now().strftime("%H:%M"),
        "sender": f"{icon} {sender['company']} ({sender['name']})",
        "text": text[:200],
        "is_system": False,
    }
    GAME_STATE["cartel_chat"].append(msg_entry)

    # If there is an active AI bot in the Cartel, let it respond tactically
    bot_allies = [
        p for p in GAME_STATE["players"].values()
        if p.get("is_bot") and p["status"] != "BANKRUPT"
        and p["role"] in ("RAIDER", "INSIDER") and p["id"] != player_id
    ]
    if bot_allies:
        ally = bot_allies[0]
        ally_icon = "🏴‍☠️" if ally["role"] == "RAIDER" else "🕵️"
        legal_targets = [
            t for t in GAME_STATE["players"].values()
            if t["status"] != "BANKRUPT" and t["role"] not in ("RAIDER", "INSIDER")
        ]
        sample_target = random.choice(legal_targets)["company"] if legal_targets else "лідера ринку"
        replies = [
            f"Прийнято! Синхронізуємо удар по «{sample_target}» та голосуємо проти них удень.",
            f"Згода! На пітчингу тримаємо легенду легальних інвесторів і наводимо підозру на «{sample_target}».",
            f"Сигнал отримано. Головне — вирахувати Податкового Аудитора, поки він нас не перевірив!",
        ]
        GAME_STATE["cartel_chat"].append({
            "time": datetime.now().strftime("%H:%M"),
            "sender": f"{ally_icon} {ally['company']} [AI]",
            "text": random.choice(replies),
            "is_system": False,
        })

    GAME_STATE["cartel_chat"] = GAME_STATE["cartel_chat"][-40:]
    broadcast_all()


# ==============================================================================
# 7. ADMIN CONTROL DESK HANDLERS (ROLE BALANCER, PHASES, RESOLVER, ECONOMY)
# ==============================================================================
@socketio.on("admin_distribute_roles")
def handle_distribute_roles(data=None):
    if not activate_room_context(data):
        return
    players_list = list(GAME_STATE["players"].values())
    count = len(players_list)
    if count == 0:
        emit("admin_toast", {"ok": False, "message": "Немає підключених учасників у лобі!"})
        return

    # Role Balancer logic based on player count
    # Always includes Startups, Raider(s), Auditor, Banker, and Insider for larger rooms
    roles_pool = []
    if count == 1:
        roles_pool = ["RAIDER"]
    elif count == 2:
        roles_pool = ["RAIDER", "AUDITOR"]
    elif count == 3:
        roles_pool = ["RAIDER", "AUDITOR", "BANKER"]
    elif count == 4:
        roles_pool = ["RAIDER", "AUDITOR", "BANKER", "STARTUP"]
    elif count == 5:
        roles_pool = ["RAIDER", "INSIDER", "AUDITOR", "BANKER", "STARTUP"]
    elif count <= 8:
        roles_pool = ["RAIDER", "INSIDER", "AUDITOR", "BANKER"]
        roles_pool.extend(["STARTUP"] * (count - len(roles_pool)))
    else:
        # 9+ players: 2 Raiders + 1 Insider + 1 Auditor + 1 Banker + Startups
        roles_pool = ["RAIDER", "RAIDER", "INSIDER", "AUDITOR", "BANKER"]
        roles_pool.extend(["STARTUP"] * (count - len(roles_pool)))

    random.shuffle(roles_pool)

    for p, assigned_role in zip(players_list, roles_pool):
        p["role"] = assigned_role
        p["coins"] = 100
        p["status"] = "ACTIVE"
        p["votes_received"] = 0
        p["voted_for"] = None
        p["last_night_target"] = None
        p["audit_reports"] = []

    GAME_STATE["round"] = 0
    GAME_STATE["voting_resolved"] = False
    GAME_STATE["winner"] = None
    GAME_STATE["night_actions"] = {
        "raider_target": None,
        "insider_target": None,
        "banker_target": None,
        "auditor_target": None,
        "acted_ids": [],
    }
    GAME_STATE["cartel_chat"] = [
        {
            "time": datetime.now().strftime("%H:%M"),
            "sender": "🔒 Шифр-сервер Картелю",
            "text": "Таємний канал активовано. Координуйте нічні цілі та захищайте один одного на голосуванні!",
            "is_system": True,
        }
    ]

    GAME_STATE["headline"] = "Ролі розподілено! Торговельна сесія розпочинається"
    GAME_STATE["subheadline"] = (
        f"У грі зареєстровано {count} компаній. Кожен засновник отримав стартові 100 🪙 SC "
        "та секретну картку ролі у своєму терміналі."
    )
    add_log(f"⚖️ Ролі автоматично збалансовано та розподілено для {count} учасників.")
    add_ticker(f"🔔 СТАРТ СЕСІЇ: {count} стартапів отримали ліцензії та по 100 🪙 SC стартового капіталу!")
    emit("admin_toast", {"ok": True, "message": f"Ролі успішно розподілено між {count} гравцями!"})
    broadcast_all()


def simulate_bot_actions_for_phase(phase_name: str):
    """Smart AI Engine for bots: tactical night targeting, KYC memory, cartel coordination, and logical day voting."""
    active_players = [p for p in GAME_STATE["players"].values() if p["status"] != "BANKRUPT"]
    if len(active_players) < 2:
        return

    if phase_name == "NIGHT":
        na = GAME_STATE["night_actions"]
        # 1. Cartel Bots (RAIDER / INSIDER) coordinate on high-value legal startups
        for p in active_players:
            if not p.get("is_bot"):
                continue
            role = p["role"]
            if role in ("RAIDER", "INSIDER"):
                legal_targets = [t for t in active_players if t["role"] not in ("RAIDER", "INSIDER")]
                if not legal_targets:
                    continue
                # Prefer already-coordinated cartel target, otherwise target a vulnerable (<=100 SC) or richest startup
                coordinated_id = na["raider_target"] or na["insider_target"]
                if coordinated_id and any(t["id"] == coordinated_id for t in legal_targets):
                    target = next(t for t in legal_targets if t["id"] == coordinated_id)
                else:
                    # Sort candidates: prioritize those who can be bankrupted in one hit (<=100 SC), then highest coins
                    vulnerable = [t for t in legal_targets if t["coins"] <= 100]
                    pool = vulnerable if vulnerable else legal_targets
                    target = random.choice(pool)

                p["last_night_target"] = target["id"]
                if role == "RAIDER" and not na["raider_target"]:
                    na["raider_target"] = target["id"]
                    GAME_STATE["cartel_chat"].append({
                        "time": datetime.now().strftime("%H:%M"),
                        "sender": f"🏴‍☠️ {p['company']} [AI]",
                        "text": f"Фіксую нічну атаку на «{target['company']}» (Баланс: {target['coins']} 🪙 SC). Вибиваємо конкурента!",
                        "is_system": False,
                    })
                    add_log(f"🌙 [Ніч • AI] Рейдер-бот «{p['company']}» обрав мішенню «{target['company']}».")
                elif role == "INSIDER" and not na["insider_target"]:
                    na["insider_target"] = target["id"]
                    if not na["raider_target"]:
                        na["raider_target"] = target["id"]
                    GAME_STATE["cartel_chat"].append({
                        "time": datetime.now().strftime("%H:%M"),
                        "sender": f"🕵️ {p['company']} [AI]",
                        "text": f"Розвідка Картелю: рекомендую вдарити по «{target['company']}» та прикрити своїх удень.",
                        "is_system": False,
                    })
                    add_log(f"🌙 [Ніч • AI] Інсайдер-бот «{p['company']}» навів картель на «{target['company']}».")

                if p["id"] not in na["acted_ids"]:
                    na["acted_ids"].append(p["id"])

            elif role == "BANKER" and not na["banker_target"]:
                # Smart Banker: prioritize companies with <= 100 SC or highest capital at risk
                at_risk = [t for t in active_players if t["coins"] <= 100]
                target = random.choice(at_risk if at_risk else active_players)
                na["banker_target"] = target["id"]
                p["last_night_target"] = target["id"]
                if p["id"] not in na["acted_ids"]:
                    na["acted_ids"].append(p["id"])
                add_log(f"🌙 [Ніч • AI] Банкір-бот «{p['company']}» видав страховий щит для «{target['company']}».")

            elif role == "AUDITOR" and not na["auditor_target"]:
                # Smart Auditor: never audits self or previously audited companies if unverified ones remain
                already_audited = {r["company"] for r in p.get("audit_reports", [])}
                unaudited = [
                    t for t in active_players
                    if t["id"] != p["id"] and t["company"] not in already_audited
                ]
                others = [t for t in active_players if t["id"] != p["id"]]
                candidates = unaudited if unaudited else others
                if candidates:
                    target = random.choice(candidates)
                    na["auditor_target"] = target["id"]
                    p["last_night_target"] = target["id"]
                    is_shadow = ROLES_META.get(target["role"], {}).get("is_shadow", False)
                    verdict = "Тіньова схема" if is_shadow else "Легальний бізнес"
                    p.setdefault("audit_reports", []).insert(0, {
                        "round": GAME_STATE["round"],
                        "company": target["company"],
                        "founder": target["name"],
                        "verdict": verdict,
                        "is_shadow": is_shadow,
                        "target_id": target["id"],
                    })
                    if p["id"] not in na["acted_ids"]:
                        na["acted_ids"].append(p["id"])
                    add_log(f"🌙 [Ніч • AI] Аудитор-бот «{p['company']}» перевірив «{target['company']}» ➔ {verdict}.")

    elif phase_name == "VOTING":
        for p in active_players:
            if not p.get("is_bot") or p.get("voted_for"):
                continue
            others = [t for t in active_players if t["id"] != p["id"]]
            if not others:
                continue

            role = p["role"]
            target = None

            if role == "AUDITOR":
                # 1. Vote against known active shadow cartel member discovered at night!
                known_shadow_ids = {
                    r.get("target_id") for r in p.get("audit_reports", []) if r.get("is_shadow")
                }
                known_legal_ids = {
                    r.get("target_id") for r in p.get("audit_reports", []) if not r.get("is_shadow")
                }
                confirmed_targets = [t for t in others if t["id"] in known_shadow_ids]
                if confirmed_targets:
                    target = confirmed_targets[0]
                else:
                    # Pick someone not proven legal, prioritizing UNDER_AUDIT
                    suspects = [t for t in others if t["id"] not in known_legal_ids] or others
                    under_audit = [t for t in suspects if t["status"] == "UNDER_AUDIT"]
                    target = random.choice(under_audit if under_audit else suspects)

            elif role in ("RAIDER", "INSIDER"):
                # Cartel bots NEVER vote against fellow cartel members!
                legal_others = [t for t in others if t["role"] not in ("RAIDER", "INSIDER")]
                pool = legal_others if legal_others else others
                # Bandwagon on legal startups under audit or already receiving votes
                flagged = [t for t in pool if t["status"] == "UNDER_AUDIT" or t["votes_received"] > 0]
                target = random.choice(flagged if flagged and random.random() < 0.75 else pool)

            else:
                # Ethical Startup / Venture Banker: 65% chance to scrutinize UNDER_AUDIT or leading suspect
                flagged = [t for t in others if t["status"] == "UNDER_AUDIT" or t["votes_received"] > 0]
                target = random.choice(flagged if flagged and random.random() < 0.65 else others)

            if target:
                p["voted_for"] = target["id"]
                target["votes_received"] += 1
                add_log(f"🗳️ [AI] «{p['company']}» проголосував проти «{target['company']}».")


@socketio.on("admin_set_phase")
def handle_set_phase(data):
    if not activate_room_context(data):
        return
    prev_phase = GAME_STATE.get("phase", "LOBBY")
    new_phase = data.get("phase", "LOBBY")
    GAME_STATE["phase"] = new_phase

    if new_phase == "LOBBY":
        GAME_STATE["headline"] = "Лобі реєстрації стартапів"
        GAME_STATE["subheadline"] = "Очікуємо приєднання всіх учасників до торговельної сесії."
        add_log("🔄 Фазу переключено на LOBBY.")

    elif new_phase == "NIGHT":
        if GAME_STATE["round"] == 0 or prev_phase == "LOBBY":
            GAME_STATE["round"] = 1
        elif prev_phase in ("DAY", "VOTING", "RESOLVE"):
            GAME_STATE["round"] += 1
        GAME_STATE["voting_resolved"] = False
        # Reset night actions and clear previous day UNDER_AUDIT warning badges for active companies
        GAME_STATE["night_actions"] = {
            "raider_target": None,
            "insider_target": None,
            "banker_target": None,
            "auditor_target": None,
            "acted_ids": [],
        }
        for p in GAME_STATE["players"].values():
            p["last_night_target"] = None
            p["voted_for"] = None
            p["votes_received"] = 0
            if p["status"] == "UNDER_AUDIT":
                p["status"] = "ACTIVE"

        simulate_bot_actions_for_phase("NIGHT")
        GAME_STATE["headline"] = f"🌙 Ніч у Кремнієвій Долині (Раунд {GAME_STATE['round']})"
        GAME_STATE["subheadline"] = (
            "Фондова біржа зачинена. Тіньовий картель, Венчурний Банкір та Податковий Аудитор "
            "проводять таємні транзакції у своїх терміналах."
        )
        add_log(f"🌙 Розпочато НІЧ (Раунд {GAME_STATE['round']}).")
        add_ticker(f"🌙 НІЧНА СЕСІЯ (Раунд {GAME_STATE['round']}): Венчурний Банкір та Податковий Аудитор вийшли на чергування.")

    elif new_phase == "DAY":
        GAME_STATE["headline"] = f"☀️ День Пітчингу та Ринкових Новин (Раунд {GAME_STATE['round']})"
        if not GAME_STATE["subheadline"] or "Фондова біржа зачинена" in GAME_STATE["subheadline"]:
            GAME_STATE["subheadline"] = (
                "Засновники презентують свої стартапи (1 хвилина на пітч) та обговорюють "
                "ранкові фінансові новини."
            )
        add_log(f"☀️ Розпочато ДЕНЬ ПІТЧИНГУ (Раунд {GAME_STATE['round']}).")

    elif new_phase == "VOTING":
        GAME_STATE["voting_resolved"] = False
        for p in GAME_STATE["players"].values():
            p["voted_for"] = None
            p["votes_received"] = 0
        simulate_bot_actions_for_phase("VOTING")
        GAME_STATE["headline"] = f"🗳️ Фінансовий Аудит: Голосування (Раунд {GAME_STATE['round']})"
        GAME_STATE["subheadline"] = (
            "Активні засновники голосують у своїх терміналах проти компанії, "
            "яку підозрюють у рейдерстві та фінансових махінаціях."
        )
        add_log(f"🗳️ Відкрито ГОЛОСУВАННЯ (Раунд {GAME_STATE['round']}).")
        add_ticker("⚖️ УВАГА: Відкрито голосування Ради директорів! Оберіть підозрілу компанію для примусового аудиту.")

    elif new_phase == "RESOLVE":
        resolve_voting_outcome()

    broadcast_all()


@socketio.on("admin_resolve_night")
def handle_resolve_night(data=None):
    """Night Resolver Engine: compares Raider takeover vs Banker shield, logs Auditor KYC, generates Morning News."""
    if not activate_room_context(data):
        return
    if GAME_STATE["phase"] != "NIGHT":
        emit("admin_toast", {"ok": False, "message": "Ніч вже розрахована! Спочатку увімкніть фазу «Крок 2: 🌙 НІЧ»."})
        return

    na = GAME_STATE["night_actions"]
    attack_target_id = na["raider_target"] or na["insider_target"]
    shield_target_id = na["banker_target"]
    audit_target_id = na["auditor_target"]

    news_parts = []

    # 1. Process Auditor KYC flag
    if audit_target_id and audit_target_id in GAME_STATE["players"]:
        audited_plr = GAME_STATE["players"][audit_target_id]
        if audited_plr["status"] == "ACTIVE":
            audited_plr["status"] = "UNDER_AUDIT"
        news_parts.append(
            f"Податковий Аудитор провів нічну KYC-перевірку документації компанії «{audited_plr['company']}»."
        )

    # 2. Process Raider Attack vs Banker Shield
    if attack_target_id and attack_target_id in GAME_STATE["players"]:
        victim = GAME_STATE["players"][attack_target_id]
        if attack_target_id == shield_target_id:
            # Saved by Venture Banker!
            GAME_STATE["headline"] = f"🛡️ Грантовий Порятунок! «{victim['company']}» відбиває рейдерську атаку!"
            news_parts.insert(
                0,
                f"Тіньовий картель намагався здійснити вороже поглинання стартапу «{victim['company']}» "
                f"({victim['name']}), але Венчурний Банкір вчасно надав антикризовий страховий грант! "
                f"Капітал компанії збережено на рівні {victim['coins']} 🪙 SC."
            )
            add_log(f"🛡️ ГРАНТОВИЙ ЩИТ: Банкір врятував «{victim['company']}» від атаки Рейдера!")
            add_ticker(f"🛡️ РЯТУВАЛЬНИЙ ГРАНТ: Венчурний Банкір заблокував вороже поглинання компанії «{victim['company']}»!")
        else:
            # Successful Hostile Takeover (-100 SC)
            victim["coins"] -= 100
            if victim["coins"] <= 0:
                victim["coins"] = 0
                victim["status"] = "BANKRUPT"
                GAME_STATE["headline"] = f"📉 Вороже Поглинання! Компанія «{victim['company']}» оголосила банкрутство"
                news_parts.insert(
                    0,
                    f"Вночі рейдери здійснили агресивну атаку на стартап «{victim['company']}» ({victim['name']}), "
                    f"вивівши 100 🪙 SC. Через нульовий баланс компанія офіційно визнана банкрутом і втрачає право голосу."
                )
                add_log(f"💥 БАНКРУТСТВО: Рейдери списали 100 🪙 SC у «{victim['company']}». Компанія збанкрутувала.")
                add_ticker(f"🚨 БАНКРУТСТВО: Внаслідок нічної рейдерської атаки компанія «{victim['company']}» втратила 100 🪙 SC!")
            else:
                victim["status"] = "UNDER_AUDIT"
                GAME_STATE["headline"] = f"⚠️ Рейдерський удар по «{victim['company']}» (-100 🪙 SC)!"
                news_parts.insert(
                    0,
                    f"Вночі рейдери атакували «{victim['company']}» ({victim['name']}) на 100 🪙 SC! "
                    f"Завдяки накопиченим резервам компанія вистояла (залишок: {victim['coins']} 🪙 SC)."
                )
                add_log(f"⚠️ АТАКА: «{victim['company']}» втратила 100 🪙 SC, але втрималась на плаву ({victim['coins']} SC).")
                add_ticker(f"⚠️ ФІНАНСОВИЙ УДАР: «{victim['company']}» втратила 100 🪙 SC через атаку рейдерів, але продовжує роботу!")
    else:
        GAME_STATE["headline"] = f"☀️ Спокійний Ранок на Біржі (Раунд {GAME_STATE['round']})"
        news_parts.insert(
            0,
            "Ніч минула без ворожих поглинань. Жоден стартап не зазнав втрат капіталу під час нічної сесії."
        )
        add_log("☀️ Ніч завершено без рейдерських атак.")

    # Clear night targets after resolution to prevent accidental double deduction
    GAME_STATE["night_actions"] = {
        "raider_target": None,
        "insider_target": None,
        "banker_target": None,
        "auditor_target": None,
        "acted_ids": [],
    }

    GAME_STATE["subheadline"] = " ".join(news_parts)
    GAME_STATE["phase"] = "DAY"
    check_win_condition()
    emit("admin_toast", {"ok": True, "message": "Нічні підсумки розраховано! Оголошено ранкові новини."})
    broadcast_all()


def check_win_condition():
    """Checks if either Legal Market or Shadow Cartel has achieved victory."""
    players = list(GAME_STATE["players"].values())
    if not players or all(p["role"] == "UNASSIGNED" for p in players):
        return False

    active = [p for p in players if p["status"] != "BANKRUPT"]
    active_shadow = [p for p in active if ROLES_META.get(p["role"], {}).get("is_shadow", False)]
    active_legal = [p for p in active if not ROLES_META.get(p["role"], {}).get("is_shadow", False)]

    if len(active_shadow) == 0 and len(active_legal) > 0:
        GAME_STATE["winner"] = "LEGAL"
        mvp = max(active_legal, key=lambda x: x["coins"])
        GAME_STATE["headline"] = "🏆 ПЕРЕМОГА ЧЕСНОГО РИНКУ! Усіх учасників картелю викрито!"
        GAME_STATE["subheadline"] = (
            f"Кремнієва Долина очищена від фінансових махінацій! "
            f"Титул найуспішнішого стартапу здобуває «{mvp['company']}» ({mvp['name']}) "
            f"із капіталом {mvp['coins']} 🪙 SC!"
        )
        add_log(f"🏆 ГРУ ЗАВЕРШЕНО: Перемога Чесного Ринку! MVP: «{mvp['company']}» ({mvp['coins']} SC).")
        add_ticker(f"🏆 ПЕРЕМОГА ЧЕСНОГО РИНКУ! Лідер капіталізації: «{mvp['company']}» ({mvp['coins']} 🪙 SC)!")
        return True

    if len(active_shadow) > 0 and len(active_shadow) >= len(active_legal):
        GAME_STATE["winner"] = "CARTEL"
        cartel_names = ", ".join([f"«{p['company']}» ({p['name']})" for p in active_shadow])
        GAME_STATE["headline"] = "🏴‍☠️ ПЕРЕМОГА ТІНЬОВОГО КАРТЕЛЮ! Ринок монополізовано!"
        GAME_STATE["subheadline"] = (
            f"Кількість учасників картелю зрівнялася з чесними бізнесами. "
            f"Контрольний пакет акцій Долини захопили: {cartel_names}!"
        )
        add_log(f"🏴‍☠️ ГРУ ЗАВЕРШЕНО: Перемога Тіньового Картелю ({cartel_names}).")
        add_ticker(f"🏴‍☠️ ТІНЬОВИЙ КАРТЕЛЬ ПЕРЕМІГ! Ринок перейшов під контроль: {cartel_names}!")
        return True

    return False


def resolve_voting_outcome():
    """Tallies day votes and liquidates the top suspect if a strict majority/plurality exists."""
    if GAME_STATE.get("voting_resolved"):
        emit("admin_toast", {"ok": False, "message": "Підсумки цього голосування вже підбито!"})
        return

    GAME_STATE["voting_resolved"] = True
    active_players = [p for p in GAME_STATE["players"].values()]
    if not active_players:
        return

    sorted_by_votes = sorted(active_players, key=lambda x: x["votes_received"], reverse=True)
    top = sorted_by_votes[0]

    if top["votes_received"] == 0:
        GAME_STATE["headline"] = "⚖️ Підсумки Раунду: Рішення не прийнято"
        GAME_STATE["subheadline"] = "Жодна компанія не отримала голосів під час аудиту. Усі стартапи продовжують діяльність."
        add_log("⚖️ Голосування завершилось без жодного голосу.")
        return

    # Check tie
    tied = [p for p in active_players if p["votes_received"] == top["votes_received"]]
    if len(tied) > 1:
        names = ", ".join([f"«{p['company']}»" for p in tied])
        GAME_STATE["headline"] = "⚖️ Паритет голосів на Раді директорів!"
        GAME_STATE["subheadline"] = (
            f"Компанії {names} набрали однакову кількість голосів ({top['votes_received']}). "
            "Через відсутність переваги жоден стартап не був ліквідований."
        )
        add_log(f"⚖️ Нічия у голосуванні між: {names} ({top['votes_received']} гол.).")
        add_ticker(f"⚖️ НІЧИЯ НА АУДИТІ: Компанії {names} розділили голоси порівну. Ліквідацію скасовано!")
        return

    # Unique top-voted suspect is liquidated
    top["coins"] = 0
    top["status"] = "BANKRUPT"
    role_meta = ROLES_META.get(top["role"], ROLES_META["UNASSIGNED"])
    is_shadow = role_meta["is_shadow"]

    if is_shadow:
        GAME_STATE["headline"] = f"🎯 Шахраїв Викрито! Ліквідовано «{top['company']}» ({role_meta['title']})"
        GAME_STATE["subheadline"] = (
            f"За рішенням інвесторів ({top['votes_received']} гол.) примусово ліквідовано компанію "
            f"«{top['company']}» ({top['name']}). Перевірка підтвердила роль: {role_meta['title']}! "
            "Усі легальні бізнеси мають право на дивідендний бонус +20 🪙 SC!"
        )
        add_log(f"🎯 УСПІШНИЙ АУДИТ: Викрито «{top['company']}» — {role_meta['title']}!")
        add_ticker(f"🎉 КАРТЕЛЬ ВИКРИТО: «{top['company']}» виявився учасником тіньової схеми ({role_meta['title']})!")
    else:
        GAME_STATE["headline"] = f"⚠️ Помилка Аудиту! Закрито легальний стартап «{top['company']}»"
        GAME_STATE["subheadline"] = (
            f"За більшістю голосів ({top['votes_received']} гол.) закрито компанію «{top['company']}» ({top['name']}). "
            f"На жаль, це був чесний резидент Долини ({role_meta['title']}). Тіньовий картель залишається у грі!"
        )
        add_log(f"⚠️ ПОМИЛКА ІНВЕСТОРІВ: Ліквідовано легальний бізнес «{top['company']}» ({role_meta['title']}).")
        add_ticker(f"📉 СУДОВА ПОМИЛКА: Ліквідовано чесну компанію «{top['company']}» ({role_meta['title']})!")

    check_win_condition()


@socketio.on("admin_economy_action")
def handle_economy_action(data):
    if not activate_room_context(data):
        return
    action_type = data.get("type")

    if action_type == "passive_yield":
        # +10 SC to all active startups
        count = 0
        for p in GAME_STATE["players"].values():
            if p["status"] != "BANKRUPT":
                p["coins"] += 10
                count += 1
        add_log(f"💰 Нараховано базовий грошовий потік +10 🪙 SC для {count} активних компаній.")
        add_ticker(f"💸 РАНКОВІ ДИВІДЕНДИ: Усі активні стартапи ({count}) отримали +10 🪙 SC операційного прибутку!")
        emit("admin_toast", {"ok": True, "message": f"Нараховано +10 🪙 SC для {count} активних компаній!"})

    elif action_type == "audit_bounty":
        # +20 SC to all active legal businesses when catching a fraudster
        count = 0
        for p in GAME_STATE["players"].values():
            is_shadow = ROLES_META.get(p["role"], {}).get("is_shadow", False)
            if p["status"] != "BANKRUPT" and not is_shadow:
                p["coins"] += 20
                count += 1
        add_log(f"🏆 Виплачено премію за викриття шахраїв +20 🪙 SC для {count} легальних бізнесів.")
        add_ticker(f"🏆 БОНУС ЗА ПРОЗОРІСТЬ: {count} легальних компаній отримали по +20 🪙 SC за викриття учасника картелю!")
        emit("admin_toast", {"ok": True, "message": f"Виплачено бонус +20 🪙 SC ({count} легальним бізнесам)!"})

    elif action_type == "adjust_player":
        pid = data.get("player_id")
        delta = int(data.get("delta", 0))
        if pid in GAME_STATE["players"]:
            p = GAME_STATE["players"][pid]
            p["coins"] = max(0, p["coins"] + delta)
            if p["coins"] <= 0:
                p["status"] = "BANKRUPT"
            elif p["status"] == "BANKRUPT" and p["coins"] > 0:
                p["status"] = "ACTIVE"
            sign = "+" if delta >= 0 else ""
            add_log(f"🪙 Баланс «{p['company']}» змінено на {sign}{delta} SC (Поточний: {p['coins']} SC).")

    broadcast_all()


@socketio.on("admin_manage_player")
def handle_manage_player(data):
    room_code = activate_room_context(data)
    if not room_code:
        return
    cmd = data.get("command")
    pid = data.get("player_id")

    if cmd == "kick" and pid in GAME_STATE["players"]:
        removed = GAME_STATE["players"].pop(pid)
        add_log(f"❌ Видалено учасника «{removed['company']}» ({removed['name']}).")
        if removed.get("sid"):
            socketio.emit("kicked_from_session", to=removed["sid"])

    elif cmd == "rename" and pid in GAME_STATE["players"]:
        new_company = (data.get("company") or "").strip()
        new_name = (data.get("name") or "").strip()
        p = GAME_STATE["players"][pid]
        if new_company:
            p["company"] = new_company
        if new_name:
            p["name"] = new_name
        add_log(f"✏️ Перейменовано учасника на «{p['company']}» ({p['name']}).")

    elif cmd == "toggle_status" and pid in GAME_STATE["players"]:
        p = GAME_STATE["players"][pid]
        if p["status"] == "BANKRUPT":
            p["status"] = "ACTIVE"
            if p["coins"] <= 0:
                p["coins"] = 100
            add_log(f"♻️ Відновлено платоспроможність компанії «{p['company']}».")
        elif p["status"] == "ACTIVE":
            p["status"] = "UNDER_AUDIT"
        else:
            p["status"] = "ACTIVE"

    elif cmd in ("add_bots", "add_one_bot"):
        limit = 1 if cmd == "add_one_bot" else int(data.get("count", 5))
        existing_companies = {p["company"] for p in GAME_STATE["players"].values()}
        added = 0
        for founder_name, comp_name in SAMPLE_BOTS:
            if comp_name not in existing_companies and added < limit:
                bid = f"bot_{uuid.uuid4().hex[:6]}"
                GAME_STATE["players"][bid] = {
                    "id": bid,
                    "sid": None,
                    "name": founder_name,
                    "company": comp_name,
                    "role": "UNASSIGNED",
                    "coins": 100,
                    "status": "ACTIVE",
                    "votes_received": 0,
                    "voted_for": None,
                    "online": True,
                    "is_bot": True,
                    "last_night_target": None,
                    "audit_reports": [],
                }
                existing_companies.add(comp_name)
                added += 1
        if added > 0:
            add_log(f"🤖 Додано розумних AI-ботів у лобі: +{added} шт.")
            emit("admin_toast", {"ok": True, "message": f"🤖 Додано розумних AI-ботів: {added} шт.!"})
        else:
            emit("admin_toast", {"ok": False, "message": "Усі доступні AI-боти з пулу вже у грі!"})

    elif cmd == "remove_bots":
        bot_ids = [pid for pid, p in GAME_STATE["players"].items() if p.get("is_bot")]
        for bid in bot_ids:
            GAME_STATE["players"].pop(bid, None)
        add_log(f"🧹 Видалено всіх AI-ботів ({len(bot_ids)} шт.).")
        emit("admin_toast", {"ok": True, "message": f"Видалено ботів: {len(bot_ids)} шт."})

    elif cmd == "set_share_url":
        raw_url = (data.get("url") or "").strip()
        if raw_url:
            if not raw_url.endswith("/play") and "/play?" not in raw_url:
                raw_url = f"{raw_url.rstrip('/')}/play"
            GAME_STATE["custom_share_url"] = raw_url
            emit("admin_toast", {"ok": True, "message": f"Оновлено публічне посилання: {raw_url}"})
        else:
            GAME_STATE["custom_share_url"] = None
            _TUNNEL_CACHE["ts"] = 0.0
            emit("admin_toast", {"ok": True, "message": "Увімкнено авто-визначення посилання ngrok / LAN."})

    elif cmd == "reset_game":
        reset_in_memory_state()
        socketio.emit("session_reset", to=f"players_{room_code}")
        emit("admin_toast", {"ok": True, "message": f"Сесію #{room_code} очищено! Лобі перезапущено."})

    elif cmd == "close_room":
        socketio.emit("session_reset", to=f"players_{room_code}")
        ROOMS.pop(room_code, None)
        SID_TO_ROOM.pop(request.sid, None)
        emit("room_closed", {
            "room_code": room_code,
            "active_rooms": len(ROOMS),
            "max_rooms": MAX_ROOMS,
            "message": f"Сесію #{room_code} завершено. Слот вивільнено ({len(ROOMS)}/{MAX_ROOMS} активних).",
        })
        return

    broadcast_all()


# ==============================================================================
# 8. ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    print("=" * 72)
    print("🌱 КРЕМНІЄВА ДОЛИНА: БИТВА СТАРТАПІВ (Silicon Valley: Startup Battle)")
    print("   Тиждень фінансової грамотності (5–9 класи)")
    print("-" * 72)
    print("👩‍🏫 Панель вчителя (Admin):      http://127.0.0.1:5000/admin")
    print("📱 Термінал учня (Play):        http://127.0.0.1:5000/play")
    print("🖥️ Проєктор / Meet (Display):   http://127.0.0.1:5000/display")
    print("=" * 72)
    port = int(os.environ.get("PORT", 5000))
    debug_mode = os.environ.get("RENDER") is None
    socketio.run(app, host="0.0.0.0", port=port, debug=debug_mode, allow_unsafe_werkzeug=True)
