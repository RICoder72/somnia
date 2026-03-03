# Somnia Container
# Python daemon + MCP server + Claude Code CLI + 1Password CLI

FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gnupg \
    ca-certificates \
    git \
    sudo \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (for Claude Code CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install 1Password CLI
RUN curl -sS https://downloads.1password.com/linux/keys/1password.asc | \
    gpg --dearmor --output /usr/share/keyrings/1password-archive-keyring.gpg && \
    echo "deb [arch=amd64 signed-by=/usr/share/keyrings/1password-archive-keyring.gpg] https://downloads.1password.com/linux/debian/amd64 stable main" | \
    tee /etc/apt/sources.list.d/1password.list && \
    apt-get update && apt-get install -y 1password-cli && \
    rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user for running Claude CLI
RUN useradd -m -s /bin/bash somnia && \
    mkdir -p /data/somnia/db /data/somnia/logs /home/somnia/.claude && \
    chown -R somnia:somnia /data/somnia /home/somnia

# Create app directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY daemon/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install MCP dependencies (fastmcp + requests for internal API calls)
RUN pip install --no-cache-dir fastmcp requests

# Copy application code
COPY . /app

# Make app readable by somnia user, entrypoint executable
RUN chown -R somnia:somnia /app && \
    chmod +x /app/docker/entrypoint.sh

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV SOMNIA_PORT=8010
ENV SOMNIA_MCP_PORT=8011
# SOMNIA_DATABASE_URL must be provided at runtime (e.g. via docker run -e or .env file)
# Example: postgresql://user:pass@host:5432/somnia
ENV HOME=/home/somnia

# NOTE: Container starts as root. The entrypoint fixes volume permissions
# then drops to the somnia user via gosu before starting services.

# Expose ports (API + MCP)
EXPOSE 8010 8011

# Health check against the Flask API
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8010/ || exit 1

# Run both services via entrypoint
CMD ["/app/docker/entrypoint.sh"]
