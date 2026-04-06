"""
Logger setup for Predict.fun bot
"""

import sys
from datetime import datetime
from pathlib import Path
from loguru import logger

from config import LOGS_DIR


def setup_logger():
    """Configure loguru logger"""
    # Убираем дефолтный handler
    logger.remove()
    
    # Формат для консоли
    console_format = (
        "<level>{time:HH:mm:ss}</level> | "
        "<level>{level: <8}</level> | "
        "<level>{message}</level>"
    )
    
    # Формат для файла
    file_format = (
        "{time:YYYY-MM-DD HH:mm:ss} | "
        "{level: <8} | "
        "{message}"
    )
    
    # Консоль (INFO и выше)
    logger.add(
        sys.stdout,
        format=console_format,
        level="INFO",
        colorize=True
    )
    
    # Файл (DEBUG для отладки)
    LOGS_DIR.mkdir(exist_ok=True)
    log_file = LOGS_DIR / f"bot_{datetime.now().strftime('%Y-%m-%d')}.log"
    
    logger.add(
        log_file,
        format=file_format,
        level="DEBUG",
        rotation="1 day",
        retention="7 days"
    )
    
    return logger
