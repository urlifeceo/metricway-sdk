# Выпуск версии

Публикацию выполняет владелец проекта после проверки CI. Workflow выпуска:
[.github/workflows/release.yml](.github/workflows/release.yml).

1. Убедитесь, что имя `metricway-sdk` доступно в PyPI, и создайте в своём
   аккаунте pending Trusted Publisher: GitHub owner `urlifeceo`, repository
   `tgmetricks-sdk`, workflow `release.yml`, environment `pypi`.
   Создайте одноимённый GitHub environment `pypi` с требуемыми правилами защиты.
2. Убедитесь, что версия в `pyproject.toml` равна `0.1.0`, CI зелёный, а
   `python -m build` и `python -m twine check dist/*` проходят.
3. Создайте и отправьте тег `v0.1.0` на проверенный коммит. Release workflow
   проверит версию, тесты и сборку, затем загрузит wheel и sdist в PyPI.
4. Проверьте страницу PyPI и установку в чистом окружении:
   `python -m pip install "metricway-sdk[aiogram]==0.1.0"`.
   Проверьте импорт `from metricway import MetricsClient, setup_aiogram_metrics`.
5. Только после успешной публикации обновите установку и пример подключения
   в соседнем репозитории `metric`: `docs/sdk-aiogram-quickstart.md`,
   `frontend/src/views/GuideView.vue` и `frontend/src/views/LandingView.vue`.
   Установка должна использовать `metricway-sdk[aiogram]` из PyPI, а
   aiogram-интеграция — импорт из `metricway` и вызов
   `setup_aiogram_metrics(dp, metrics)`.

Пакет хранит события только в памяти. Перед релизом проверьте разделы README
о составе собираемых данных и ограничениях доставки.
