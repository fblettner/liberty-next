"""Login throttle — a small in-memory sliding window of failed logins per key.

Keys are ``user:<username>`` and ``ip:<client address>``; a login attempt is refused
(HTTP 429 with ``Retry-After``) once the user key has reached ``max_failures`` failures
inside the last ``window_seconds``, or the address key has reached ``ip_multiplier`` times
that (the address limit exists to catch password spraying across many accounts, so it is
looser — a legitimate user mistyping a few times must not lock their whole office NAT).
A successful login clears the user key only. State is
per process (no shared store), which is the deployment shape nomaflow and Socket.IO
already assume (one replica); it resets on restart — fine for its purpose, which is to
blunt online password guessing, not to be an audit trail.

``max_failures = 0`` disables the throttle entirely (``[auth] login_max_failures = 0``).
"""

from __future__ import annotations

import time
from collections import deque


class LoginThrottle:
    IP_PREFIX = "ip:"

    def __init__(self, *, max_failures: int = 10, window_seconds: int = 300, ip_multiplier: int = 5) -> None:
        self.max_failures = max(0, int(max_failures))
        self.window = max(1, int(window_seconds))
        self.ip_multiplier = max(1, int(ip_multiplier))
        self._failures: dict[str, deque[float]] = {}

    def limit_for(self, key: str) -> int:
        return self.max_failures * (self.ip_multiplier if key.startswith(self.IP_PREFIX) else 1)

    @property
    def enabled(self) -> bool:
        return self.max_failures > 0

    def _prune(self, key: str, now: float) -> deque[float]:
        if len(self._failures) > 5000:  # keep the dict bounded on a noisy install
            for k in [k for k, v in self._failures.items() if not v]:
                self._failures.pop(k, None)
        q = self._failures.get(key)
        if q is None:
            q = deque()
            self._failures[key] = q
        cutoff = now - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        return q

    def retry_after(self, *keys: str) -> int:
        """Seconds the caller must wait before another attempt — ``0`` when allowed now.
        The longest wait across *keys* wins (a locked user stays locked from any address)."""
        if not self.enabled:
            return 0
        now = time.monotonic()
        wait = 0
        for key in keys:
            q = self._prune(key, now)
            if len(q) >= self.limit_for(key):
                wait = max(wait, int(q[0] + self.window - now) + 1)
        return wait

    def record_failure(self, *keys: str) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        for key in keys:
            self._prune(key, now).append(now)

    def reset(self, *keys: str) -> None:
        for key in keys:
            self._failures.pop(key, None)


def client_ip(headers: dict[str, str] | None, peer: str | None) -> str:
    """The client address for throttling: the first ``X-Forwarded-For`` hop when a reverse
    proxy (Traefik / nginx in the shipped layouts) set it, else the TCP peer."""
    fwd = (headers or {}).get("x-forwarded-for", "")
    first = fwd.split(",")[0].strip() if fwd else ""
    return first or (peer or "unknown")
