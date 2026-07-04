import time as _time


class RateLimiter:
    def __init__(self, max_failures, lockout_seconds):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures = {}  # key -> list[timestamp]

    def _prune(self, key, now):
        cutoff = now - self.lockout_seconds
        kept = [t for t in self._failures.get(key, []) if t >= cutoff]
        if kept:
            self._failures[key] = kept
        else:
            self._failures.pop(key, None)
        return kept

    def is_locked(self, key, now=None):
        now = _time.time() if now is None else now
        kept = self._prune(key, now)
        return len(kept) >= self.max_failures

    def register_failure(self, key, now=None):
        now = _time.time() if now is None else now
        self._prune(key, now)
        self._failures.setdefault(key, []).append(now)

    def clear(self, key):
        self._failures.pop(key, None)
