from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar

from pythonjsonlogger.json import JsonFormatter

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__)

run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
session_id_var: ContextVar[str | None] = ContextVar("session_id", default=None)
step_var: ContextVar[int | None] = ContextVar("step", default=None)


@contextmanager
def log_context(*, run_id: str | None = None, session_id: str | None = None):
    tokens = [run_id_var.set(run_id), session_id_var.set(session_id)]
    try:
        yield
    finally:
        run_id_var.reset(tokens[0])
        session_id_var.reset(tokens[1])


def set_step(step: int | None) -> None:
    step_var.set(step)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for name, var in (
            ("run_id", run_id_var),
            ("session_id", session_id_var),
            ("step", step_var),
        ):
            value = var.get()
            if value is not None and not hasattr(record, name):
                setattr(record, name, value)
        return True


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
    handler.addFilter(ContextFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    logging.getLogger("uvicorn.access").setLevel("WARNING")
