# NOC Sentinel — CCTV & Network Ping Dashboard

## Original problem statement (Indonesian)
Buat dashboard NOC untuk CCTV & ping jaringan: input IP untuk ping, CRUD data CCTV, report online/offline + availability jaringan & CCTV, multi-user login, dibungkus dalam 1 docker container port 6678.

## Architecture
- Backend: FastAPI + Motor (MongoDB async) at /api prefix, port 8001 internally / 6678 in Docker
- Frontend: React (CRA), built statically and served by FastAPI in Docker
- Auth: JWT via httpOnly cookie (8h expiry) + Authorization Bearer fallback
- DB: MongoDB (collections: users, cameras, availability_history, ping_history)
- Deployment: single Docker image via multi-stage build + docker-compose (app + mongo)

## User personas
- Admin — CRUD cameras, manage operators, all reports
- Operator — view dashboard, run ping probes, view reports (no CRUD)

## Core requirements (static)
- Multi-user (admin/operator RBAC)
- CRUD CCTV/NVR devices (admin only)
- Ping arbitrary IP:port
- Auto refresh status (30s) + manual refresh
- Availability reports (summary + 48-point history) + CSV export
- Snapshot preview via picture_url
- Single Docker container on port 6678

## Implemented (as of Feb 2026)
- All endpoints listed above (19/19 backend tests pass — iteration 3)
- Frontend full flow (login/setup/all tabs/CRUD/logout — 100% pass, iteration 4)
- Docker + docker-compose with MongoDB + volume persistence
- Seeded admin via SEED_ADMIN_EMAIL/PASSWORD env vars (admin@noc.local / admin12345 in compose)
- Fix: ObjectId serialization on POST /api/cameras
- Fix: CSV export DictWriter fieldnames drift (extrasaction='ignore')
- Fix: React useEffect(load, []) → useEffect(() => { load(); }, [])

## Backlog / P1
- Optionally add camera picture_url column to CSV export
- Tighten CORS allow_origin_regex for production
- Optional persistent availability recording on GET /api/reports/summary
