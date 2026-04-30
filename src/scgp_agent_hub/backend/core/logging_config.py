"""Structured logging configuration with JSON output for production."""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
user_email_var: ContextVar[str] = ContextVar("user_email", default="")


class StructuredFormatter(logging.Formatter):
    """JSON-lines log formatter for production observability."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        req_id = getattr(record, "request_id", None) or request_id_var.get("")
        if req_id:
            log_entry["request_id"] = req_id

        trace = getattr(record, "trace_id", None) or trace_id_var.get("")
        if trace:
            log_entry["trace_id"] = trace

        email = getattr(record, "user_email", None) or user_email_var.get("")
        if email:
            log_entry["user_email"] = email

        for key in ("route", "duration_ms", "status"):
            val = getattr(record, key, None) or record.__dict__.get(key)
            if val is not None:
                log_entry[key] = val

        return json.dumps(log_entry, default=str)


def configure_logging(level: str = "INFO", json_output: bool | None = None) -> None:
    if json_output is None:
        json_output = os.environ.get("LOG_FORMAT", "").lower() == "json" or bool(
            os.environ.get("DATABRICKS_RUNTIME_VERSION", "")
        )

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if root.handlers:
        root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    root.addHandler(handler)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
