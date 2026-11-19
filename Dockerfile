FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DOWNLOAD_DIR=/app/downloads \
    COOKIE_FILE=/app/cookies.txt

WORKDIR /app

# MP3 conversion, merging, and SSL/certs for yt-dlp
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

RUN mkdir -p /app/downloads

CMD ["python", "bot.py"]
