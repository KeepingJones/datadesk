FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Copy lockfiles and pyproject.toml
COPY uv.lock pyproject.toml ./

# Install dependencies using uv
RUN uv sync --frozen --no-dev

# Copy application code
COPY datadesk/ datadesk/
COPY main.py ./

# Expose port for Ops Console
EXPOSE 8000

# Default entrypoint to run the ops console
CMD ["uv", "run", "python", "main.py", "serve"]
