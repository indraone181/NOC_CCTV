# Instalasi NOC Sentinel di Server Lokal

## Prasyarat
- Docker Engine dan Docker Compose/plugin tersedia di server.
- MongoDB berjalan di server pada port `27017` atau gunakan MongoDB host lain.
- Port `6678` dibuka pada firewall server.
- Server dapat menjangkau jaringan CCTV dan `10.2.187.11:5000`.

## Build dan jalankan

```bash
cd /path/ke/project
docker build -t noc-sentinel:latest .
docker run -d \
  --name noc-sentinel \
  --restart unless-stopped \
  --add-host=host.docker.internal:host-gateway \
  -p 6678:6678 \
  --env-file backend/.env \
  -e MONGO_URL=mongodb://host.docker.internal:27017 \
  noc-sentinel:latest
```

Buka `http://IP_SERVER:6678`, lalu buat administrator pertama kali melalui form setup.

## Jika MongoDB berada di host berbeda

Ganti nilai `MONGO_URL` saat menjalankan container:

```bash
docker run -d --name noc-sentinel --restart unless-stopped \
  -p 6678:6678 --env-file backend/.env \
  -e MONGO_URL=mongodb://USER:PASSWORD@IP_MONGODB:27017/NAMA_DATABASE?authSource=admin \
  noc-sentinel:latest
```

## Pemeriksaan setelah berjalan

```bash
docker ps
docker logs -f noc-sentinel
curl http://127.0.0.1:6678/health
```

Jika snapshot kamera tidak tampil, pastikan IP server Docker dapat membuka `picture_url` CCTV dan API `CCTV_STATUS_URL`.