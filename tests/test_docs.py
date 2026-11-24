"""The written contract agents are told to follow."""

from __future__ import annotations

from ubc.paths import REPO_ROOT


def test_agents_states_the_stack_tdd_seams_and_that_poc_scripts_stay_out() -> None:
    text = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "Python 3.12" in text
    assert "vanilla JS" in text
    assert "pytest" in text
    assert "ruff" in text
    assert "Красный → зелёный → рефакторинг." in text
    assert "публичный интерфейс" in text
    assert "(a)" in text and "(b)" in text and "(c)" in text
    assert "Сеть в тестах запрещена." in text
    assert "Скрипты-прототипы (PoC) источников не лежат в этом репозитории." in text
    assert "Защиту от роботов не обходить" in text


def test_readme_explains_tests_and_the_local_pipeline() -> None:
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    assert "pytest" in text
    assert "ruff check ." in text
    assert "python -m ubc.pipeline" in text
    assert "https://github.com/Anwert/utility-bills-compare/issues/1" in text
    assert "https://github.com/Anwert/utility-bills-compare/issues/18" in text
    assert "Скрипты-прототипы (PoC) источников не лежат в этом репозитории." in text


def test_glossary_names_the_domain_terms() -> None:
    text = (REPO_ROOT / "GLOSSARY.md").read_text(encoding="utf-8")
    for term in (
        "Эталонная квартира",
        "Класс дома",
        "Тип отопления",
        "Ячейка",
        "Жилищные услуги",
        "Коммунальные услуги",
        "Нормализованная запись",
        "Источник",
        "Приоритет",
        "Уровень надёжности",
        "fixed",
        "norm_based",
        "statistics_estimate",
        "third_party",
        "stale",
        "Кандидат дома",
        "Закреплённый дом",
        "hdd_annual",
        "Градусо-сутки",
        "Конфигурация города",
        "Контракт собранных данных",
    ):
        assert term in text
