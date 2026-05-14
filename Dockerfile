# Reze deploy image — apuntada a Fly.io free tier (512MB RAM, shared CPU).
# Imagen final ~600-800MB después de pre-cachear el modelo de embeddings.

FROM python:3.11-slim

# Mínimo necesario: curl para healthchecks; el resto se evita para ahorrar RAM.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

WORKDIR /app

# Capa de deps: cambia poco → cache hit alto entre deploys
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-descargar el modelo de embeddings en build time (no en primera request).
# Esto agrega ~100MB a la imagen pero la primera consulta no espera la descarga.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2')" || true

# Código de la app
COPY . .

# Variables de runtime
ENV PORT=8000 \
    REZE_DB=/data/reze_state.db \
    FASTEMBED_CACHE_DIR=/data/fastembed_cache \
    PYTHONUNBUFFERED=1

EXPOSE 8000

# Sin reload, 1 worker (la app es chiquita y comparte estado en memoria).
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT} --workers 1"]
