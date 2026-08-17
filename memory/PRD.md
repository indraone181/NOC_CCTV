# PRD — NOC Sentinel

## Original problem statement
Dashboard NOC CCTV dan ping jaringan dengan input IP, pengolahan data CCTV, laporan offline/online, availability jaringan dan CCTV, multi-user login, serta satu Docker port 6678.

## Architecture decisions
- React 19 frontend, FastAPI backend, MongoDB via existing environment variables.
- JWT httpOnly cookie session with admin/operator roles.
- Single Docker image intended to expose port 6678.

## Personas
- Admin NOC: mengelola device dan user.
- Operator NOC: memantau status, probe jaringan, dan laporan.

## Core requirements (static)
- Live status CCTV/NVR, ping IP, availability summary, CRUD device, filtering, export CSV, multi-user.

## What's implemented (2026-02-14)
- Initial admin setup form, login/logout, role-aware access.
- CCTV/NVR registry seeded from provided sample data, status refresh, ping probe.
- Overview metrics, reports, CSV export, responsive tactical NOC UI.
- Dockerfile for port 6678.

## Prioritized backlog
- P0: validate deployment image with MongoDB and connect external CCTV status API.
- P1: persistent hourly availability history chart, PDF export, scheduled refresh.
- P2: camera snapshot preview, audit log, password reset.