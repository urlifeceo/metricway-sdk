from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher, Router
from aiogram.types import Chat, Message, Update, User

from metricway import MetricsClient, TrafficSource, setup_aiogram_metrics
from metricway.traffic import start_id, traffic_record

NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def record(payload="", **kwargs):
    return traffic_record(
        456, payload, "", "", bot_id=123, chat_id=456, message_id=7, occurred_at=NOW, **kwargs
    )


def test_stable_identity_and_classification():
    assert start_id(123, 456, 7) == "486420a2-9768-525d-b58e-2bc0a301f743"
    direct = record()
    assert direct["source_kind"] == "direct"
    assert direct["event_id"] == record("partner_a")["event_id"]
    assert start_id(123, 456, 7) != start_id(123, 456, 8)
    assert record("direct")["source_kind"] == "link"
    assert record(" Partner_A ")["source"] == "Partner_A"
    assert record("not a link")["resolution_reason"] == "invalid_payload"


def test_resolver_service_error_and_fallback():
    assert record("ref_123", resolver=lambda p: TrafficSource("referral"))["source"] == "referral"
    assert (
        record("invoice_1", resolver=lambda p: TrafficSource(purpose="service"))["source"]
        == "unknown"
    )
    assert record("other", resolver=lambda p: None)["source"] == "other"

    def broken(payload):
        raise RuntimeError("do not log this payload")

    assert record("other", resolver=broken)["resolution_reason"] == "resolver_error"


def test_legacy_and_v2_transport():
    client = MetricsClient("https://example.com", "test")
    client.track_traffic(456, "legacy")
    client.track_traffic(456, bot_id=123, chat_id=456, message_id=7, occurred_at=NOW)
    assert client._queue.get_nowait()[0] == "traffic"
    assert client._queue.get_nowait()[0] == "traffic/v2"
    with pytest.raises(ValueError):
        client.track_traffic(
            456, utm_source="ads", bot_id=123, chat_id=456, message_id=7, occurred_at=NOW
        )


@pytest.mark.asyncio
async def test_automatic_starts_and_nested_middleware_do_not_duplicate():
    client = MetricsClient("https://example.com", "test")
    dp, child = Dispatcher(), Router()
    dp.include_router(child)
    setup_aiogram_metrics(dp, client)
    setup_aiogram_metrics(dp, client)
    setup_aiogram_metrics(child, client)
    bot = Bot("123:ABC")
    bot.me = AsyncMock(
        return_value=User(id=123, is_bot=True, first_name="Bot", username="ExampleBot")
    )
    try:
        for index, text in enumerate(
            [
                "/start",
                "/start partner_a",
                "/start partner_b",
                "/help",
                "/start@OTHER_BOT ignored",
                "/start@ExampleBot tagged",
            ],
            1,
        ):
            message = Message(
                message_id=index,
                date=NOW,
                chat=Chat(id=456, type="private"),
                from_user=User(id=456, is_bot=False, first_name="Test"),
                text=text,
            )
            await dp.feed_update(bot, Update(update_id=index, message=message))
        items = list(client._queue._queue)
        traffic = [r for table, r in items if table == "traffic/v2"]
        assert len(traffic) == 4
        assert [r["source"] for r in traffic] == ["direct", "partner_a", "partner_b", "tagged"]
        assert len([r for table, r in items if table == "events"]) == 6
        # Same Telegram message after a process retry has the same identity.
        client.track_traffic(456, bot_id=123, chat_id=456, message_id=1, occurred_at=NOW)
        assert list(client._queue._queue)[-1][1]["event_id"] == traffic[0]["event_id"]
    finally:
        await bot.session.close()


@pytest.mark.asyncio
async def test_groups_and_bot_senders_are_activity_only():
    client = MetricsClient("https://example.com", "test")
    dp = Dispatcher()
    setup_aiogram_metrics(dp, client)
    bot = Bot("123:ABC")
    try:
        for index, chat, user in [
            (1, Chat(id=-100, type="group"), User(id=456, is_bot=False, first_name="Human")),
            (2, Chat(id=456, type="private"), User(id=456, is_bot=True, first_name="Bot")),
        ]:
            await dp.feed_update(
                bot,
                Update(
                    update_id=index,
                    message=Message(
                        message_id=index, date=NOW, chat=chat, from_user=user, text="/start partner"
                    ),
                ),
            )
        items = list(client._queue._queue)
        assert [table for table, _ in items] == ["events", "events"]
        import json

        payloads = [json.loads(record["payload"]) for _, record in items]
        assert payloads[0]["chat_type"] == "group"
        assert payloads[1]["is_bot"] is True
    finally:
        await bot.session.close()
