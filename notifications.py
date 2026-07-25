import logging

import requests

import config


logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def send(message: str) -> None:
    if not enabled():
        return
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": message},
            timeout=20,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)

