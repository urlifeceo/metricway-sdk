import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class EventDTO:
    ts: str
    event_id: str
    project_token: str
    user_id: int
    chat_id: int
    handler: str
    update_type: str
    payload: str

    @classmethod
    def create(
        cls,
        project_token: str,
        user_id: int,
        chat_id: int,
        handler: str,
        update_type: str,
        payload: dict[str, Any],
    ) -> "EventDTO":
        return cls(
            ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            event_id=str(uuid.uuid4()),
            project_token=project_token,
            user_id=user_id,
            chat_id=chat_id,
            handler=handler,
            update_type=update_type,
            payload=json.dumps(payload, ensure_ascii=False, allow_nan=False),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrafficDTO:
    ts: str
    project_token: str
    user_id: int
    start_payload: str
    utm_source: str
    utm_campaign: str
    referrer: str

    @classmethod
    def create(
        cls,
        project_token: str,
        user_id: int,
        start_payload: str = "",
        utm_source: str = "",
        utm_campaign: str = "",
        referrer: str = "",
    ) -> "TrafficDTO":
        return cls(
            ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            project_token=project_token,
            user_id=user_id,
            start_payload=start_payload,
            utm_source=utm_source,
            utm_campaign=utm_campaign,
            referrer=referrer,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PurchaseDTO:
    ts: str
    project_token: str
    user_id: int
    amount: float
    currency: str
    product_id: str
    payment_provider: str

    @classmethod
    def create(
        cls,
        project_token: str,
        user_id: int,
        amount: float,
        currency: str = "RUB",
        product_id: str = "",
        payment_provider: str = "",
    ) -> "PurchaseDTO":
        return cls(
            ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            project_token=project_token,
            user_id=user_id,
            amount=amount,
            currency=currency,
            product_id=product_id,
            payment_provider=payment_provider,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "project_token": self.project_token,
            "user_id": self.user_id,
            "amount": self.amount,
            "currency": self.currency,
            "product_id": self.product_id,
            "payment_provider": self.payment_provider,
        }


@dataclass
class ErrorDTO:
    ts: str
    project_token: str
    user_id: int
    error_type: str
    error_message: str
    stack: str

    @classmethod
    def create(
        cls,
        project_token: str,
        user_id: int,
        error_type: str,
        error_message: str,
        stack: str = "",
    ) -> "ErrorDTO":
        return cls(
            ts=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            project_token=project_token,
            user_id=user_id,
            error_type=error_type,
            error_message=error_message,
            stack=stack,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
