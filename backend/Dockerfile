FROM python:3.11-slim

# System dependencies for Playwright's Chromium + Xvfb virtual display
RUN apt-get update && apt-get install -y \
    wget curl gnupg \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libasound2 libpango-1.0-0 libpangocairo-1.0-0 \
    libxshmfence1 fonts-liberation \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright's Chromium browser
RUN playwright install chromium

# Copy backend source and testing modules
COPY backend/ /app/backend/
COPY testing/ /app/testing/

# Create runtime data directories
RUN mkdir -p /app/testing/extracts /app/testing/reports

WORKDIR /app/backend

EXPOSE 8000

# Start a virtual display on :99 so headless=False Chrome has a display to attach to.
# This is what enables the CDP fallback (bare Chrome, no automation flags) to also work.
CMD ["sh", "-c", "Xvfb :99 -screen 0 1280x720x24 -nolisten tcp >/dev/null 2>&1 & sleep 1 && DISPLAY=:99 uvicorn main:app --host 0.0.0.0 --port 8000"]
