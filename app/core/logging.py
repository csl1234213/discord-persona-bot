import json
import logging
import re
from contextvars import ContextVar

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="none")


class StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "correlation_id": correlation_id.get(),
        }
        return json.dumps(payload, ensure_ascii=False)


def redact(text: str) -> str:
    text = re.sub(r"(?i)(bearer\s+|token[=:]\s*|api[_-]?key[=:]\s*)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"\b[\w-]{23,28}\.[\w-]{6}\.[\w-]{27,110}\b", "[REDACTED]", text)
    text = re.sub(r"https?://[^\s]+", "[URL_REDACTED]", text)
    return text


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter())
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
    for name in ("httpx", "httpcore", "discord.http", "sqlalchemy.engine"):
        logging.getLogger(name).setLevel(logging.WARNING)
