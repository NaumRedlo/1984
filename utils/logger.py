import logging
from logging.handlers import RotatingFileHandler
import os

LOG_DIR = "logs"
LOG_FILE_LEVEL = "bot.log"
LOG_ERROR_LEVEL = "errors.log"
MAX_BYTES = 10_000_000  # 10 MB per log file
BACKUP_COUNT = 5  # Keep 5 old files


def setup_logger():
    # Create logs directory if it doesn't exist
    os.makedirs(LOG_DIR, exist_ok=True)

    # Format for all handlers
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File handler for all logs
    file_handler = RotatingFileHandler(
        f"{LOG_DIR}/{LOG_FILE_LEVEL}",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8"
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    # Error-only file handler
    error_handler = RotatingFileHandler(
        f"{LOG_DIR}/{LOG_ERROR_LEVEL}",
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8"
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # Configure root logger
    logger = logging.getLogger("Bot")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
    logger.addHandler(console_handler)

    return logger


def get_logger(name: str):
    parent_logger = logging.getLogger("Bot")
    return parent_logger.getChild(name)


# ← Global logger instance
logger = setup_logger()
__all__ = ["logger", "get_logger"]
