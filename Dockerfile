FROM node:20-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/yarn.lock* ./
RUN yarn install --frozen-lockfile || yarn install
COPY frontend/ ./
# Build the React bundle so it hits the same origin (relative /api paths)
RUN echo "REACT_APP_BACKEND_URL=" > .env && echo "ENABLE_HEALTH_CHECK=false" >> .env
RUN yarn build

FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
# emergentintegrations adalah paket internal Emergent — tidak dipakai di project ini, di-skip agar build works di luar platform
RUN grep -v '^emergentintegrations' requirements.txt > requirements.docker.txt \
    && pip install --no-cache-dir -r requirements.docker.txt
COPY backend/ ./backend/
COPY --from=frontend /build/frontend/build ./frontend_build
EXPOSE 6678
CMD ["uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "6678"]
