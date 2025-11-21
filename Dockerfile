FROM python:3.14-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy Python configuration
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

EXPOSE 8080

ENV PYTHONUNBUFFERED=1

CMD ["uv", "run", "python", "main.py"]