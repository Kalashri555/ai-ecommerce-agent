# Backend image: FastAPI + both MCP servers (started by FastAPI as stdio subprocesses).
# Build context is the PROJECT ROOT (see docker-compose.yml), because the
# backend imports nothing from mcp_servers but launches it, and the MCP
# servers reuse backend/app services.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend/ backend/
COPY mcp_servers/ mcp_servers/

# SQLite lives on a volume so data survives container restarts.
RUN mkdir -p /data && useradd --create-home appuser && chown -R appuser /app /data
USER appuser

WORKDIR /app/backend
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
