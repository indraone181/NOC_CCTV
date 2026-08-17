from dotenv import load_dotenv
load_dotenv()

import asyncio
import csv
import io
import os
import secrets
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
import requests

import bcrypt
import jwt
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field
from pymongo import UpdateOne

mongo = AsyncIOMotorClient(os.environ["MONGO_URL"])
db = mongo[os.environ["DB_NAME"]]
JWT_SECRET = os.environ["JWT_SECRET"]
ALGORITHM = "HS256"
api = APIRouter(prefix="/api")

class LoginInput(BaseModel):
    email: str
    password: str = Field(min_length=8)

class SetupInput(LoginInput):
    name: str = "NOC Administrator"

class CameraInput(BaseModel):
    id: Optional[str] = None
    name: str
    ip: str
    nvr: str
    picture_url: str = ""
    location: str = "Main Site"

class UserInput(BaseModel):
    email: str
    password: str = Field(min_length=8)
    name: str
    role: str = "operator"


class PingInput(BaseModel):
    ip: str
    port: Optional[int] = None

class SettingsInput(BaseModel):
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_enabled: bool = False
    alert_threshold_minutes: int = 5

class TelegramTestInput(BaseModel):
    message: str = ""

async def get_settings():
    doc = await db.settings.find_one({"id": "global"}, {"_id": 0})
    return doc or {"id": "global", "telegram_bot_token": "", "telegram_chat_id": "", "telegram_enabled": False, "alert_threshold_minutes": 5}

async def send_telegram(text, token=None, chat_id=None):
    if not token or not chat_id:
        s = await get_settings()
        token = token or s.get("telegram_bot_token")
        chat_id = chat_id or s.get("telegram_chat_id")
    if not token or not chat_id:
        raise HTTPException(400, "Telegram belum dikonfigurasi")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = await asyncio.to_thread(requests.post, url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=6)
    if resp.status_code >= 400:
        raise HTTPException(400, f"Telegram error: {resp.text[:200]}")
    return resp.json()

async def process_alerts_and_history(items):
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    settings = await get_settings()
    telegram_ok = settings.get("telegram_enabled") and settings.get("telegram_bot_token") and settings.get("telegram_chat_id")
    threshold = float(settings.get("alert_threshold_minutes", 5))
    daily_ops, cam_ops, alerts = [], [], []
    for item in items:
        is_online = 1 if item.get("status") == "online" else 0
        daily_ops.append(UpdateOne(
            {"camera_id": item["id"], "day": day},
            {"$inc": {"total": 1, "online": is_online}, "$setOnInsert": {"camera_id": item["id"], "camera_name": item.get("name"), "day": day}},
            upsert=True,
        ))
        offline_since = item.get("offline_since")
        alert_sent_at = item.get("alert_sent_at")
        update_set, update_unset = {}, {}
        if item.get("status") == "offline":
            if not offline_since:
                offline_since = now.isoformat()
                update_set["offline_since"] = offline_since
                item["offline_since"] = offline_since
            try:
                elapsed_min = (now - datetime.fromisoformat(offline_since)).total_seconds() / 60
            except Exception:
                elapsed_min = 0
            if telegram_ok and elapsed_min >= threshold and (not alert_sent_at or alert_sent_at < offline_since):
                alerts.append(("down", item))
                update_set["alert_sent_at"] = now.isoformat()
        else:
            if offline_since or alert_sent_at:
                if telegram_ok and offline_since:
                    alerts.append(("up", item))
                update_unset["offline_since"] = ""
                update_unset["alert_sent_at"] = ""
        if update_set or update_unset:
            doc = {}
            if update_set: doc["$set"] = update_set
            if update_unset: doc["$unset"] = update_unset
            cam_ops.append(UpdateOne({"id": item["id"]}, doc))
    if daily_ops:
        await db.camera_daily.bulk_write(daily_ops)
    if cam_ops:
        await db.cameras.bulk_write(cam_ops)
    for kind, item in alerts:
        try:
            if kind == "down":
                text = f"🚨 <b>CCTV DOWN</b>\n<b>{item.get('name')}</b>\nIP: <code>{item.get('ip')}</code>\nLokasi: {item.get('location','-')}\nOffline sejak: {str(item.get('offline_since',''))[:19].replace('T',' ')} UTC"
            else:
                text = f"✅ <b>CCTV RECOVERED</b>\n<b>{item.get('name')}</b>\nIP: <code>{item.get('ip')}</code>\nOnline kembali."
            await send_telegram(text, token=settings["telegram_bot_token"], chat_id=settings["telegram_chat_id"])
        except Exception:
            pass

async def record_availability(items):
    total = len(items)
    online = sum(item.get("status") == "online" for item in items)
    now = datetime.now(timezone.utc)
    await db.availability_history.insert_one({
        "id": str(uuid.uuid4()),
        "checked_at": now.isoformat(),
        "online": online,
        "offline": total - online,
        "total": total,
        "availability": round(online / total * 100, 1) if total else 0,
    })

def safe_user(user):
    return {"id": user.get("id"), "email": user["email"], "name": user.get("name", ""), "role": user.get("role", "operator")}

def token_for(user):
    return jwt.encode({"sub": user["id"], "exp": datetime.now(timezone.utc) + timedelta(hours=8)}, JWT_SECRET, algorithm=ALGORITHM)

async def current_user(request: Request):
    token = request.cookies.get("access_token") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        raise HTTPException(401, "Sesi belum aktif")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        user = await db.users.find_one({"id": payload["sub"]}, {"_id": 0})
        if not user:
            raise HTTPException(401, "User tidak ditemukan")
        return user
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Sesi tidak valid atau sudah berakhir") from exc

async def admin_only(user=Depends(current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "Akses khusus administrator")
    return user

async def ping_host(ip: str, port: int = 80):
    started = datetime.now(timezone.utc)
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=2.0)
        writer.close()
        await writer.wait_closed()
        return True, round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 1)
    except Exception:
        return False, None

async def icmp_ping(ip: str):
    started = datetime.now(timezone.utc)
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping", "-c", "1", "-W", "2", ip,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        rc = await asyncio.wait_for(proc.wait(), timeout=3.0)
        if rc == 0:
            return True, round((datetime.now(timezone.utc) - started).total_seconds() * 1000, 1)
        # rc != 0 could be "host unreachable" OR "permission denied" — treat as inconclusive → fallback
        return None, None
    except FileNotFoundError:
        return None, None
    except Exception:
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        return None, None

async def smart_ping(ip: str):
    """ICMP first, fallback to TCP:80 when ICMP is unavailable/blocked."""
    ok, latency = await icmp_ping(ip)
    if ok is None:
        for port in (80, 443, 8080, 22):
            tcp_ok, tcp_latency = await ping_host(ip, port)
            if tcp_ok:
                return True, tcp_latency
        return False, None
    return ok, latency

@api.get("/setup/status")
async def setup_status():
    return {"needs_setup": await db.users.count_documents({}) == 0}

@api.post("/setup")
async def setup_admin(payload: SetupInput, response: Response):
    if await db.users.count_documents({}):
        raise HTTPException(409, "Setup sudah selesai")
    user = {"id": str(uuid.uuid4()), "email": payload.email.lower().strip(), "name": payload.name, "role": "admin", "password_hash": bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode(), "created_at": datetime.now(timezone.utc).isoformat()}
    try:
        await db.users.insert_one(user)
    except Exception as exc:
        raise HTTPException(409, "Email sudah digunakan") from exc
    response.set_cookie("access_token", token_for(user), httponly=True, samesite="lax", max_age=28800)
    return safe_user(user)

@api.post("/auth/login")
async def login(payload: LoginInput, response: Response):
    user = await db.users.find_one({"email": payload.email.lower().strip()}, {"_id": 0})
    if not user or not bcrypt.checkpw(payload.password.encode(), user["password_hash"].encode()):
        raise HTTPException(401, "Email atau password salah")
    response.set_cookie("access_token", token_for(user), httponly=True, samesite="lax", max_age=28800)
    return safe_user(user)

@api.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}

@api.get("/auth/me")
async def me(user=Depends(current_user)):
    return safe_user(user)

@api.get("/cameras")
async def cameras(user=Depends(current_user)):
    return await db.cameras.find({}, {"_id": 0}).sort("name", 1).to_list(1000)

@api.post("/cameras")
async def add_camera(payload: CameraInput, user=Depends(admin_only)):
    item = payload.model_dump(); item["id"] = item.get("id") or str(uuid.uuid4()); item["status"] = "unknown"; item["latency_ms"] = None; item["last_checked"] = None
    await db.cameras.insert_one(item)
    item.pop("_id", None)
    return item

@api.put("/cameras/{camera_id}")
async def edit_camera(camera_id: str, payload: CameraInput, user=Depends(admin_only)):
    item = payload.model_dump(exclude={"id"}); await db.cameras.update_one({"id": camera_id}, {"$set": item})
    return await db.cameras.find_one({"id": camera_id}, {"_id": 0})

@api.delete("/cameras/{camera_id}")
async def delete_camera(camera_id: str, user=Depends(admin_only)):
    await db.cameras.delete_one({"id": camera_id}); return {"ok": True}

@api.post("/cameras/refresh")
async def refresh_cameras(user=Depends(current_user)):
    items = await db.cameras.find({}, {"_id": 0}).to_list(1000)
    status_url = os.environ.get("CCTV_STATUS_URL")
    if status_url:
        try:
            response = await asyncio.to_thread(requests.get, status_url, timeout=4)
            remote = response.json().get("cameras", [])
            by_ip = {item.get("ip"): item for item in remote}
            updates = []
            for item in items:
                if item["ip"] in by_ip and by_ip[item["ip"]].get("status"):
                    item["status"] = by_ip[item["ip"]]["status"]
                    item["picture_url"] = by_ip[item["ip"]].get("picture_url", item.get("picture_url", ""))
                    item["last_checked"] = datetime.now(timezone.utc).isoformat()
                    updates.append(UpdateOne({"id": item["id"]}, {"$set": {"status": item["status"], "picture_url": item["picture_url"], "last_checked": item["last_checked"]}}))
            if updates:
                await db.cameras.bulk_write(updates)
            await record_availability(items)
            await process_alerts_and_history(items)
            return items
        except Exception:
            pass
    async def check(item):
        online, latency = await ping_host(item["ip"])
        item.update({"status": "online" if online else "offline", "latency_ms": latency, "last_checked": datetime.now(timezone.utc).isoformat()})
        return item
    checked = await asyncio.gather(*[check(item) for item in items])
    updates = [UpdateOne({"id": item["id"]}, {"$set": {"status": item["status"], "latency_ms": item["latency_ms"], "last_checked": item["last_checked"]}}) for item in checked]
    if updates:
        await db.cameras.bulk_write(updates)
    await record_availability(checked)
    await process_alerts_and_history(checked)
    return checked

@api.post("/ping")
async def ping(payload: PingInput, user=Depends(current_user)):
    if payload.port:
        online, latency = await ping_host(payload.ip, payload.port)
        label = f"{payload.ip}:{payload.port}"
    else:
        online, latency = await smart_ping(payload.ip)
        label = payload.ip
    result = {"ip": payload.ip, "port": payload.port, "target": label, "status": "online" if online else "offline", "latency_ms": latency, "checked_at": datetime.now(timezone.utc).isoformat()}
    await db.ping_history.insert_one({"id": str(uuid.uuid4()), **result})
    return result

@api.get("/reports/summary")
async def summary(user=Depends(current_user)):
    cameras = await db.cameras.find({}, {"_id": 0}).to_list(1000)
    online = sum(c.get("status") == "online" for c in cameras)
    return {"total": len(cameras), "online": online, "offline": len(cameras) - online, "availability": round(online / len(cameras) * 100, 1) if cameras else 0, "checked_at": datetime.now(timezone.utc).isoformat()}

@api.get("/reports/history")
async def availability_history(user=Depends(current_user)):
    history = await db.availability_history.find({}, {"_id": 0}).sort("checked_at", -1).to_list(48)
    return list(reversed(history))

@api.get("/reports/export")
async def export_report(user=Depends(current_user)):
    data = await db.cameras.find({}, {"_id": 0}).to_list(1000); out = io.StringIO(); writer = csv.DictWriter(out, fieldnames=["id", "name", "ip", "nvr", "location", "status", "latency_ms", "last_checked"], extrasaction="ignore"); writer.writeheader(); writer.writerows(data)
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=noc-cctv-report.csv"})

@api.get("/reports/camera-uptime")
async def camera_uptime(days: int = 7, user=Depends(current_user)):
    days = max(1, min(int(days), 60))
    from_day = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    docs = await db.camera_daily.find({"day": {"$gte": from_day}}, {"_id": 0}).sort([("camera_id", 1), ("day", 1)]).to_list(20000)
    cameras = {c["id"]: c for c in await db.cameras.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(2000)}
    grouped = {}
    for d in docs:
        cid = d["camera_id"]
        if cid not in grouped:
            grouped[cid] = {"camera_id": cid, "camera_name": cameras.get(cid, {}).get("name") or d.get("camera_name") or cid, "days": []}
        uptime = round(d["online"] / d["total"] * 100, 1) if d.get("total") else 0
        grouped[cid]["days"].append({"day": d["day"], "uptime": uptime, "total": d.get("total", 0), "online": d.get("online", 0)})
    return list(grouped.values())

@api.get("/settings")
async def get_settings_api(user=Depends(admin_only)):
    s = await get_settings()
    token = s.get("telegram_bot_token") or ""
    return {
        "telegram_bot_token_masked": (token[:6] + "…" + token[-4:]) if len(token) > 12 else ("***" if token else ""),
        "telegram_bot_token_set": bool(token),
        "telegram_chat_id": s.get("telegram_chat_id", ""),
        "telegram_enabled": bool(s.get("telegram_enabled")),
        "alert_threshold_minutes": int(s.get("alert_threshold_minutes", 5)),
    }

@api.put("/settings")
async def update_settings(payload: SettingsInput, user=Depends(admin_only)):
    doc = payload.model_dump(); doc["id"] = "global"
    if not doc.get("telegram_bot_token"):
        existing = await get_settings()
        doc["telegram_bot_token"] = existing.get("telegram_bot_token", "")
    if int(doc.get("alert_threshold_minutes", 5)) < 1:
        doc["alert_threshold_minutes"] = 1
    await db.settings.update_one({"id": "global"}, {"$set": doc}, upsert=True)
    return {"ok": True}

@api.post("/settings/telegram/test")
async def test_telegram(payload: TelegramTestInput, user=Depends(admin_only)):
    msg = payload.message or "🚨 Test NOC Sentinel: koneksi Telegram berhasil."
    result = await send_telegram(msg)
    return {"ok": True, "response": result}

@api.get("/cameras/import/template")
async def import_template(user=Depends(current_user)):
    rows = [
        ["name", "ip", "nvr", "location", "picture_url"],
        ["FA-PARKIRAN_CAM_01", "10.187.17.159", "NVR-91", "FA Parkiran", "http://10.2.187.91:80/ISAPI/Streaming/channels/101/picture"],
        ["FA-PARKIRAN_CAM_02", "10.187.17.160", "NVR-91", "FA Parkiran", "http://10.2.187.91:80/ISAPI/Streaming/channels/201/picture"],
        ["MC-MAINWS_1", "10.187.8.150", "NVR-92", "Main Site", ""],
    ]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerows(rows)
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=noc-cctv-import-template.csv"})

@api.post("/cameras/import")
async def import_cameras(request: Request, user=Depends(admin_only)):
    form = await request.form()
    file = form.get("file")
    if not file or not hasattr(file, "read"):
        raise HTTPException(400, "File CSV wajib diunggah dengan field name 'file'")
    raw = await file.read()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        content = raw.decode("utf-8", errors="ignore")
    reader = csv.DictReader(io.StringIO(content))
    inserted, skipped, errors = 0, 0, []
    for idx, row in enumerate(reader, start=2):
        name = (row.get("name") or "").strip()
        ip = (row.get("ip") or "").strip()
        if not name or not ip:
            skipped += 1; errors.append(f"Baris {idx}: name/ip kosong"); continue
        item = {
            "id": (row.get("id") or "").strip() or str(uuid.uuid4()),
            "name": name, "ip": ip,
            "nvr": (row.get("nvr") or "NVR").strip(),
            "location": (row.get("location") or "Main Site").strip(),
            "picture_url": (row.get("picture_url") or "").strip(),
            "status": "unknown", "latency_ms": None, "last_checked": None,
        }
        try:
            await db.cameras.insert_one(item); inserted += 1
        except Exception as exc:
            skipped += 1; errors.append(f"Baris {idx}: {str(exc)[:80]}")
    return {"inserted": inserted, "skipped": skipped, "errors": errors[:10]}

@api.get("/users")
async def users(user=Depends(admin_only)):
    return [safe_user(u) async for u in db.users.find({}, {"_id": 0})]

@api.post("/users")
async def add_user(payload: UserInput, user=Depends(admin_only)):
    item = {"id": str(uuid.uuid4()), "email": payload.email.lower().strip(), "name": payload.name, "role": payload.role, "password_hash": bcrypt.hashpw(payload.password.encode(), bcrypt.gensalt()).decode(), "created_at": datetime.now(timezone.utc).isoformat()}
    try:
        await db.users.insert_one(item)
    except Exception as exc:
        raise HTTPException(409, "Email sudah digunakan") from exc
    return safe_user(item)

app = FastAPI(title="NOC Sentinel")
app.include_router(api)
allowed_origins = [origin.strip() for origin in os.environ.get("CORS_ORIGINS", "").split(",") if origin.strip() and origin.strip() != "*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=r"^https?://([a-zA-Z0-9.-]+|\[[0-9a-fA-F:]+\])(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    seed_email = os.environ.get("SEED_ADMIN_EMAIL")
    seed_password = os.environ.get("SEED_ADMIN_PASSWORD")
    if seed_email and seed_password and await db.users.count_documents({}) == 0:
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "email": seed_email.lower().strip(),
            "name": os.environ.get("SEED_ADMIN_NAME", "NOC Administrator"),
            "role": "admin",
            "password_hash": bcrypt.hashpw(seed_password.encode(), bcrypt.gensalt()).decode(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    if await db.cameras.count_documents({}) == 0:
        seed = [{"id": f"NVR-91-{i}", "ip": ip, "name": name, "nvr": "NVR-91", "picture_url": picture, "location": "FA Parkiran", "status": "unknown", "latency_ms": None, "last_checked": None} for i, (ip, name, picture) in enumerate([("10.187.17.159", "FA-PARKIRAN_HD_ARDECON1-FIX", "http://10.2.187.91:80/ISAPI/Streaming/channels/101/picture"), ("10.187.17.160", "FA-PARKIRAN_HD_ARDECON2-FIX", "http://10.2.187.91:80/ISAPI/Streaming/channels/201/picture"), ("10.187.17.161", "FA-PARKIRAN_ARDECON-FIX", "http://10.2.187.91:80/ISAPI/Streaming/channels/301/picture"), ("10.187.8.150", "MC-MAINWS_1-FIX", "http://10.2.187.91:80/ISAPI/Streaming/channels/401/picture"), ("10.187.8.155", "MC-WS_SSE1-FIX", "http://10.2.187.91:80/ISAPI/Streaming/channels/501/picture")], 1)]
        await db.cameras.insert_many(seed)

@app.get("/health")
async def health(): return {"status": "ok"}

if os.path.isdir("/app/frontend_build"):
    app.mount("/", StaticFiles(directory="/app/frontend_build", html=True), name="frontend")