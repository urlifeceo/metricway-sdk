import traceback
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery

from tgmetrics.client import MetricsClient


class AiogramMetricsMiddleware(BaseMiddleware):
    def __init__(self, metrics_client: MetricsClient):
        super().__init__()
        self.client = metrics_client

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user_id = 0
        chat_id = 0
        update_type = "unknown"
        payload: Dict[str, Any] = {}

        if isinstance(event, Message):
            update_type = "message"
            user_id = event.from_user.id if event.from_user else 0
            chat_id = event.chat.id
            payload["text"] = event.text or ""

            if event.text and event.text.startswith("/start"):
                parts = event.text.split(maxsplit=1)
                if len(parts) > 1:
                    start_payload = parts[1]
                    payload["start_payload"] = start_payload
                    self.client.track_traffic(
                        user_id=user_id,
                        start_payload=start_payload,
                    )

        elif isinstance(event, CallbackQuery):
            update_type = "callback_query"
            user_id = event.from_user.id
            chat_id = event.message.chat.id if event.message else 0
            payload["callback_data"] = event.data or ""

        handler_obj = data.get("handler")
        handler_name = handler_obj.callback.__name__ if handler_obj else "unknown"

        if user_id != 0:
            self.client.track_event(
                user_id=user_id,
                chat_id=chat_id,
                handler=handler_name,
                update_type=update_type,
                payload=payload,
            )

        try:
            return await handler(event, data)
        except Exception as exc:
            if user_id != 0:
                self.client.track_error(
                    user_id=user_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    stack=traceback.format_exc(),
                )
            raise exc