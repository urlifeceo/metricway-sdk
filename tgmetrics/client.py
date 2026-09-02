import asyncio
import logging
import json
from typing import Any, Dict, List, Optional
import aiohttp

from tgmetrics.models import ErrorDTO, EventDTO, PurchaseDTO, TrafficDTO

logger = logging.getLogger("tgmetrics")


class MetricsClient:
    def __init__(
        self,
        api_url: str,
        project_token: str,
        batch_size: int = 500,
        flush_interval: float = 3.0,
    ):
        self.url = api_url.rstrip("/")
        self.project_token = project_token
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        self._worker_task = asyncio.create_task(self._flusher_loop())
        logger.info(f"MetricsClient started. Endpoint: {self.url}")

    async def close(self) -> None:
        logger.info("Closing MetricsClient...")
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        
        while not self._queue.empty():
            await self._flush()

        if self._session:
            await self._session.close()
        logger.info("MetricsClient closed successfully.")

    def track_event(
        self,
        user_id: int,
        chat_id: int,
        handler: str,
        update_type: str,
        payload: Dict[str, Any],
    ) -> None:
        dto = EventDTO.create(
            project_token=self.project_token,
            user_id=user_id,
            chat_id=chat_id,
            handler=handler,
            update_type=update_type,
            payload=payload,
        )
        self._queue.put_nowait(("events", dto.to_dict()))

    def track_traffic(
        self,
        user_id: int,
        start_payload: str = "",
        utm_source: str = "",
        utm_campaign: str = "",
        referrer: str = "",
    ) -> None:
        dto = TrafficDTO.create(
            project_token=self.project_token,
            user_id=user_id,
            start_payload=start_payload,
            utm_source=utm_source,
            utm_campaign=utm_campaign,
            referrer=referrer,
        )
        self._queue.put_nowait(("traffic", dto.to_dict()))

    def track_error(
        self,
        user_id: int,
        error_type: str,
        error_message: str,
        stack: str = "",
    ) -> None:
        dto = ErrorDTO.create(
            project_token=self.project_token,
            user_id=user_id,
            error_type=error_type,
            error_message=error_message,
            stack=stack,
        )
        self._queue.put_nowait(("errors", dto.to_dict()))

    def track_purchase(
        self,
        user_id: int,
        amount: float,
        currency: str = "RUB",
        product_id: str = "",
        payment_provider: str = "",
    ) -> None:
        dto = PurchaseDTO.create(
            project_token=self.project_token,
            user_id=user_id,
            amount=amount,
            currency=currency,
            product_id=product_id,
            payment_provider=payment_provider,
        )
        self._queue.put_nowait(("purchases", dto.to_dict()))

    async def _flusher_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.flush_interval)
                await self._flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in metrics flusher loop: {e}", exc_info=True)

    async def _flush(self) -> None:
        if self._queue.empty():
            return

        batches: Dict[str, List[Dict[str, Any]]] = {
            "events": [],
            "traffic": [],
            "errors": [],
            "purchases": [],
        }

        count = 0
        while not self._queue.empty() and count < self.batch_size:
            table, item = self._queue.get_nowait()
            batches[table].append(item)
            self._queue.task_done()
            count += 1

        for table, items in batches.items():
            if items:
                await self._send_batch(table, items)

    async def _send_batch(self, table: str, items: List[Dict[str, Any]]) -> None:
        if not self._session:
            return

        endpoint = f"{self.url}/collector/track/{table}"
        body_bytes = json.dumps(items, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Project-Token": self.project_token,
        }

        try:
            async with self._session.post(
                endpoint,
                data=body_bytes,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status not in (200, 201, 202):
                    text = await resp.text()
                    logger.error(f"Failed to send batch ({table}): status {resp.status}, body: {text}")
        except Exception as e:
            logger.error(f"Failed to send batch to collector ({table}): {e}")