import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
from aiohttp import web

from metricway import MetricsClient
from metricway.client import MAX_REQUEST_BYTES
from metricway.models import ErrorDTO, EventDTO, PurchaseDTO, TrafficDTO


class FakeResponse:
    def __init__(self, status=202, headers=None):
        self.status = status
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class FakeSession:
    def __init__(self, statuses=None, **_):
        self.statuses = iter(statuses or [])
        self.requests = []
        self.closed = False

    def post(self, endpoint, *, data, headers):
        self.requests.append((endpoint, data, headers))
        value = next(self.statuses, 202)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):
            return FakeResponse(*value)
        return FakeResponse(value)

    async def close(self):
        self.closed = True


def install_session(monkeypatch, statuses=None):
    session = FakeSession(statuses)
    monkeypatch.setattr("metricway.client.aiohttp.ClientSession", lambda **_: session)
    return session


def test_dto_wire_shapes():
    event = EventDTO.create("token", 1, 2, "handler", "message", {"text": "привет"})
    assert len(event.event_id) == 36
    assert json.loads(event.payload) == {"text": "привет"}
    assert set(event.to_dict()) == {
        "ts",
        "event_id",
        "project_token",
        "user_id",
        "chat_id",
        "handler",
        "update_type",
        "payload",
    }
    assert TrafficDTO.create("token", 1, "campaign").to_dict()["start_payload"] == "campaign"
    assert ErrorDTO.create("token", 1, "ValueError", "bad").to_dict()["stack"] == ""
    assert PurchaseDTO.create("token", 1, 99.0).to_dict()["currency"] == "RUB"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_url": "", "project_token": "token"},
        {"api_url": "ftp://x", "project_token": "token"},
        {"api_url": "https://x", "project_token": ""},
        {"api_url": "https://x", "project_token": "token", "batch_size": 0},
        {"api_url": "https://x", "project_token": "token", "batch_size": 501},
        {"api_url": "https://x", "project_token": "token", "max_queue_size": 0},
        {"api_url": "https://x", "project_token": "token", "max_retries": -1},
        {"api_url": "https://x", "project_token": "token", "shutdown_timeout": 0},
    ],
)
def test_config_rejected(kwargs):
    with pytest.raises(ValueError):
        MetricsClient(**kwargs)


def test_queue_limits_and_serialization(caplog):
    client = MetricsClient("https://example.test", "token", max_queue_size=1)
    with caplog.at_level(logging.WARNING, logger="metricway"):
        client.track_traffic(1)
        client.track_traffic(2)
        client.track_event(3, 4, "handler", "message", {"huge": "x" * MAX_REQUEST_BYTES})
        client.track_event(3, 4, "handler", "message", {"invalid": object()})
    assert client._queue.qsize() == 1
    assert "queue is full" in caplog.text
    assert "exceeds collector request limit" in caplog.text
    assert "not JSON serializable" in caplog.text
    assert "x" * 100 not in caplog.text


@pytest.mark.asyncio
async def test_lifecycle_and_all_routes(monkeypatch, caplog):
    session = install_session(monkeypatch)
    client = MetricsClient("https://example.test/", "token", batch_size=4)
    client.track_event(1, 2, "h", "message", {"text": "hi"})
    client.track_traffic(1)
    client.track_error(1, "Error", "bad")
    client.track_purchase(1, 12.5)
    await client.start()
    await client.start()
    await client.close()
    await client.close()
    assert session.closed
    assert {url.rsplit("/", 1)[-1] for url, _, _ in session.requests} == {
        "events",
        "traffic",
        "errors",
        "purchases",
    }
    for url, body, headers in session.requests:
        assert url.startswith("https://example.test/collector/track/")
        assert headers["X-Project-Token"] == "token"
        assert headers["Content-Type"] == "application/json"
        assert json.loads(body)[0]["project_token"] == "token"
    with caplog.at_level(logging.WARNING, logger="metricway"):
        client.track_traffic(2)
    assert "client is closed" in caplog.text
    with pytest.raises(RuntimeError):
        await client.start()


@pytest.mark.asyncio
async def test_batch_size_and_body_limit(monkeypatch):
    session = install_session(monkeypatch)
    client = MetricsClient("https://example.test", "token", batch_size=3)
    for _ in range(5):
        client.track_traffic(1)
    await client.start()
    await client.close()
    assert [len(json.loads(body)) for _, body, _ in session.requests] == [3, 2]

    session2 = install_session(monkeypatch)
    client2 = MetricsClient("https://example.test", "token", batch_size=2)
    for _ in range(2):
        client2.track_event(1, 2, "h", "message", {"text": "x" * 600_000})
    await client2.start()
    await client2.close()
    assert len(session2.requests) == 2
    assert all(len(body) <= MAX_REQUEST_BYTES for _, body, _ in session2.requests)


@pytest.mark.asyncio
async def test_retry_then_success(monkeypatch):
    session = install_session(monkeypatch, [503, 202])
    client = MetricsClient("https://example.test", "token", batch_size=1, max_retries=1)
    client.track_traffic(1)
    await client.start()
    await client.close()
    assert len(session.requests) == 2
    assert session.requests[0][1] == session.requests[1][1]


@pytest.mark.asyncio
async def test_retry_after_and_network_error(monkeypatch):
    import asyncio

    delays = []
    original_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        delays.append(seconds)
        await original_sleep(0)

    monkeypatch.setattr("metricway.client.asyncio.sleep", fake_sleep)
    session = install_session(monkeypatch, [(429, {"Retry-After": "3"}), OSError("down"), 202])
    client = MetricsClient("https://example.test", "token", batch_size=1, max_retries=2)
    client.track_traffic(1)
    await client.start()
    await client.close()
    assert len(session.requests) == 3
    assert delays == [3, 2]


@pytest.mark.asyncio
async def test_terminal_response_is_not_retried(monkeypatch, caplog):
    session = install_session(monkeypatch, [401])
    client = MetricsClient("https://example.test", "token", batch_size=1)
    client.track_traffic(1)
    with caplog.at_level(logging.ERROR, logger="metricway"):
        await client.start()
        await client.close()
    assert len(session.requests) == 1
    assert "HTTP 401" in caplog.text


@pytest.mark.asyncio
async def test_shutdown_timeout_reports_unsent(monkeypatch, caplog):
    session = install_session(monkeypatch, [503])
    client = MetricsClient("https://example.test", "token", batch_size=1, shutdown_timeout=0.01)
    client.track_traffic(1)
    with caplog.at_level(logging.WARNING, logger="metricway"):
        await client.start()
        await client.close()
    assert session.closed
    assert "1 metrics were not sent" in caplog.text


@pytest.mark.asyncio
async def test_close_without_start(caplog):
    client = MetricsClient("https://example.test", "token")
    client.track_traffic(1)
    with caplog.at_level(logging.WARNING, logger="metricway"):
        await client.close()
    assert "never started" in caplog.text


def test_retry_after_parsing():
    assert MetricsClient._retry_after("7") == 7
    assert MetricsClient._retry_after("100") == 60
    assert MetricsClient._retry_after("bad") == 0
    assert MetricsClient._retry_after("nan") == 0
    date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=5))
    assert 0 < MetricsClient._retry_after(date) <= 5


def test_base_import_without_aiogram():
    code = """
import builtins
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name == 'aiogram' or name.startswith('aiogram.'):
        raise ModuleNotFoundError("No module named 'aiogram'", name='aiogram')
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from metricway import MetricsClient
assert MetricsClient.__name__ == 'MetricsClient'
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_real_http_request():
    received = []

    async def collect(request):
        received.append((request.path, request.headers["X-Project-Token"], await request.json()))
        return web.json_response({"status": "queued"}, status=202)

    app = web.Application()
    app.router.add_post("/collector/track/events", collect)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        client = MetricsClient(f"http://127.0.0.1:{port}", "token", batch_size=1)
        client.track_event(1, 2, "h", "message", {"text": "привет"})
        await client.start()
        await client.close()
    finally:
        await runner.cleanup()

    assert received[0][0] == "/collector/track/events"
    assert received[0][1] == "token"
    assert json.loads(received[0][2][0]["payload"]) == {"text": "привет"}
