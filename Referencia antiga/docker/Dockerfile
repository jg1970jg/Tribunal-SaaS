# ============================================================
# TRIBUNAL GOLDENMASTER - Dockerfile
# ============================================================
# Build: docker build -t tribunal-goldenmaster .
# Run: docker run -p 8501:8501 --env-file .env tribunal-goldenmaster
# ============================================================

FROM python:3.11-slim

# Metadata
LABEL maintainer="Tribunal GoldenMaster"
LABEL description="Sistema de Análise Jurídica com IA para Direito Português"
LABEL version="2.0"

# Variáveis de ambiente
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_ENABLE_CORS=false

# Criar diretórios
RUN mkdir -p /app/data /app/outputs /app/historico

# Instalar dependências do sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libxml2-dev \
    libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

# Diretório de trabalho
WORKDIR /app

# Copiar requirements primeiro (cache de Docker)
COPY requirements.txt .

# Instalar dependências Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fonte
COPY src/ ./src/
COPY data/schema.sql ./data/
COPY data/create_db.py ./data/
COPY fixtures/ ./fixtures/
COPY app.py .

# Copiar entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Criar utilizador não-root
RUN useradd -m -u 1000 tribunal && \
    chown -R tribunal:tribunal /app

USER tribunal

# Expor porta do Streamlit
EXPOSE 8501

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# Entrypoint
ENTRYPOINT ["/entrypoint.sh"]

# Comando default
CMD ["streamlit", "run", "src/app.py", "--server.address", "0.0.0.0"]
