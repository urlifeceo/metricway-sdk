"""Public traffic classification and stable Telegram message identities."""

import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

SDK_VERSION = "0.2.0"
TRAFFIC_NAMESPACE = uuid.UUID("0bbf3ce5-c091-4fd5-b0ea-6813ec7f21d4")
PAYLOAD_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}", re.ASCII)
logger = logging.getLogger("metricway")


@dataclass(frozen=True)
class TrafficSource:
    source: str = ""
    campaign: str = ""
    purpose: str = "acquisition"

    def __post_init__(self):
        if self.purpose not in ("acquisition", "service"):
            raise ValueError("Invalid traffic purpose")
        if self.purpose == "acquisition" and (
            not self.source or len(self.source) > 256 or self.source != self.source.strip()
        ):
            raise ValueError("source must contain 1..256 characters without outer whitespace")
        if len(self.campaign) > 256:
            raise ValueError("campaign exceeds 256 characters")


TrafficResolver = Callable[[str], TrafficSource | None]


def start_id(bot_id: int, chat_id: int, message_id: int) -> str:
    """Stable across processes/retries. The string format is part of wire v2."""
    return str(uuid.uuid5(TRAFFIC_NAMESPACE, f"{bot_id}:{chat_id}:{message_id}:traffic_start"))


def traffic_record(
    user_id: int,
    start_payload: str,
    utm_source: str,
    utm_campaign: str,
    *,
    bot_id: int,
    chat_id: int,
    message_id: int,
    occurred_at: datetime,
    event_id: str | None = None,
    resolver: TrafficResolver | None = None,
) -> dict:
    if bot_id <= 0 or user_id <= 0 or chat_id != user_id or message_id <= 0:
        raise ValueError("Traffic v2 requires a human private-chat start message")
    if occurred_at.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")
    payload = start_payload.strip()
    if len(payload) > 4096:
        raise ValueError("start_payload exceeds 4096 characters")
    valid = not payload or PAYLOAD_PATTERN.fullmatch(payload) is not None
    kind, source, campaign, purpose, reason = "direct", "direct", "", "acquisition", "bare_start"
    if not payload and (utm_source or utm_campaign):
        raise ValueError("A bare /start cannot have a marketing source")
    if payload:
        kind, source, reason = "link", payload, "payload"
        if not valid:
            kind, source, reason = "unknown", "unknown", "invalid_payload"
        else:
            resolved = None
            if resolver:
                try:
                    resolved = resolver(payload)
                    if resolved is not None and not isinstance(resolved, TrafficSource):
                        raise TypeError("resolver must return TrafficSource or None")
                except Exception as exc:
                    logger.warning("Traffic resolver failed (%s)", type(exc).__name__)
                    kind, source, reason = "unknown", "unknown", "resolver_error"
            if resolved:
                purpose, campaign = resolved.purpose, resolved.campaign
                kind, source, reason = (
                    ("unknown", "unknown", "service_start")
                    if purpose == "service"
                    else ("link", resolved.source, "resolver")
                )
            if utm_source and purpose != "service":
                explicit = TrafficSource(utm_source, utm_campaign)
                kind, source, campaign, reason = (
                    "link",
                    explicit.source,
                    explicit.campaign,
                    "explicit",
                )
            elif utm_campaign:
                if len(utm_campaign) > 256:
                    raise ValueError("campaign exceeds 256 characters")
                campaign = utm_campaign
    identity = event_id or start_id(bot_id, chat_id, message_id)
    uuid.UUID(identity)
    return dict(
        schema_version=2,
        event_id=identity,
        ts=occurred_at.astimezone(timezone.utc).isoformat(),
        bot_id=bot_id,
        chat_id=chat_id,
        message_id=message_id,
        user_id=user_id,
        entry_kind="link" if payload else "direct",
        start_payload=payload,
        payload_valid=valid,
        source_kind=kind,
        source=source,
        campaign=campaign,
        purpose=purpose,
        resolution_reason=reason,
        sdk_version=SDK_VERSION,
    )
