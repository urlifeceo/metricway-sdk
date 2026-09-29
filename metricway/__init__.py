from metricway.client import MetricsClient

__all__ = ["MetricsClient", "AiogramMetricsMiddleware", "setup_aiogram_metrics"]


def __getattr__(name: str):
    if name in {"AiogramMetricsMiddleware", "setup_aiogram_metrics"}:
        try:
            from metricway.middleware import AiogramMetricsMiddleware, setup_aiogram_metrics
        except ModuleNotFoundError as exc:
            if exc.name == "aiogram":
                raise ModuleNotFoundError(
                    "aiogram integration requires: pip install 'metricway-sdk[aiogram]'"
                ) from exc
            raise
        return {
            "AiogramMetricsMiddleware": AiogramMetricsMiddleware,
            "setup_aiogram_metrics": setup_aiogram_metrics,
        }[name]
    raise AttributeError(f"module 'metricway' has no attribute {name!r}")
