FROM node:20-alpine AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/yarn.lock* ./
RUN yarn install --frozen-lockfile || yarn install
COPY frontend/ ./
RUN yarn build

FROM python:3.11-slim
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY --from=frontend /build/frontend/build ./frontend_build
RUN printf '\n' >> /dev/null
EXPOSE 6678
CMD ["uvicorn", "backend.server:app", "--host", "0.0.0.0", "--port", "6678"]