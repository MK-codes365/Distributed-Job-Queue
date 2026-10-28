"""Tests for worker job execution and outcome handling."""

import pytest
from jobqueue import queue, worker


def test_worker_process_success():
    job_id, _ = queue.enqueue("sleep", payload={"seconds": 0.01})
    claimed = queue.claim()
    assert claimed == job_id

    worker._process(job_id)

    record = queue.get_job(job_id)
    assert record["status"] == "completed"
    assert "completed_at" in record


def test_worker_process_failure():
    # Force flaky handler to fail 100% of the time
    job_id, _ = queue.enqueue("flaky", payload={"fail_rate": 1.0})
    claimed = queue.claim()
    assert claimed == job_id

    worker._process(job_id)

    record = queue.get_job(job_id)
    assert record["status"] == "scheduled"
    assert record["attempts"] == 1
    assert "flaky job rolled a failure" in record["last_error"]


def test_worker_process_missing_record():
    # Calling process on an expired or nonexistent job id should not raise
    worker._process("nonexistent-vanished-id")

