import time
from collections import OrderedDict
from contextlib import contextmanager

from app.core.errors import AppError


class ChatAdmission:
    """Bound queued work and event deduplication memory before creating DB sessions."""

    def __init__(self, maximum: int, *, seen_limit: int = 10000, ttl: float = 3600):
        self.maximum = maximum
        self.pending = 0
        self.seen_limit = seen_limit
        self.ttl = ttl
        self.seen: OrderedDict[str, float] = OrderedDict()

    @contextmanager
    def hold(self, event_id):
        now = time.monotonic()
        while self.seen and next(iter(self.seen.values())) < now - self.ttl:
            self.seen.popitem(last=False)
        key = str(event_id)
        if key in self.seen:
            raise AppError("duplicate_event", "Duplicate request ignored.")
        if self.pending >= self.maximum:
            raise AppError("queue_full", "Request queue is full.")
        self.seen[key] = now
        if len(self.seen) > self.seen_limit:
            self.seen.popitem(last=False)
        self.pending += 1
        try:
            yield
        finally:
            self.pending -= 1
