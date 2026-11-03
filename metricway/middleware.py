"""Optional aiogram 3 integration, including automatic private-chat traffic."""

import logging
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from aiogram import BaseMiddleware, Router
from aiogram.types import CallbackQuery, Message, TelegramObject

from metricway.client import MetricsClient

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]
_CONTEXT_KEY = "_metricway_context"
logger = logging.getLogger("metricway")


@dataclass
class _EventContext:
    handler: str = "unknown"


class AiogramMetricsMiddleware(BaseMiddleware):
    def __init__(self, metrics_client: MetricsClient) -> None:
        super().__init__()
        self.client = metrics_client

    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        # A child router sees the parent's context; don't record the same action twice.
        if data.get(_CONTEXT_KEY) is not None:
            return await handler(event, data)
        user_id, chat_id, update_type = 0, 0, "unknown"
        payload: dict[str, Any] = {}
        bot = data.get("bot")
        if isinstance(event, Message):
            update_type = "message"
            user_id = event.from_user.id if event.from_user else 0
            chat_id = event.chat.id
            payload["text"] = event.text or ""
            payload["chat_type"] = event.chat.type
            payload["is_bot"] = bool(event.from_user and event.from_user.is_bot)
            if bot and event.from_user and not event.from_user.is_bot:
                payload.update(
                    bot_id=bot.id,
                    chat_type=event.chat.type,
                    message_id=event.message_id,
                    occurred_at=event.date.astimezone(timezone.utc).isoformat(),
                )
            parts = (event.text or "").split(maxsplit=1)
            command = parts[0] if parts and (event.text or "").startswith("/") else ""
            name, _, mention = command.partition("@")
            is_start = name == "/start"
            if is_start and mention:
                try:
                    me = await bot.me() if bot else None
                    is_start = bool(me and me.username and mention.lower() == me.username.lower())
                except Exception as exc:
                    logger.warning("Cannot verify start command recipient (%s)", type(exc).__name__)
                    is_start = False
            if (
                is_start
                and bot
                and event.chat.type == "private"
                and event.from_user
                and not event.from_user.is_bot
            ):
                argument = parts[1].strip() if len(parts) > 1 else ""
                payload["start_payload"] = argument
                try:
                    self.client.track_traffic(
                        user_id=user_id,
                        start_payload=argument,
                        bot_id=bot.id,
                        chat_id=chat_id,
                        message_id=event.message_id,
                        occurred_at=event.date,
                    )
                except (TypeError, ValueError) as exc:
                    logger.warning(
                        "Traffic start rejected (%s); check custom track_traffic adapters",
                        type(exc).__name__,
                    )
        elif isinstance(event, CallbackQuery):
            update_type = "callback_query"
            user_id = event.from_user.id
            chat_id = event.message.chat.id if event.message else 0
            payload["callback_data"] = event.data or ""
            payload["chat_type"] = event.message.chat.type if event.message else "unknown"
            payload["is_bot"] = event.from_user.is_bot
            if bot and event.message and not event.from_user.is_bot:
                payload.update(
                    bot_id=bot.id,
                    chat_type=event.message.chat.type,
                    callback_query_id=event.id,
                    occurred_at=datetime.now(timezone.utc).isoformat(),
                )
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
            data.pop(_CONTEXT_KEY, None)


class _HandlerNameMiddleware(BaseMiddleware):
    async def __call__(self, handler: Handler, event: TelegramObject, data: dict[str, Any]) -> Any:
        context, handler_obj = data.get(_CONTEXT_KEY), data.get("handler")
        if context is not None and handler_obj is not None:
            callback = getattr(handler_obj, "callback", None)
            if callback is not None:
                context.handler = getattr(callback, "__name__", "unknown")
        return await handler(event, data)


def setup_aiogram_metrics(router: Router, client: MetricsClient) -> None:
    if getattr(router, "_metricway_metrics_installed", False):
        return
    router.message.outer_middleware(AiogramMetricsMiddleware(client))
    router.callback_query.outer_middleware(AiogramMetricsMiddleware(client))
    router.message.middleware(_HandlerNameMiddleware())
    router.callback_query.middleware(_HandlerNameMiddleware())
    router._metricway_metrics_installed = True
