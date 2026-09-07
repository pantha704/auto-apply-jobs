FROM python:3.12-slim

# System dependencies for Playwright browsers
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    gnupg \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    libwayland-client0 \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (Chromium only for headless scraping)
RUN python -m playwright install --with-deps chromium

# Copy application code
COPY . .

# Non-root user for security
RUN useradd -m -r -s /bin/bash appuser && \
    mkdir -p /var/lib/jobhunt /var/log/jobhunt && \
    chown -R appuser:appuser /app /var/lib/jobhunt /var/log/jobhunt
USER appuser

# Environment
ENV PYTHONUNBUFFERED=1
ENV JOBHUNT_CONTROL_DB=/var/lib/jobhunt/controlplane.db
ENV JOBHUNT_VAULT_KEY=/var/lib/jobhunt/canonical-runtime.key

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -sf http://127.0.0.1:8787/health || exit 1

CMD ["uvicorn", "controlplane.app:app", "--host", "0.0.0.0", "--port", "8787"]
