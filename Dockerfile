FROM node:20-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    SQL_BACKUP_DATA_DIR=/data \
    SQL_BACKUP_HOST=0.0.0.0 \
    SQL_BACKUP_PORT=8788 \
    TZ=Asia/Shanghai

COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ unixodbc unixodbc-dev \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y gcc g++ unixodbc-dev \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY --from=ui /ui/dist ./frontend/dist

EXPOSE 8788
VOLUME ["/data"]
CMD ["python", "-m", "app"]
