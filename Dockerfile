# ==========================================
# Stage 1: Build Frontend SPA
# ==========================================
FROM oven/bun:1 AS frontend-builder
WORKDIR /app

COPY package.json bun.lockb* package-lock.json* ./
RUN bun install --frozen-lockfile || bun install

COPY . .
RUN bun run build

# ==========================================
# Stage 2: Python Backend & Unified Server
# ==========================================
FROM python:3.11-slim
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# System dependencies for PDF processing & fonts
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    libmupdf-dev \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# Copy backend requirements and install
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy upstream-core and install in editable mode
COPY upstream-core/ ./upstream-core/
RUN pip install --no-cache-dir -e ./upstream-core

# Copy backend code
COPY backend/ ./backend/

# Copy built frontend static files
COPY --from=frontend-builder /app/dist ./dist

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
