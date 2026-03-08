FROM python:3.12-slim

WORKDIR /app

# ── System packages ────────────────────────────────────────────────────────
# Build tools, GIS stack, OSM tools, Node.js, pandoc
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Core tools
    git curl wget ca-certificates \
    gcc g++ build-essential cmake pkg-config \
    # GIS / geospatial (GDAL, GEOS, PROJ, spatial index)
    libgdal-dev gdal-bin \
    libgeos-dev \
    libproj-dev proj-bin \
    libspatialindex-dev \
    # OSM processing
    osmium-tool \
    # Node.js (LTS via NodeSource)
    nodejs npm \
    # Document conversion
    pandoc \
    # Image tools
    libpng-dev libjpeg-dev \
    # Misc runtime
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ────────────────────────────────────────────────────
RUN pip install --no-cache-dir uv

COPY . /app/
RUN uv pip install --system -e .

# ── Workspace directories ──────────────────────────────────────────────────
RUN mkdir -p /workspace /outputs /repos
WORKDIR /workspace

# ── Git config ────────────────────────────────────────────────────────────
RUN git config --global --add safe.directory '*'

EXPOSE 8003

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8003/forge')" 2>/dev/null || exit 1

CMD ["python", "/app/server.py"]
