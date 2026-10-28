"""Unit and integration tests for core queue mechanics and Redis Lua scripts."""

import pytest
from jobqueue import config, queue


def test_enqueue_and_get_job():
    job_id, created = queue.enqueue("sleep", payload={"seconds": 2}, priority="default")
    assert created is True
    assert job_id is not None

    record = queue.get_job(job_id)
    assert record is not None
    assert record["id"] == job_id
    assert record["type"] == "sleep"
    assert record["payload"] == {"seconds": 2}
    assert record["status"] == "ready"
    assert record["attempts"] == 0
    assert record["priority"] == "default"


def test_fifo_ordering():
    id_1, _ = queue.enqueue("sleep", payload={"seq": 1})
    id_2, _ = queue.enqueue("sleep", payload={"seq": 2})
    id_3, _ = queue.enqueue("sleep", payload={"seq": 3})

    assert queue.claim() == id_1
    assert queue.claim() == id_2
    assert queue.claim() == id_3
    assert queue.claim() is None


def test_priority_drain():
    low_id, _ = queue.enqueue("sleep", priority="low")
    default_id, _ = queue.enqueue("sleep", priority="default")
    high_id, _ = queue.enqueue("sleep", priority="high")

    # High priority must always be claimed first, then default, then low
    assert queue.claim() == high_id
    assert queue.claim() == default_id
    assert queue.claim() == low_id
    assert queue.claim() is None


def test_idempotency_key():
    job_id_1, created_1 = queue.enqueue("email", payload={"to": "user@test.com"}, idempotency_key="welcome-user")
    assert created_1 is True

    # Second enqueue with same key must return existing job id without creating a new job
    job_id_2, created_2 = queue.enqueue("email", payload={"to": "user@test.com"}, idempotency_key="welcome-user")
    assert created_2 is False
    assert job_id_1 == job_id_2

    # Check stats reflect only 1 enqueued job
    stats = queue.stats()
    assert stats["enqueued"] == 1
    assert stats["ready_total"] == 1


def test_claim_and_ack():
    job_id, _ = queue.enqueue("sleep", payload={"seconds": 1})
    claimed_id = queue.claim()
    assert claimed_id == job_id

    # Inflight counter should be 1
    stats = queue.stats()
    assert stats["inflight"] == 1

    # Successful ack
    acked = queue.ack(job_id)
    assert acked is True

    # Job record should be completed and inflight should be 0
    record = queue.get_job(job_id)
    assert record["status"] == "completed"
    stats_after = queue.stats()
    assert stats_after["inflight"] == 0
    assert stats_after["completed"] == 1


def test_ack_expired_or_lost_lease():
    # Attempting to ack a job that was never claimed / not inflight must return False
    acked = queue.ack("nonexistent-id")
    assert acked is False


def test_fail_exponential_backoff(mock_time):
    job_id, _ = queue.enqueue("flaky")
    queue.claim()

    outcome = queue.fail(job_id, "temporary network glitch")
    assert outcome == "retry"

    record = queue.get_job(job_id)
    assert record["status"] == "scheduled"
    assert record["attempts"] == 1
    assert record["last_error"] == "temporary network glitch"


def test_fail_dead_letter_queue(monkeypatch):
    monkeypatch.setattr(config, "MAX_ATTEMPTS", 2)

    job_id, _ = queue.enqueue("flaky")
    queue.claim()

    # Attempt 1 -> retry
    outcome_1 = queue.fail(job_id, "error 1")
    assert outcome_1 == "retry"

    # Promote and claim again
    queue.promote(limit=10)
    queue.claim()

    # Attempt 2 -> dead letter queue
    outcome_2 = queue.fail(job_id, "error 2")
    assert outcome_2 == "dead"

    record = queue.get_job(job_id)
    assert record["status"] == "dead"
    assert record["attempts"] == 2

    stats = queue.stats()
    assert stats["dead"] == 1
    assert stats["failed"] == 2


def test_reap_visibility_timeout(mock_time):
    job_id, _ = queue.enqueue("sleep")
    claimed = queue.claim()
    assert claimed == job_id

    # Initially before timeout, reap should do nothing
    requeued, dead = queue.reap()
    assert requeued == 0
    assert dead == 0

    # Advance time past visibility timeout
    mock_time(config.VISIBILITY_TIMEOUT + 5)

    # Reaper should reclaim expired job
    requeued, dead = queue.reap()
    assert requeued == 1
    assert dead == 0

    record = queue.get_job(job_id)
    assert record["status"] == "ready"
    assert record["attempts"] == 1
    assert "visibility timeout" in record["last_error"]


def test_reap_poison_job_to_dead_letter(mock_time, monkeypatch):
    monkeypatch.setattr(config, "MAX_ATTEMPTS", 2)

    job_id, _ = queue.enqueue("sleep")
    queue.claim()

    # Expire attempt 1
    mock_time(config.VISIBILITY_TIMEOUT + 5)
    requeued, dead = queue.reap()
    assert requeued == 1
    assert dead == 0

    # Claim again and expire attempt 2
    queue.claim()
    mock_time(config.VISIBILITY_TIMEOUT + 5)
    requeued, dead = queue.reap()
    assert requeued == 0
    assert dead == 1

    record = queue.get_job(job_id)
    assert record["status"] == "dead"
    assert record["attempts"] == 2
    assert "max attempts reached" in record["last_error"]


def test_delayed_job_promotion(mock_time):
    # Enqueue a job delayed into future
    job_id, created = queue.enqueue("sleep", delay_until=1100.0)
    assert created is True

    record = queue.get_job(job_id)
    assert record["status"] == "scheduled"

    # Not due yet at t=1000
    assert queue.promote() == 0
    assert queue.claim() is None

    # Advance time to t=1101
    mock_time(101.0)
    promoted = queue.promote()
    assert promoted == 1

    # Should now be claimable
    claimed_id = queue.claim()
    assert claimed_id == job_id

