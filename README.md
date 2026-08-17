# NOC Sentinel — CCTV & Network Ping Dashboard

Dashboard NOC untuk memantau kesehatan CCTV/NVR dan konektivitas jaringan.

## Fitur
- Multi-user (Admin & Operator) dengan JWT cookie session
- CRUD kamera/NVR (khusus admin)
- Auto refresh status setiap 30 detik + tombol manual refresh
- Network probe (ping IP + port arbitrary)
- Laporan availability + grafik historis (48 titik terakhir) + export CSV
- Preview snapshot kamera via `picture_url`

## Deployment lokal via Docker Compose

Prasyarat: Docker & Docker Compose terpasang di server lokal Anda.

```bash
cd /path/to/app
docker compose up -d --build
```

Aplikasi akan tersedia di **http://SERVER_IP:6678**.

### Login default (setelah container jalan)
- Email: `admin@noc.local`
- Password: `admin12345`

**PENTING**: Ganti password admin default dan `JWT_SECRET` di `docker-compose.yml` sebelum production!

### Konfigurasi via docker-compose.yml
Edit environment `app` service:
- `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` / `SEED_ADMIN_NAME` — akun admin awal (hanya di-seed jika DB kosong)
- `JWT_SECRET` — ganti dengan random string panjang
- `CCTV_STATUS_URL` — (opsional) endpoint JSON eksternal untuk polling status kamera; jika tidak diset, sistem akan TCP-ping IP kamera

### Berhentikan / restart
```bash
docker compose down          # stop + hapus container (data mongo tetap tersimpan di volume)
docker compose down -v       # stop + hapus volume (reset semua data)
docker compose restart app   # restart app saja
docker compose logs -f app   # lihat log app
```

### Update container setelah pull kode baru
```bash
docker compose up -d --build
```

## Struktur
- `backend/` — FastAPI + Motor (MongoDB async driver)
- `frontend/` — React (Create React App)
- `Dockerfile` — Multi-stage build (build React → serve via FastAPI StaticFiles pada port 6678)
- `docker-compose.yml` — App + MongoDB + volume persist
