# logging_config.py - set up Python logging for DataDesk
import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logging(log_file: str = None, level: int = logging.INFO):
    """Configure logging for the DataDesk application.
    If log_file is provided, logs are also written to that file.
    """
    os.makedirs("logs", exist_ok=True)
    handlers = [logging.StreamHandler()]
    if not log_file:
        log_file = os.path.join("logs", "app.log")
    handlers.append(RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)  # reduce noise
