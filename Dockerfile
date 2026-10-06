# CampusVision — CPU image (lab PCs, servers without NVIDIA GPU)
# Build:  docker build -t campusvision:cpu .
# Run:    docker run --rm -p 5000:5000 --env-file .env campusvision:cpu
# (data now lives in MongoDB Atlas, not a local volume - pass MONGODB_URI via --env-file)

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # CPU-tuned defaults (override with -e DET_SIZE=... etc.)
    DET_SIZE=640 \
    DET_THRESH=0.30 \
    USE_FACE_TILES=0 \
    SAMPLE_INTERVAL_SEC=2.5 \
    FRAME_RESIZE_WIDTH=960 \
    TF_CPP_MIN_LOG_LEVEL=3 \
    ORT_LOGGING_LEVEL=3 \
    TF_ENABLE_ONEDNN_OPTS=0

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        ffmpeg \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements-cpu.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements-cpu.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/

WORKDIR /app/backend
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD curl -fsS http://127.0.0.1:5000/ || exit 1

CMD ["python", "app.py"]
