FROM python:3.13-slim

# Dependências do sistema
RUN apt-get update && \
    apt-get install -y \
    ffmpeg \
    nodejs \
    npm \
    git \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Instala Deno
RUN curl -fsSL https://deno.land/install.sh | sh

ENV DENO_INSTALL="/root/.deno"
ENV PATH="$DENO_INSTALL/bin:$PATH"

WORKDIR /app

# Dependências Python
COPY requirements.txt .

RUN pip install --no-cache-dir -U pip && \
    pip install --no-cache-dir -r requirements.txt

# Instala bgutil
RUN git clone \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    /opt/bgutil

WORKDIR /opt/bgutil/server

RUN npm ci && \
    npx tsc

WORKDIR /app

COPY . .

ENV PORT=8080

CMD ["sh", "-c", "node /opt/bgutil/server/build/main.js & for i in $(seq 1 20); do if curl -fsS --max-time 2 http://127.0.0.1:4416/ping >/dev/null; then echo '[bgutil] Provider ready'; exec python main.py; fi; echo '[bgutil] Waiting for provider...'; sleep 1; done; echo '[bgutil] Provider unavailable after 20 attempts' >&2; exit 1"]
