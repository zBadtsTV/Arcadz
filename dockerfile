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

CMD ["sh", "-c", "node /opt/bgutil/server/build/main.js & BGUTIL_PID=$!; sleep 5; echo '=== TESTANDO BGUTIL ==='; curl -v http://127.0.0.1:4416/ 2>&1 || true; echo '=== INICIANDO BOT ==='; python main.py"]
