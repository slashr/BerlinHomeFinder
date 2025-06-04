# Berlin Home Finder

This repository contains a script that scans Berlin housing websites and sends you Telegram notifications when new apartment listings match predefined criteria.

## Environment Variables

Set the following variables before running the script:

- `TELEGRAM_BOT_TOKEN` – token of your Telegram bot.
- `TELEGRAM_USER_ID` – your Telegram chat ID to receive notifications.
- `STATE_FILE` – optional path for storing seen listing IDs (defaults to `./notified.pkl`).

## Running

Install dependencies and run the scanner:

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
python scan.py
```

Alternatively, use the provided Dockerfile:

```bash
docker build -t berlin-home-finder .
docker run -e TELEGRAM_BOT_TOKEN=... -e TELEGRAM_USER_ID=... berlin-home-finder
```

## License

This project is licensed under the [MIT License](LICENSE).
