# Build from project root: docker build -f backend/Dockerfile .
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -ms /bin/bash appuser

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && pip install -r requirements.txt

COPY backend ./backend
WORKDIR /app/backend

EXPOSE 8000
ENV HOST=0.0.0.0 PORT=8000
CMD uvicorn main:app --host "$HOST" --port "$PORT"
