# MetricWay Python SDK

[![CI](https://github.com/urlifeceo/metricway-sdk/actions/workflows/ci.yml/badge.svg)](https://github.com/urlifeceo/metricway-sdk/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/metricway-sdk.svg)](https://pypi.org/project/metricway-sdk/)
[![Python](https://img.shields.io/pypi/pyversions/metricway-sdk.svg)](https://pypi.org/project/metricway-sdk/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An asynchronous Python SDK for sending Telegram bot product events to [MetricWay](https://metricway.tech). It supports manual tracking and automatic aiogram 3 integration without making bot handlers wait for HTTP requests.

## Installation and quick start

Requires **Python 3.10 or later**.

Create a project in MetricWay and store its token in the `METRICWAY_PROJECT_TOKEN` environment variable. Store your Telegram bot token in `TELEGRAM_BOT_TOKEN`.

For an aiogram bot, install:

```bash
python -m pip install "metricway-sdk[aiogram]"
```

```python
import asyncio
import os

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message
from metricway import MetricsClient, setup_aiogram_metrics

metrics = MetricsClient(
    api_url="https://metricway.tech",
    project_token=os.environ["METRICWAY_PROJECT_TOKEN"],
)
dp = Dispatcher()
setup_aiogram_metrics(dp, metrics)


@dp.message(CommandStart())
async def start_command(message: Message) -> None:
    await message.answer("Hello!")


async def on_startup(bot: Bot) -> None:
    await metrics.start()


async def on_shutdown(bot: Bot) -> None:
    await metrics.close()


dp.startup.register(on_startup)
dp.shutdown.register(on_shutdown)


async def main() -> None:
    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
```

Register `setup_aiogram_metrics` once on the router. It tracks incoming messages and callback queries even when no handler matches; the handler name is then `unknown`.

For manual tracking without aiogram, install the base package:

```bash
python -m pip install metricway-sdk
```

## How it works

```mermaid
flowchart LR
    Bot[Telegram bot] --> SDK[MetricWay SDK]
    SDK --> Queue[Bounded in-memory queue]
    Queue --> Batch[Batching and retry worker]
    Batch --> Collector[MetricWay collector]
```

The client separates event collection from network delivery. Public `track_*` methods prepare records and add them to a bounded queue. A background asyncio task groups records into batches and sends them to the collector.

- **Non-blocking collection:** bot handlers do not wait for HTTP requests.
- **Bounded queue:** memory use is limited, with defined behavior under overload.
- **Batching:** up to 500 records and at most 1 MiB of JSON per request.
- **Retries:** network errors, HTTP 429, and 5xx responses are retried with backoff and support for `Retry-After`.
- **Graceful shutdown:** `close()` attempts to deliver pending records within the configured timeout.
- **Optional aiogram integration:** the base package does not require aiogram.

## Manual tracking

Call `await metrics.start()` before delivery is needed and `await metrics.close()` during shutdown. The `track_*` methods are synchronous: they enqueue records without waiting for the network.

```python
metrics.track_event(
    user_id=123,
    chat_id=456,
    handler="trial_started",
    update_type="trial",
    payload={"product_id": "pro_month"},
)
metrics.track_traffic(
    user_id=123,
    start_payload="campaign_a",
    utm_source="telegram",
)
metrics.track_error(
    user_id=123,
    error_type="PaymentError",
    error_message="Payment failed",
    stack="",
)
metrics.track_purchase(
    user_id=123,
    amount=990.0,
    currency="RUB",
    product_id="pro_month",
    payment_provider="telegram_payments",
)
```

Track a purchase only after payment is confirmed, and prevent duplicate tracking in your application. The SDK does not convert currencies.

## Automatically collected data

| Event | Data sent |
| --- | --- |
| Message | User ID, chat ID, handler name, update type, and full message text |
| Callback query | User ID, chat ID, handler name, update type, and callback data |
| `/start <payload>` | An additional traffic record containing the start payload |
| Handler error | Error type, message, and full stack trace; the exception is re-raised |

**Message text, callback data, and stack traces may contain personal data or secrets.** Review your bot's data handling before enabling automatic collection. Keep passwords, payment details, and other secrets out of payloads and error messages.

Keep the project token on the server. The SDK sends it in the `X-Project-Token` header and in each record, and does not print it in its own logs.

## Configuration and delivery limits

| Constructor option | Default | Purpose |
| --- | --- | --- |
| `batch_size` | `500` | Maximum records collected for a flush; must be between 1 and 500 |
| `flush_interval` | `3.0` | Seconds between scheduled flushes |
| `max_queue_size` | `10000` | Maximum queued records |
| `max_retries` | `5` | Retry attempts after the first request |
| `shutdown_timeout` | `10.0` | Maximum seconds spent draining the queue on shutdown |

Requests are limited to 500 records and 1 MiB of JSON. Network errors, HTTP 429, and 5xx responses are retried up to five times after the initial attempt with increasing delays. `Retry-After` is honored up to 60 seconds. Other unsuccessful HTTP responses are discarded without retries.

When the queue is full, new records are dropped. After `close()`, new records are also dropped.

The queue is **in memory only**. Process failure, exhausted retries, or resource limits can cause event loss. A successful HTTP response means the collector accepted the batch; it does not guarantee durable storage. Enable the `metricway` logger to inspect delivery warnings.

## Troubleshooting

1. Check the project token and ensure `start()` is called when the bot starts.
2. Check connectivity to the collector at `https://metricway.tech/collector/` from the bot server.
3. Inspect `metricway` logs: 401 indicates an authentication problem, 429 a rate limit, and 413 an oversized request.
4. Send `/start test_campaign` and check the event and traffic source in the dashboard.

## Development and CI

From the repository root, create and activate a virtual environment, then install development dependencies:

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[aiogram,dev]"
python -m ruff check .
python -m ruff format --check .
python -m pytest --cov=metricway --cov-report=term-missing
python -m build
python -m twine check dist/*
```

GitHub Actions checks Python 3.10–3.14, Ruff, pytest with an 85% coverage threshold, wheel/sdist builds, and `twine check`. A separate release workflow publishes version tags to PyPI through Trusted Publishing.

Additional contributor and release instructions are currently in Russian: [CONTRIBUTING.md](CONTRIBUTING.md) and [RELEASING.md](RELEASING.md).

## License

[MIT](LICENSE).
