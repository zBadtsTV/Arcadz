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

CMD ["sh", "-c", "node /opt/bgutil/server/build/main.js & sleep 5; python main.py"]
