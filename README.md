# BerlinHomeFinder

This project is a small crawler that looks for apartments on various Berlin housing websites and sends new offers via Telegram.

## Usage

Install the requirements and run `scan.py`. The script expects the following environment variables:

- `TELEGRAM_BOT_TOKEN` – your bot token
- `TELEGRAM_USER_ID` – your chat ID

Optionally set `STATE_FILE` to control where seen listings are stored.

## License

This project is licensed under the [MIT License](LICENSE).
