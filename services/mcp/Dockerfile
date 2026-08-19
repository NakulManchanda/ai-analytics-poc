FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv==0.9.26 \
    && useradd --create-home --uid 10002 mcpuser

COPY services/mcp/pyproject.toml services/mcp/uv.lock ./services/mcp/
COPY services/mcp/mcp_server ./services/mcp/mcp_server
COPY services/dataset_spike ./services/dataset_spike
RUN uv sync --frozen --no-dev --project services/mcp \
    && chown -R mcpuser:mcpuser /app

COPY config/datasets ./config/datasets

EXPOSE 8001

USER mcpuser

CMD ["uv", "run", "--project", "services/mcp", "--no-sync", "fastmcp", "run", "services/mcp/mcp_server/server.py", "--transport", "http", "--host", "0.0.0.0", "--port", "8001"]
