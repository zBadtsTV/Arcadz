FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y \
    ffmpeg \
    nodejs \
    npm \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

RUN git clone --single-branch \
    --branch 1.3.1 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    /opt/bgutil

WORKDIR /opt/bgutil/server

RUN npm ci && \
    npx tsc

WORKDIR /app

COPY . .

CMD ["sh", "-c", "node /opt/bgutil/server/build/main.js & sleep 5 && python main.py"]
