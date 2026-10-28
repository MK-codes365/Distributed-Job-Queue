"""Pytest test configuration and fixtures."""

import pytest

try:
    import redis
    from jobqueue import config
    from jobqueue.redis_client import get_client
except ImportError as exc:
    redis = None
    config = None
    get_client = None
    _import_err = exc

_redis_available = None


def is_redis_available():
    global _redis_available
    if _redis_available is None:
        if redis is None:
            _redis_available = False
        else:
            try:
                client = get_client()
                client.ping()
                _redis_available = True
            except Exception:
                _redis_available = False
    return _redis_available


@pytest.fixture(autouse=True)
def clean_redis(monkeypatch):
    """Ensure Redis is running and clean the database before and after each test."""
    if not is_redis_available():
        pytest.skip("Redis server is not available at %s" % (config.REDIS_URL if config else "localhost"))

    client = get_client()
    # Clean before test
    client.flushdb()
    yield client
    # Clean after test
    try:
        client.flushdb()
    except Exception:
        pass


@pytest.fixture
def mock_time(monkeypatch):
    """Allows tests to control time across queue operations."""
    if not is_redis_available():
        pytest.skip("Redis server is not available")

    current_time = [1000.0]

    def get_time():
        return current_time[0]

    def advance(seconds):
        current_time[0] += seconds

    import jobqueue.queue as q
    monkeypatch.setattr(q, "_now", get_time)
    return advance
