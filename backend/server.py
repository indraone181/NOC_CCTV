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
    port: int = 80

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
            for item in items:
                if item["ip"] in by_ip and by_ip[item["ip"]].get("status"):
                    item["status"] = by_ip[item["ip"]]["status"]
                    item["picture_url"] = by_ip[item["ip"]].get("picture_url", item.get("picture_url", ""))
                    item["last_checked"] = datetime.now(timezone.utc).isoformat()
                    await db.cameras.update_one({"id": item["id"]}, {"$set": {"status": item["status"], "picture_url": item["picture_url"], "last_checked": item["last_checked"]}})
            await record_availability(items)
            return items
        except Exception:
            pass
    async def check(item):
        online, latency = await ping_host(item["ip"])
        item.update({"status": "online" if online else "offline", "latency_ms": latency, "last_checked": datetime.now(timezone.utc).isoformat()})
        await db.cameras.update_one({"id": item["id"]}, {"$set": {"status": item["status"], "latency_ms": latency, "last_checked": item["last_checked"]}})
        return item
    checked = await asyncio.gather(*[check(item) for item in items])
    await record_availability(checked)
    return checked

@api.post("/ping")
async def ping(payload: PingInput, user=Depends(current_user)):
    online, latency = await ping_host(payload.ip, payload.port)
    result = {"ip": payload.ip, "port": payload.port, "status": "online" if online else "offline", "latency_ms": latency, "checked_at": datetime.now(timezone.utc).isoformat()}
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
    data = await db.cameras.find({}, {"_id": 0}).to_list(1000); out = io.StringIO(); writer = csv.DictWriter(out, fieldnames=["id", "name", "ip", "nvr", "location", "status", "latency_ms", "last_checked"]); writer.writeheader(); writer.writerows(data)
    return StreamingResponse(iter([out.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=noc-cctv-report.csv"})

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
app.add_middleware(CORSMiddleware, allow_origins=[origin for origin in os.environ.get("CORS_ORIGINS", "").split(",") if origin and origin != "*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    if await db.cameras.count_documents({}) == 0:
        seed = [{"id": f"NVR-91-{i}", "ip": ip, "name": name, "nvr": "NVR-91", "picture_url": picture, "location": "FA Parkiran", "status": "unknown", "latency_ms": None, "last_checked": None} for i, (ip, name, picture) in enumerate([("10.187.17.159", "FA-PARKIRAN_HD_ARDECON1-FIX", "http://10.2.187.91:80/ISAPI/Streaming/channels/101/picture"), ("10.187.17.160", "FA-PARKIRAN_HD_ARDECON2-FIX", "http://10.2.187.91:80/ISAPI/Streaming/channels/201/picture"), ("10.187.17.161", "FA-PARKIRAN_ARDECON-FIX", "http://10.2.187.91:80/ISAPI/Streaming/channels/301/picture"), ("10.187.8.150", "MC-MAINWS_1-FIX", "http://10.2.187.91:80/ISAPI/Streaming/channels/401/picture"), ("10.187.8.155", "MC-WS_SSE1-FIX", "http://10.2.187.91:80/ISAPI/Streaming/channels/501/picture")], 1)]
        await db.cameras.insert_many(seed)

@app.get("/health")
async def health(): return {"status": "ok"}

if os.path.isdir("/app/frontend_build"):
    app.mount("/", StaticFiles(directory="/app/frontend_build", html=True), name="frontend")