"""
logger.py - Professional logging setup (file + console) with rotation.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime


def setup_logger(name: str = "aether", log_dir: str = "logs", level: str = "INFO") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if logger.handlers:
        return logger  # already configured

    # Console
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s - %(message)s", "%H:%M:%S"))

    # File with rotation
    log_file = os.path.join(log_dir, f"aether_{datetime.utcnow().strftime('%Y%m%d')}.log")
    fh = RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024, backupCount=7, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        "%Y-%m-%d %H:%M:%S"
    ))

    logger.addHandler(ch)
    logger.addHandler(fh)
    logger.propagate = False
    return logger
