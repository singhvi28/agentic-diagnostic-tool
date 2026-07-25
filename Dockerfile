FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples
COPY logs ./logs

RUN pip install --no-cache-dir -e ".[dev]"

ENV TARGET_APP_ROOT=/app/examples/target_app \
    ERROR_LOG_PATH=/app/logs/app_errors.log \
    SANDBOX_ROOT=/app/examples/target_app

CMD ["diagnostic-mcp"]
