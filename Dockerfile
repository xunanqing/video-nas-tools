FROM python:3.12-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/data \
    VIDEO_NAS_DATA_DIR=/data \
    VIDEO_NAS_MEDIA_ROOT=/media/videos \
    VIDEO_NAS_DEDUP_CACHE=/data/dedup.sqlite3

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && ffmpeg -hide_banner -h full | grep -F -- '-display_rotation' \
    && ffprobe -version \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /data /media/videos \
    && chown 1000:1000 /data

WORKDIR /opt/video-nas-tools
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY app/ app/
COPY static/ static/

USER 1000:1000
EXPOSE 8899
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD ["python", "-c", "from urllib.request import urlopen; urlopen('http://127.0.0.1:8899/api/settings', timeout=3).close()"]
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8899"]
