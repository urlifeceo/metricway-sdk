# Разработка

Нужен Python 3.10 или новее. В корне репозитория:

```bash
python -m venv .venv
python -m pip install -e ".[aiogram,dev]"
python -m ruff check .
python -m pytest --cov=metricway --cov-report=term-missing
python -m build
python -m twine check dist/*
```

Тесты не обращаются к рабочему collector. При изменении сетевого протокола сверяйте
маршруты, заголовок токена, размер запроса и поля DTO с кодом collector в репозитории
metricway. Сохраните совместимость публичных методов `MetricsClient.track_*` и
документируйте изменения поведения доставки.

Процесс публикации описан в [RELEASING.md](RELEASING.md).
