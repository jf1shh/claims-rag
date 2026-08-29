import pytest

from backend.rate_limit import RateLimitExceeded, SlidingWindowRateLimiter


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_given_under_limit_then_requests_are_allowed():
    clock = _Clock()
    limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60.0, clock=clock)
    limiter.check("a")
    limiter.check("a")
    limiter.check("a")  # exactly at the limit still allowed


def test_given_over_limit_then_request_is_rejected_and_not_counted():
    clock = _Clock()
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60.0, clock=clock)
    limiter.check("a")
    limiter.check("a")
    with pytest.raises(RateLimitExceeded):
        limiter.check("a")
    # The rejected request is not counted: advancing time but not escaping the
    # window, the limit is still exhausted.
    clock.advance(10)
    with pytest.raises(RateLimitExceeded):
        limiter.check("a")


def test_given_window_elapses_then_requests_are_allowed_again():
    clock = _Clock()
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60.0, clock=clock)
    limiter.check("a")
    limiter.check("a")
    with pytest.raises(RateLimitExceeded):
        limiter.check("a")
    clock.advance(60.0)  # exactly one window: all hits pruned
    limiter.check("a")
    limiter.check("a")


def test_given_partial_window_then_only_elapsed_hits_are_pruned():
    clock = _Clock()
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60.0, clock=clock)
    limiter.check("a")  # t=0
    clock.advance(59.9)
    limiter.check("a")  # t=59.9 -- both still in the window
    with pytest.raises(RateLimitExceeded):
        limiter.check("a")
    clock.advance(0.2)  # t=60.1: first hit (t=0) pruned, second (t=59.9) remains
    limiter.check("a")  # one slot freed
    with pytest.raises(RateLimitExceeded):
        limiter.check("a")


def test_given_multiple_keys_then_they_are_isolated():
    clock = _Clock()
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60.0, clock=clock)
    limiter.check("a")
    limiter.check("a")
    with pytest.raises(RateLimitExceeded):
        limiter.check("a")
    limiter.check("b")  # different principal unaffected


def test_given_burst_then_blocked_until_window_resets():
    clock = _Clock()
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=10.0, clock=clock)
    assert limiter.allow("k") is True
    assert limiter.allow("k") is False
    clock.advance(9.0)
    assert limiter.allow("k") is False
    clock.advance(1.0)
    assert limiter.allow("k") is True


def test_given_positive_limits_then_construction_is_enforced():
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(max_requests=0)
    with pytest.raises(ValueError):
        SlidingWindowRateLimiter(window_seconds=0)


def test_given_reset_then_all_keys_are_cleared():
    clock = _Clock()
    limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0, clock=clock)
    limiter.check("a")
    with pytest.raises(RateLimitExceeded):
        limiter.check("a")
    limiter.reset()
    limiter.check("a")
