# 1. Usar a imagem base do Python especificada
FROM python:3.13.7-slim-trixie

# Definir o diretório de trabalho dentro do container
WORKDIR /app

# Instalar o Git (necessário para o git clone) e limpar o cache do apt
# O slim-trixie é baseado em Debian, então usamos apt-get
RUN apt-get update && apt-get install -y git && \
    rm -rf /var/lib/apt/lists/*

# Clonar o repositório, fazer checkout na master e instalar as dependências
# Usamos "git clone .." para clonar o conteúdo diretamente no /app
RUN git clone https://gitlab.uspdigital.usp.br/isaacsantos.soares/damicore-python3.git . && \
    git checkout master && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir setuptools==80.9.0
