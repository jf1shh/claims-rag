from __future__ import annotations

import json
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

import boto3


@dataclass(frozen=True)
class QueueMessage:
    """A message dequeued from a queue, with everything the consumer needs to
    process it and report the outcome (ack / retry / dead-letter)."""

    message_id: str
    payload: dict[str, Any]
    receipt_handle: str | None = None
    receive_count: int = 0
    # In-process only: monotonic seconds when the message becomes visible again
    # after a reject (the SQS analog is the visibility timeout).
    next_visible_at: float = 0.0
    # In-process only: the original body, kept so a dead-lettered message can be
    # inspected/forwarded verbatim (SQS returns the body on receive).
    body: str | None = None


class Queue(ABC):
    """Contract every queue backend must implement.

    Semantics mirror SQS standard queues so the seam stays provider-neutral:
    ``dequeue`` hands a message to exactly one consumer for the duration of its
    visibility window; ``ack`` confirms durable processing (message gone);
    ``reject`` makes the message visible again for a retry (in-process: after a
    backoff; SQS: via ``change_message_visibility``); ``dead_letter`` moves a
    message that exhausted its retries to the dead-letter queue when one is
    configured. Consumers must ack or reject/dead-letter every message they
    dequeue -- an unacked message becomes visible again (SQS visibility timeout;
    in-process: never, by design, since it is a dev/test loopback).

    Implementations must be thread-safe: a worker may poll from one thread while
    producers enqueue from request threads.
    """

    @abstractmethod
    def enqueue(self, payload: dict[str, Any], **kwargs: Any) -> str:
        """Publishes a message; returns its message_id."""

    @abstractmethod
    def dequeue(self) -> QueueMessage | None:
        """Returns the next visible message, or None when the queue is empty."""

    @abstractmethod
    def ack(self, message: QueueMessage) -> None:
        """Confirms the message was processed durably; removes it from the queue."""

    @abstractmethod
    def reject(self, message: QueueMessage, backoff_seconds: float) -> None:
        """Makes the message visible again for a retry (delayed by backoff_seconds)."""

    @abstractmethod
    def dead_letter(self, message: QueueMessage) -> None:
        """Moves the message to the dead-letter queue (or drops it if none is configured)."""

    @abstractmethod
    def close(self) -> None:
        """Releases any backend resources."""


class InProcessQueue(Queue):
    """In-memory queue for local development and hermetic tests.

    Not durable (messages are lost on process exit) and has no visibility
    timeout -- once dequeued a message stays in flight until acked/rejected/
    dead-lettered, so a crash mid-processing loses it. That is the deliberate
    dev-loopback tradeoff; production uses SQSQueue where SQS's visibility
    timeout and redrive handle crashed consumers. Retry/backoff semantics are
    identical so behavior tested here transfers to SQS.
    """

    def __init__(self, now_fn: Callable[[], float] | None = None):
        self._now = now_fn or time.monotonic
        self._pending: list[QueueMessage] = []
        self._in_flight: dict[str, QueueMessage] = {}
        self._dead_letters: list[QueueMessage] = []
        self._lock = Lock()

    # -- introspection helpers (tests / observability) -------------------- #
    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def in_flight_count(self) -> int:
        with self._lock:
            return len(self._in_flight)

    @property
    def dead_letter_count(self) -> int:
        with self._lock:
            return len(self._dead_letters)

    def dead_letters(self) -> list[QueueMessage]:
        with self._lock:
            return list(self._dead_letters)

    # -- Queue interface -------------------------------------------------- #
    def enqueue(self, payload: dict[str, Any], **kwargs: Any) -> str:
        delay_seconds = float(kwargs.get("delay_seconds", 0) or 0)
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be non-negative")
        message_id = f"msg_{uuid.uuid4().hex}"
        message = QueueMessage(
            message_id=message_id,
            payload=dict(payload),
            receive_count=0,
            next_visible_at=self._now() + delay_seconds,
        )
        with self._lock:
            self._pending.append(message)
        return message_id

    def dequeue(self) -> QueueMessage | None:
        now = self._now()
        with self._lock:
            for index, message in enumerate(self._pending):
                if message.next_visible_at <= now:
                    del self._pending[index]
                    self._in_flight[message.message_id] = message
                    return message
        return None

    def ack(self, message: QueueMessage) -> None:
        with self._lock:
            self._in_flight.pop(message.message_id, None)

    def reject(self, message: QueueMessage, backoff_seconds: float) -> None:
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")
        with self._lock:
            in_flight = self._in_flight.pop(message.message_id, None)
            if in_flight is None:
                return
            retried = QueueMessage(
                message_id=in_flight.message_id,
                payload=in_flight.payload,
                receipt_handle=in_flight.receipt_handle,
                receive_count=in_flight.receive_count + 1,
                next_visible_at=self._now() + backoff_seconds,
                body=in_flight.body,
            )
            self._pending.append(retried)

    def dead_letter(self, message: QueueMessage) -> None:
        with self._lock:
            in_flight = self._in_flight.pop(message.message_id, None)
            if in_flight is None:
                return
            self._dead_letters.append(in_flight)

    def close(self) -> None:
        pass


class SQSQueue(Queue):
    """AWS SQS standard-queue adapter.

    Retry semantics map directly onto SQS primitives: ``reject`` sets the
    message's visibility timeout (0 for immediate retry, else a backoff),
    and SQS's own ``ApproximateReceiveCount`` drives the retry budget. When a
    ``dlq_url`` is configured, ``dead_letter`` forwards the message there and
    removes it from the source queue; without one, the message is dropped
    (matching the in-process adapter). Usable unchanged against real SQS or
    moto for hermetic tests, mirroring S3DocumentBlobStore.
    """

    def __init__(
        self,
        queue_url: str,
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        dlq_url: str | None = None,
        visibility_timeout: int = 30,
        wait_time_seconds: int = 0,
    ):
        if not queue_url.strip():
            raise ValueError("SQS queue URL must not be empty")
        self.queue_url = queue_url
        self.dlq_url = dlq_url
        self.visibility_timeout = visibility_timeout
        self.wait_time_seconds = wait_time_seconds
        client_kwargs: dict = {"region_name": region}
        if endpoint_url:
            client_kwargs["endpoint_url"] = endpoint_url
        self._client = boto3.client("sqs", **client_kwargs)

    def enqueue(self, payload: dict[str, Any], **kwargs: Any) -> str:
        params: dict = {
            "QueueUrl": self.queue_url,
            "MessageBody": json.dumps(payload),
        }
        delay = kwargs.get("delay_seconds")
        if delay is not None:
            if delay < 0:
                raise ValueError("delay_seconds must be non-negative")
            params["DelaySeconds"] = delay
        response = self._client.send_message(**params)
        return response["MessageId"]

    def dequeue(self) -> QueueMessage | None:
        response = self._client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=self.wait_time_seconds,
            VisibilityTimeout=self.visibility_timeout,
            AttributeNames=["ApproximateReceiveCount"],
        )
        raw = (response.get("Messages") or [None])[0]
        if raw is None:
            return None
        receive_count = int(raw.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
        return QueueMessage(
            message_id=raw["MessageId"],
            payload=json.loads(raw["Body"]),
            receipt_handle=raw["ReceiptHandle"],
            receive_count=receive_count,
            body=raw["Body"],
        )

    def ack(self, message: QueueMessage) -> None:
        if not message.receipt_handle:
            return
        self._client.delete_message(
            QueueUrl=self.queue_url,
            ReceiptHandle=message.receipt_handle,
        )

    def reject(self, message: QueueMessage, backoff_seconds: float) -> None:
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be non-negative")
        if not message.receipt_handle:
            return
        # VisibilityTimeout=0 makes the message immediately available again;
        # a positive value is the retry backoff. SQS increments
        # ApproximateReceiveCount on the next receive, driving the retry budget.
        self._client.change_message_visibility(
            QueueUrl=self.queue_url,
            ReceiptHandle=message.receipt_handle,
            VisibilityTimeout=int(backoff_seconds),
        )

    def dead_letter(self, message: QueueMessage) -> None:
        if not message.receipt_handle:
            return
        if self.dlq_url:
            self._client.send_message(
                QueueUrl=self.dlq_url,
                MessageBody=message.body or json.dumps(message.payload),
            )
        self._client.delete_message(
            QueueUrl=self.queue_url,
            ReceiptHandle=message.receipt_handle,
        )

    def close(self) -> None:
        pass
