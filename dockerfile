FROM python:3.13-slim

# Dependências do sistema
RUN apt-get update && \
    apt-get install -y \
    ffmpeg \
    nodejs \
    npm \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependências Python
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Instala o PO Token Provider
RUN git clone --single-branch \
    --branch 1.3.1 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    /opt/bgutil

WORKDIR /opt/bgutil/server

RUN npm ci
RUN npx tsc

WORKDIR /app

# Código do bot
COPY . .

# Inicia o provider e depois o bot
CMD ["sh", "-c", "node /opt/bgutil/server/dist/main.js & python main.py"]
