FROM python:3.12-slim

WORKDIR /app

# Install git, 1Password CLI, and build deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git ca-certificates curl gnupg \
        gcc g++ pkg-config libcairo2-dev libffi-dev && \
    # 1Password CLI (for user_secrets)
    curl -sS https://downloads.1password.com/linux/keys/1password.asc | \
        gpg --dearmor -o /usr/share/keyrings/1password-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/$(dpkg --print-architecture) stable main" > \
        /etc/apt/sources.list.d/1password-cli.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends 1password-cli && \
    rm -rf /var/lib/apt/lists/*

# Install uv for fast Python package management
RUN pip install uv

# Copy application code
COPY . /app/

# Install Python deps (supernote-tool CLI comes from supernotelib)
RUN uv pip install --system -e .

# Trust /data for git operations
RUN git config --global --add safe.directory '*'

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python3 -c "import socket; socket.create_connection(('localhost', 8000), timeout=5).close()"

CMD ["python", "server.py"]
