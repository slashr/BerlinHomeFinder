# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Install system dependencies required by Playwright and build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    ca-certificates \
    # Install dependencies for Chromium
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpangocairo-1.0-0 \
    libpango-1.0-0 \
    libatspi2.0-0 \
    libgtk-3-0 \
    libdrm2 \
    libxshmfence1 \
    libxss1 \
    fonts-liberation \
    libappindicator3-1 \
    libnspr4 \
    libx11-xcb1 \
    libx11-6 \
    libxext6 \
    libxfixes3 \
    libxrender1 \
    libxi6 \
    libxtst6 \
    libxkbcommon0 \
    # Install build tools
    build-essential \
    # Clean up
    && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file
COPY requirements.txt ./

# Install Playwright and other Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (only Chromium)
RUN playwright install chromium

# Copy the rest of the application code
COPY . .

# Define environment variables (optional)
ENV TELEGRAM_BOT_TOKEN=''
ENV TELEGRAM_USER_ID=''

# Command to run the script
CMD ["python", "scan.py"]

