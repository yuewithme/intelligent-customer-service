import json
import logging
import sys
from typing import Any


def configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            stream=sys.stdout,
            format="%(message)s",
        )


def log_event(event: dict[str, Any]) -> None:
    configure_logging()
    logging.getLogger("wechat_rag_bot").info(
        json.dumps(event, ensure_ascii=False, default=str)
    )

