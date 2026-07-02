from rate_limit import RateLimiter


def test_allows_until_threshold():
    rl = RateLimiter(max_failures=3, lockout_seconds=100)
    t = [1000.0]
    clock = lambda: t[0]
    assert rl.is_locked("k", now=clock()) is False
    rl.register_failure("k", now=clock())
    rl.register_failure("k", now=clock())
    assert rl.is_locked("k", now=clock()) is False
    rl.register_failure("k", now=clock())
    assert rl.is_locked("k", now=clock()) is True


def test_lockout_expires():
    rl = RateLimiter(max_failures=1, lockout_seconds=100)
    rl.register_failure("k", now=1000.0)
    assert rl.is_locked("k", now=1050.0) is True
    assert rl.is_locked("k", now=1101.0) is False


def test_clear_resets():
    rl = RateLimiter(max_failures=1, lockout_seconds=100)
    rl.register_failure("k", now=1000.0)
    rl.clear("k")
    assert rl.is_locked("k", now=1000.0) is False


def test_keys_are_independent():
    rl = RateLimiter(max_failures=1, lockout_seconds=100)
    rl.register_failure("a", now=1000.0)
    assert rl.is_locked("a", now=1000.0) is True
    assert rl.is_locked("b", now=1000.0) is False
