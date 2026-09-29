"""Asynchronous, best-effort metrics delivery."""

import asyncio
import json
import logging
import math
from collections import defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

from metricway.models import ErrorDTO, EventDTO, PurchaseDTO, TrafficDTO

logger = logging.getLogger("metricway")
MAX_BATCH_SIZE = 500
MAX_REQUEST_BYTES = 1024 * 1024
TABLES = ("events", "traffic", "errors", "purchases")
Record = tuple[str, dict[str, Any]]


class MetricsClient:
    """Queue metrics in memory and send them without blocking bot handlers."""

    def __init__(
        self,
        api_url: str,
        project_token: str,
        batch_size: int = MAX_BATCH_SIZE,
        flush_interval: float = 3.0,
        max_queue_size: int = 10_000,
        max_retries: int = 5,
        shutdown_timeout: float = 10.0,
    ) -> None:
        if not api_url or not api_url.startswith(("https://", "http://")):
            raise ValueError("api_url must be an HTTP(S) URL")
        if not project_token:
            raise ValueError("project_token must not be empty")
        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            raise ValueError("batch_size must be between 1 and 500")
        if flush_interval <= 0 or max_queue_size <= 0 or max_retries < 0:
            raise ValueError(
                "flush_interval and max_queue_size must be positive; max_retries cannot be negative"
            )
        if shutdown_timeout <= 0:
            raise ValueError("shutdown_timeout must be positive")

        self.url = api_url.rstrip("/")
        self.project_token = project_token
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.max_retries = max_retries
        self.shutdown_timeout = shutdown_timeout
        self._queue: asyncio.Queue[Record] = asyncio.Queue(maxsize=max_queue_size)
        self._wake = asyncio.Event()
        self._worker_task: asyncio.Task[None] | None = None
        self._session: aiohttp.ClientSession | None = None
        self._inflight_count = 0
        self._closed = False
        self._close_lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the worker. Repeated calls are safe."""
        if self._closed:
            raise RuntimeError("MetricsClient cannot be restarted after close()")
        if self._worker_task is not None:
            return
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))
        self._worker_task = asyncio.create_task(self._flusher_loop())
        logger.info("MetricsClient started")

    async def close(self) -> None:
        """Drain pending records for at most shutdown_timeout seconds."""
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            if self._worker_task is None:
                remaining = self._queue.qsize()
                if remaining:
                    logger.warning("Discarding %d metrics: client was never started", remaining)
                return

            self._wake.set()
            try:
                await asyncio.wait_for(self._queue.join(), timeout=self.shutdown_timeout)
            except asyncio.TimeoutError:
                logger.warning(
                    "MetricsClient shutdown timed out; %d metrics were not sent",
                    self._queue.qsize() + self._inflight_count,
                )
            finally:
                self._worker_task.cancel()
                try:
                    await self._worker_task
                except asyncio.CancelledError:
                    pass
                if self._session is not None:
                    await self._session.close()
                logger.info("MetricsClient closed")

    def track_event(
        self, user_id: int, chat_id: int, handler: str, update_type: str, payload: dict[str, Any]
    ) -> None:
        try:
            dto = EventDTO.create(
                self.project_token, user_id, chat_id, handler, update_type, payload
            )
        except (TypeError, ValueError):
            logger.warning("Discarding events metric: payload is not JSON serializable")
            return
        self._enqueue("events", dto.to_dict())

    def track_traffic(
        self,
        user_id: int,
        start_payload: str = "",
        utm_source: str = "",
        utm_campaign: str = "",
        referrer: str = "",
    ) -> None:
        dto = TrafficDTO.create(
            self.project_token, user_id, start_payload, utm_source, utm_campaign, referrer
        )
        self._enqueue("traffic", dto.to_dict())

    def track_error(
        self, user_id: int, error_type: str, error_message: str, stack: str = ""
    ) -> None:
        dto = ErrorDTO.create(self.project_token, user_id, error_type, error_message, stack)
        self._enqueue("errors", dto.to_dict())

    def track_purchase(
        self,
        user_id: int,
        amount: float,
        currency: str = "RUB",
        product_id: str = "",
        payment_provider: str = "",
    ) -> None:
        dto = PurchaseDTO.create(
            self.project_token, user_id, amount, currency, product_id, payment_provider
        )
        self._enqueue("purchases", dto.to_dict())

    def _enqueue(self, table: str, item: dict[str, Any]) -> None:
        if self._closed:
            logger.warning("Discarding %s metric: client is closed", table)
            return
        try:
            body = self._encode([item])
        except (TypeError, ValueError):
            logger.warning("Discarding %s metric: record is not JSON serializable", table)
            return
        if len(body) > MAX_REQUEST_BYTES:
            logger.warning("Discarding %s metric: record exceeds collector request limit", table)
            return
        try:
            self._queue.put_nowait((table, item))
        except asyncio.QueueFull:
            logger.warning("Discarding %s metric: queue is full", table)
            return
        if self._queue.qsize() >= self.batch_size:
            self._wake.set()

    async def _flusher_loop(self) -> None:
        while True:
            try:
                if not self._closed:
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=self.flush_interval)
                    except asyncio.TimeoutError:
                        pass
                self._wake.clear()
                while not self._queue.empty():
                    await self._flush()
                if self._closed:
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Unexpected error in metrics flusher")
                await asyncio.sleep(1)

    async def _flush(self) -> None:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        count = 0
        while count < self.batch_size:
            try:
                table, item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            grouped[table].append(item)
            self._inflight_count += 1
            count += 1

        for table in TABLES:
            chunk: list[dict[str, Any]] = []
            for item in grouped[table]:
                if chunk and len(self._encode([*chunk, item])) > MAX_REQUEST_BYTES:
                    await self._finish_batch(table, chunk)
                    chunk = []
                chunk.append(item)
            if chunk:
                await self._finish_batch(table, chunk)

    async def _finish_batch(self, table: str, items: list[dict[str, Any]]) -> None:
        try:
            await self._send_batch(table, items)
        finally:
            for _ in items:
                self._queue.task_done()
            self._inflight_count -= len(items)

    async def _send_batch(self, table: str, items: list[dict[str, Any]]) -> None:
        assert self._session is not None
        endpoint = f"{self.url}/collector/track/{table}"
        body = self._encode(items)
        headers = {"Content-Type": "application/json", "X-Project-Token": self.project_token}
        for attempt in range(self.max_retries + 1):
            retry_after = 0.0
            try:
                async with self._session.post(endpoint, data=body, headers=headers) as response:
                    if 200 <= response.status < 300:
                        return
                    if response.status == 429 or response.status >= 500:
                        retry_after = self._retry_after(response.headers.get("Retry-After"))
                    else:
                        logger.error(
                            "Discarding %d %s metrics: collector returned HTTP %d",
                            len(items),
                            table,
                            response.status,
                        )
                        return
                    status = f"HTTP {response.status}"
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                status = type(exc).__name__

            if attempt == self.max_retries:
                logger.error(
                    "Discarding %d %s metrics after %d attempts (%s)",
                    len(items),
                    table,
                    attempt + 1,
                    status,
                )
                return
            await asyncio.sleep(max(min(2**attempt, 60), retry_after))

    @staticmethod
    def _encode(items: list[dict[str, Any]]) -> bytes:
        return json.dumps(items, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode(
            "utf-8"
        )

    @staticmethod
    def _retry_after(value: str | None) -> float:
        if not value:
            return 0.0
        try:
            seconds = float(value)
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                seconds = (date - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return 0.0
        if not math.isfinite(seconds):
            return 0.0
        return min(max(seconds, 0.0), 60.0)
