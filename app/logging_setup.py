from __future__ import annotations

import logging

from pythonjsonlogger.json import JsonFormatter

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)


class ContextFormatter(JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super().add_fields(log_record, record, message_dict)
        log_record["level"] = record.levelname
        log_record.pop("levelname", None)
        log_record["logger"] = record.name
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                log_record.setdefault(key, value)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(ContextFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").setLevel("WARNING")
