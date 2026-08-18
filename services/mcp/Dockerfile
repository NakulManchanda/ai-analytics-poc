FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv==0.9.26 \
    && useradd --create-home --uid 10002 mcpuser

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev \
    && chown -R mcpuser:mcpuser /app

COPY mcp_server ./mcp_server

EXPOSE 8001

USER mcpuser

CMD ["uv", "run", "--no-sync", "fastmcp", "run", "mcp_server/server.py", "--transport", "http", "--host", "0.0.0.0", "--port", "8001"]
