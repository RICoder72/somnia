FROM python:3.12-slim

WORKDIR /app

# Match Synology's Docker API version
ENV DOCKER_API_VERSION=1.41

# Docker CLI + git
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg git && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    chmod a+r /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends docker-ce-cli && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml server.py ./
RUN uv pip install --system -e .

RUN git config --global --add safe.directory /data && \
    git config --global user.email "claude@super-claude.local" && \
    git config --global user.name "Claude"

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python3 -c "import socket; socket.create_connection(('localhost', 8001), timeout=5).close()"

CMD ["python", "server.py"]
