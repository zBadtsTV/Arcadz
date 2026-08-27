FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y \
    ffmpeg \
    nodejs \
    npm \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

RUN git clone \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    /opt/bgutil

WORKDIR /opt/bgutil/server

RUN npm ci
RUN npx tsc

WORKDIR /app

COPY . .

CMD ["sh", "-c", "node /opt/bgutil/server/build/main.js & sleep 5; echo '=== YTDLP PLUGINS ==='; python -m yt_dlp --version; python -m yt_dlp -v --simulate 'https://www.youtube.com/watch?v=44pt8w67S8I' 2>&1 | grep -E 'Plugin directories|PO Token|bgutil|JS Challenge|player_client|ERROR' || true; echo '=== INICIANDO BOT ==='; python main.py"]
