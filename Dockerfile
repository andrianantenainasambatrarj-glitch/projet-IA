# TradeVision IA — image de production (fonctionne sur Hugging Face Spaces,
# Render, Railway, Fly.io, Koyeb, Docker local…)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    DATA_DIR=/data \
    LOG_LEVEL=INFO

WORKDIR /app

# Dépendances système minimales (curl pour la sonde de santé)
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-optional.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt \
    && pip install pillow yfinance || true

COPY app ./app
COPY web ./web
COPY knowledge ./knowledge
COPY tests ./tests
COPY run.py ./
COPY README.md ./

# Répertoire de données inscriptible (index vectoriel, documents, historique)
RUN mkdir -p /data && chmod -R 777 /data

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/healthz" || exit 1

CMD ["python", "run.py"]
