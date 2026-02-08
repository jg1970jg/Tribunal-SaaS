# Docker - Tribunal GoldenMaster

Esta secção descreve como executar o Tribunal GoldenMaster usando Docker.

## Pré-requisitos

- Docker instalado ([Get Docker](https://docs.docker.com/get-docker/))
- Docker Compose instalado (incluído no Docker Desktop)
- Ficheiro `.env` configurado na raiz do projeto

## Quick Start

### 1. Configurar API Keys

```bash
# Copiar ficheiro de exemplo
cp .env.example .env

# Editar com as suas chaves
nano .env  # ou notepad .env no Windows
```

### 2. Build e Iniciar

```bash
# Na pasta docker/
cd docker

# Build e iniciar
docker compose up --build

# Ou em background
docker compose up --build -d
```

### 3. Aceder

Abra no browser: **http://localhost:8501**

## Comandos Úteis

```bash
# Iniciar (após build)
docker compose up

# Iniciar em background
docker compose up -d

# Parar
docker compose down

# Ver logs
docker compose logs -f

# Rebuild
docker compose up --build

# Remover volumes (CUIDADO: apaga dados!)
docker compose down -v
```

## Volumes Persistentes

Os seguintes dados são persistidos em volumes Docker:

| Volume | Conteúdo |
|--------|----------|
| `tribunal-outputs` | Resultados das análises |
| `tribunal-historico` | Histórico de análises |
| `tribunal-data` | Base de dados de legislação |

## Variáveis de Ambiente

Definidas no `.env`:

| Variável | Descrição | Default |
|----------|-----------|---------|
| `OPENAI_API_KEY` | Chave API OpenAI | - |
| `OPENROUTER_API_KEY` | Chave API OpenRouter | - |
| `MAX_BUDGET_USD` | Budget máximo por run | 5.00 |
| `MAX_TOKENS_TOTAL` | Limite de tokens | 500000 |
| `LOG_LEVEL` | Nível de logging | INFO |

## Troubleshooting

### Container não inicia

```bash
# Ver logs detalhados
docker compose logs tribunal

# Verificar se .env existe
ls -la ../.env
```

### Erro de API Key

Verifique se o ficheiro `.env` está na raiz do projeto (não na pasta docker/).

### Porta ocupada

```bash
# Usar porta diferente
docker compose up -p 8502:8501
```

### Problemas de memória

Ajuste os limites em `docker-compose.yml`:

```yaml
deploy:
  resources:
    limits:
      memory: 4G  # Aumentar se necessário
```

## Build Manual

Se preferir não usar docker-compose:

```bash
# Na raiz do projeto
docker build -f docker/Dockerfile -t tribunal-goldenmaster .

# Executar
docker run -p 8501:8501 --env-file .env \
  -v tribunal-outputs:/app/outputs \
  -v tribunal-historico:/app/historico \
  -v tribunal-data:/app/data \
  tribunal-goldenmaster
```
