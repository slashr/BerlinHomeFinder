FROM python:3.12-slim

# Only the tools we really need for pip and wget
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Fetch Chromium *and* its Debian/Ubuntu runtime libraries
RUN playwright install --with-deps chromium

COPY . .

ENV TELEGRAM_BOT_TOKEN=''
ENV TELEGRAM_USER_ID=''

CMD ["python", "scan.py"]

