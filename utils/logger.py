import logging
from logging.handlers import RotatingFileHandler
import os

LOG_DIR = "logs"
LOG_FILE_LEVEL = "bot.log"
LOG_ERROR_LEVEL = "errors.log"
MAX_BYTES = 10_000_000
BACKUP_COUNT = 5

def setup_logger():

    os.makedirs(LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = RotatingFileHandler(
        f"{LOG_DIR}/{LOG_FILE_LEVEL}",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    error_handler = RotatingFileHandler(
        f"{LOG_DIR}/{LOG_ERROR_LEVEL}",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    logger = logging.getLogger("Bot")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
    logger.addHandler(console_handler)

    return logger

def get_logger(name: str):
    parent_logger = logging.getLogger("Bot")
    return parent_logger.getChild(name)

logger = setup_logger()
__all__ = ["logger", "get_logger"]
