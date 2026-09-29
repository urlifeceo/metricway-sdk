from datetime import datetime, timezone

import pytest
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from metricway import setup_aiogram_metrics
from metricway.middleware import AiogramMetricsMiddleware


class Recorder:
    def __init__(self):
        self.events = []
        self.traffic = []
        self.errors = []

    def track_event(self, **kwargs):
        self.events.append(kwargs)

    def track_traffic(self, **kwargs):
        self.traffic.append(kwargs)

    def track_error(self, **kwargs):
        self.errors.append(kwargs)


def message(text):
    return Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=456, type="private"),
        from_user=User(id=123, is_bot=False, first_name="Test"),
        text=text,
    )


@pytest.mark.asyncio
async def test_handled_and_unhandled_messages():
    recorder = Recorder()
    dp = Dispatcher()
    setup_aiogram_metrics(dp, recorder)

    @dp.message(CommandStart())
    async def start_handler(msg: Message):
        return None

    bot = Bot("123:ABC")
    try:
        await dp.feed_update(bot, Update(update_id=1, message=message("/start campaign")))
        await dp.feed_update(bot, Update(update_id=2, message=message("unhandled")))
    finally:
        await bot.session.close()

    assert [event["handler"] for event in recorder.events] == [
        "start_handler",
        "unknown",
    ]
    assert [event["update_type"] for event in recorder.events] == ["message", "message"]
    assert recorder.traffic == [{"user_id": 123, "start_payload": "campaign"}]
    assert recorder.events[0]["payload"]["text"] == "/start campaign"


@pytest.mark.asyncio
async def test_callback_query_without_handler():
    recorder = Recorder()
    dp = Dispatcher()
    setup_aiogram_metrics(dp, recorder)
    bot = Bot("123:ABC")
    try:
        callback = CallbackQuery(
            id="call-1",
            from_user=User(id=123, is_bot=False, first_name="Test"),
            chat_instance="chat",
            message=message("button"),
            data="pressed",
        )
        await dp.feed_update(bot, Update(update_id=3, callback_query=callback))
    finally:
        await bot.session.close()

    assert recorder.events[0]["handler"] == "unknown"
    assert recorder.events[0]["chat_id"] == 456
    assert recorder.events[0]["payload"] == {"callback_data": "pressed"}


@pytest.mark.asyncio
async def test_handler_exception_preserves_traceback():
    recorder = Recorder()
    middleware = AiogramMetricsMiddleware(recorder)

    async def failing_handler(event, data):
        raise ValueError("failure")

    with pytest.raises(ValueError) as error:
        await middleware(failing_handler, message("hi"), {})
    assert recorder.errors[0]["error_type"] == "ValueError"
    assert "failing_handler" in recorder.errors[0]["stack"]
    assert error.value.__traceback__ is not None
    assert recorder.events[0]["handler"] == "unknown"


@pytest.mark.asyncio
async def test_start_command_detection():
    recorder = Recorder()
    middleware = AiogramMetricsMiddleware(recorder)

    async def handler(event, data):
        return None

    await middleware(handler, message("/startup campaign"), {})
    await middleware(handler, message("   "), {})
    await middleware(handler, message("/start@ExampleBot campaign"), {})
    assert recorder.traffic == [{"user_id": 123, "start_payload": "campaign"}]
