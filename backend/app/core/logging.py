"""Structured logging with a per-request ID.

Every log line (JSON in production) carries `request_id`, so one user action can be followed
across the API logs. Extra fields are passed with `logger.info("msg", extra={"user_id": ...})`.
"""
import contextvars
import datetime as dt
import json
import logging
import re
import sys
import uuid

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime", "taskName"}


def new_request_id(incoming: str | None = None) -> str:
    """Reuse a caller-supplied X-Request-ID if it is sane (so IDs can span services), else mint one.
    Validated because it ends up in logs and a response header."""
    if incoming and _VALID_REQUEST_ID.match(incoming):
        return incoming
    return uuid.uuid4().hex


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": dt.datetime.fromtimestamp(record.created, dt.timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rid = request_id_var.get()
        base = f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} {record.name}: {record.getMessage()}"
        if rid != "-":
            base += f"  [{rid[:8]}]"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler._docchat = True  # type: ignore[attr-defined]
    root = logging.getLogger()
    # Replace only OUR previous handler (calling twice never duplicates lines) and leave anything
    # else alone (e.g. pytest's capture handlers).
    root.handlers[:] = [h for h in root.handlers if not getattr(h, "_docchat", False)]
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "sentence_transformers", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
