FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for high-speed package installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy requirements and install
COPY requirements.txt .
RUN uv pip install --system -r requirements.txt

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /home/app --create-home app

# Copy source code and configuration
COPY . .

ENV HOME=/home/app
USER 10001:10001

# Expose Streamlit default port
EXPOSE 8501

# Compose runs FPL and news ingestion in a dedicated worker.
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
