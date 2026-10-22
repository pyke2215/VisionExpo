# Vision Expo – GPU image (NVIDIA). CPU-only build:
#   docker build --build-arg TORCH_INDEX=https://download.pytorch.org/whl/cpu -t vision-expo:cpu .
FROM python:3.11-slim

ARG TORCH_INDEX=https://download.pytorch.org/whl/cu126

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENCV_LOG_LEVEL=ERROR \
    TZ=Asia/Ho_Chi_Minh \
    PORT=8000

# libgl/libglib: opencv-python runtime; git: CLIP install; tzdata: local-time charts.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 git tzdata curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install torch torchvision --index-url ${TORCH_INDEX}
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && pip install "git+https://github.com/ultralytics/CLIP.git"

COPY models ./models
COPY app ./app
COPY web ./web
COPY tools ./tools
# Seed config: copied into the mounted /app/config on first start if files are missing.
COPY config ./config.default
# Weights are not in git: on a fresh clone download them and bake the vocabulary (CLIP, needs internet once).
RUN if [ ! -f models/yolo26n-pose.pt ] || [ ! -f models/yolo26n-seg.pt ] || [ ! -f models/yoloe_exhibition.pt ]; then \
        mkdir -p config && cp config.default/vocabulary.json config/ 2>/dev/null || true; \
        python tools/prepare_models.py --device cpu && rm -rf config; \
    fi
COPY docker/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh \
    && python -c "import app.main"

EXPOSE 8000
VOLUME ["/app/config", "/app/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fs "http://127.0.0.1:${PORT}/api/status" > /dev/null || exit 1

ENTRYPOINT ["/entrypoint.sh"]
