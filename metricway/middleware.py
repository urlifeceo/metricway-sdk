"""Optional aiogram 3 integration."""

import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware, Router
from aiogram.types import CallbackQuery, Message, TelegramObject

from metricway.client import MetricsClient

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]
_CONTEXT_KEY = "_metricway_context"


@dataclass
class _EventContext:
    handler: str = "unknown"


class AiogramMetricsMiddleware(BaseMiddleware):
    """Outer middleware for message and callback observers."""

    def __init__(self, metrics_client: MetricsClient) -> None:
        super().__init__()
        self.client = metrics_client

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        user_id = 0
        chat_id = 0
        update_type = "unknown"
        payload: dict[str, Any] = {}

        if isinstance(event, Message):
            update_type = "message"
            user_id = event.from_user.id if event.from_user else 0
            chat_id = event.chat.id
            payload["text"] = event.text or ""
            parts = (event.text or "").split(maxsplit=1)
            if parts and parts[0].split("@", 1)[0] == "/start" and len(parts) > 1:
                payload["start_payload"] = parts[1]
                if user_id:
                    self.client.track_traffic(user_id=user_id, start_payload=parts[1])
        elif isinstance(event, CallbackQuery):
            update_type = "callback_query"
            user_id = event.from_user.id
            chat_id = event.message.chat.id if event.message else 0
            payload["callback_data"] = event.data or ""

        context = _EventContext()
        data[_CONTEXT_KEY] = context
        try:
            return await handler(event, data)
        except Exception as exc:
            if user_id:
                self.client.track_error(
                    user_id=user_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    stack=traceback.format_exc(),
                )
            raise
        finally:
            if user_id:
                self.client.track_event(
                    user_id=user_id,
                    chat_id=chat_id,
                    handler=context.handler,
                    update_type=update_type,
                    payload=payload,
                )


class _HandlerNameMiddleware(BaseMiddleware):
    """Inner middleware sees the handler resolved by aiogram's filters."""

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        context = data.get(_CONTEXT_KEY)
        handler_obj = data.get("handler")
        if context is not None and handler_obj is not None:
            callback = getattr(handler_obj, "callback", None)
            if callback is not None:
                context.handler = getattr(callback, "__name__", "unknown")
        return await handler(event, data)


def setup_aiogram_metrics(router: Router, client: MetricsClient) -> None:
    """Register metrics for all messages and callbacks on a router."""
    router.message.outer_middleware(AiogramMetricsMiddleware(client))
    router.callback_query.outer_middleware(AiogramMetricsMiddleware(client))
    router.message.middleware(_HandlerNameMiddleware())
    router.callback_query.middleware(_HandlerNameMiddleware())
