"""Queue seam tests (Phase 3 async ingestion).

In-process semantics are tested with an injectable clock so retry/backoff
behavior is deterministic; the SQS adapter is exercised against moto, mirroring
the hermetic S3 blob-store tests.
"""

import boto3
import pytest
from moto import mock_aws

from backend.queue import InProcessQueue, SQSQueue


class _Clock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_queue(**kwargs):
    return InProcessQueue(now_fn=_Clock(), **kwargs)


# ---------------------------------------------------------------------------
# InProcessQueue
# ---------------------------------------------------------------------------

def test_given_message_when_dequeued_then_payload_and_receive_count_are_returned():
    queue = make_queue()
    queue.enqueue({"job_id": "job_1", "filename": "a.txt"})
    message = queue.dequeue()
    assert message is not None
    assert message.payload == {"job_id": "job_1", "filename": "a.txt"}
    assert message.receive_count == 0
    # Dequeued messages move to in-flight; nothing is left pending.
    assert queue.pending_count == 0
    assert queue.in_flight_count == 1


def test_given_empty_queue_when_dequeued_then_none():
    queue = make_queue()
    assert queue.dequeue() is None


def test_given_message_when_acked_then_it_is_gone():
    queue = make_queue()
    queue.enqueue({"job_id": "job_1"})
    message = queue.dequeue()
    queue.ack(message)
    assert queue.in_flight_count == 0
    assert queue.dequeue() is None


def test_given_delayed_message_when_dequeued_before_delay_then_not_visible():
    clock = _Clock()
    queue = InProcessQueue(now_fn=clock)
    queue.enqueue({"job_id": "job_1"}, delay_seconds=10)
    assert queue.dequeue() is None
    clock.advance(10)
    assert queue.dequeue() is not None


def test_given_rejected_message_when_backoff_not_elapsed_then_not_visible_then_visible():
    clock = _Clock()
    queue = InProcessQueue(now_fn=clock)
    queue.enqueue({"job_id": "job_1"})
    message = queue.dequeue()
    queue.reject(message, backoff_seconds=5)
    assert queue.in_flight_count == 0
    assert queue.dequeue() is None
    clock.advance(5)
    retried = queue.dequeue()
    assert retried is not None
    assert retried.payload == {"job_id": "job_1"}
    assert retried.receive_count == 1


def test_given_zero_backoff_when_rejected_then_immediately_visible():
    queue = make_queue()
    queue.enqueue({"job_id": "job_1"})
    message = queue.dequeue()
    queue.reject(message, backoff_seconds=0)
    retried = queue.dequeue()
    assert retried is not None
    assert retried.receive_count == 1


def test_given_message_when_dead_lettered_then_moved_to_dlq():
    queue = make_queue()
    queue.enqueue({"job_id": "job_1"})
    message = queue.dequeue()
    queue.dead_letter(message)
    assert queue.dead_letter_count == 1
    assert queue.in_flight_count == 0
    assert queue.dead_letters()[0].payload == {"job_id": "job_1"}
    assert queue.dequeue() is None


def test_given_negative_backoff_when_rejected_then_rejected():
    queue = make_queue()
    queue.enqueue({"job_id": "job_1"})
    message = queue.dequeue()
    with pytest.raises(ValueError):
        queue.reject(message, backoff_seconds=-1)


# ---------------------------------------------------------------------------
# SQSQueue (moto)
# ---------------------------------------------------------------------------

@pytest.fixture
def sqs():
    """An in-process SQS mock with a source queue and a DLQ; yields (client, queue)."""
    with mock_aws():
        client = boto3.client("sqs", region_name="us-east-1")
        source_url = client.create_queue(QueueName="ingest-queue")["QueueUrl"]
        dlq_url = client.create_queue(QueueName="ingest-dlq")["QueueUrl"]
        queue = SQSQueue(queue_url=source_url, region="us-east-1", dlq_url=dlq_url)
        yield client, queue, source_url, dlq_url


def test_given_message_when_sent_then_received_with_receipt_handle(sqs):
    _, queue, _, _ = sqs
    queue.enqueue({"job_id": "job_1", "filename": "a.txt"})
    message = queue.dequeue()
    assert message is not None
    assert message.payload == {"job_id": "job_1", "filename": "a.txt"}
    assert message.receipt_handle
    assert message.receive_count >= 1


def test_given_message_when_acked_then_no_longer_received(sqs):
    _, queue, _, _ = sqs
    queue.enqueue({"job_id": "job_1"})
    message = queue.dequeue()
    queue.ack(message)
    assert queue.dequeue() is None


def test_given_message_when_rejected_with_zero_visibility_then_received_again(sqs):
    _, queue, _, _ = sqs
    queue.enqueue({"job_id": "job_1"})
    first = queue.dequeue()
    queue.reject(first, backoff_seconds=0)
    second = queue.dequeue()
    assert second is not None
    assert second.payload == {"job_id": "job_1"}
    # SQS's ApproximateReceiveCount tracks the retry budget.
    assert second.receive_count >= first.receive_count


def test_given_message_when_dead_lettered_then_lands_in_dlq(sqs):
    _, queue, _, dlq_url = sqs
    queue.enqueue({"job_id": "job_1"})
    message = queue.dequeue()
    queue.dead_letter(message)
    # Source queue is now empty; the message is in the DLQ.
    assert queue.dequeue() is None
    client = boto3.client("sqs", region_name="us-east-1")
    dlq_message = client.receive_message(QueueUrl=dlq_url)["Messages"][0]
    assert '"job_id": "job_1"' in dlq_message["Body"]


def test_given_message_when_dead_lettered_without_dlq_then_dropped(sqs):
    _, _, source_url, _ = sqs
    queue = SQSQueue(queue_url=source_url, region="us-east-1")
    queue.enqueue({"job_id": "job_1"})
    message = queue.dequeue()
    queue.dead_letter(message)
    assert queue.dequeue() is None


def test_given_empty_queue_url_then_rejected():
    with pytest.raises(ValueError):
        SQSQueue(queue_url="", region="us-east-1")
