from warnings import filterwarnings
from telegram.warnings import PTBUserWarning

filterwarnings(
    action="ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning
)
filterwarnings(
    action="ignore", message=r".*the `days` parameter.*", category=PTBUserWarning
)
filterwarnings(
    action="ignore", message=r".*invalid escape sequence.*", category=SyntaxWarning
)

import logging

from Config import Config

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=Config.LOG_LEVEL,
)
# python-telegram-bot uses httpx; INFO logs every getUpdates/sendMessage (noise).
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

from handlers import setup_and_run

if __name__ == "__main__":
    setup_and_run()
