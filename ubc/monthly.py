"""One monthly run: collect, choose, alert, pin houses, calculate, publish files.

``run_month`` is the whole pipeline of #18. Everything that talks to the outside
is passed in: the fetcher, the proxy transport, the GitHub client, and the house
step. Adapters come from the city files, so a new one needs no change here.

Nothing under ``data_root`` changes until every requested city has been
calculated. ``PublicationError`` means the run published nothing; alerts about
failed sources are sent before that and stay sent.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from ubc.adapter import AdapterOutput, CollectContext, get_adapter
from ubc.alerts import AlertDeliveryError, GitHubIssues, sync_alerts
from ubc.articles import ArticleOutcome, Reading, decide_article, keep_article
from ubc.calculate import CalculationError, calculate_cell
from ubc.display_rates import FILE_NAME as DISPLAY_RATES_NAME
from ubc.display_rates import DisplayRate, load_display_rates
from ubc.fetch import ContentFetcher, FetchError
from ubc.houses import CellKey, PinResult, enrich_candidates, pin_houses
from ubc.load import ConfigError
from ubc.policy import AlertAction, AlertReason, AttemptStatus, DataAlert
from ubc.proxy import ProxyTransport, fetcher_for_source
from ubc.recording import RecordingFetcher, Redactor
from ubc.schemas import (
    SCHEMA_VERSION,
    BuildingCandidate,
    Cell,
    CellStatus,
    CityConfig,
    CityId,
    CityMonth,
    Currency,
    ExchangeRate,
    HeatingType,
    HouseClass,
    Item,
    ListingSite,
    Mark,
    NormalizedRecord,
    PinnedHouse,
    ReferenceData,
    SourceConfig,
    YearMonth,
)
from ubc.secrets import SecretConfig, eligible_sources
from ubc.site_index import build_site_index
from ubc.state import (
    ArticleState,
    CityHouses,
    CityState,
    EmptyCell,
    houses_path,
    load_houses,
    load_state,
    state_path,
)

NOT_CONNECTED = "источники тарифов для города ещё не подключены"
_DEGREE_DAYS = frozenset({Item.hdd, Item.hdd_annual})
OPEN_DATA_ID = "house_open_data"

_EURO_URL = "https://www.ecb.europa.eu/"
_EURO_NAME = "евро, пересчёт не нужен"
_ALL_CELLS: tuple[CellKey, ...] = tuple(
    (house_class, heating) for house_class in HouseClass for heating in HeatingType
)
# Without this article the heating type cannot be priced at all.
_FUEL: dict[HeatingType, tuple[Item, str]] = {
    HeatingType.district: (Item.heating, "тариф центрального отопления"),
    HeatingType.gas_boiler: (Item.gas, "тариф на газ"),
    HeatingType.electric: (Item.electricity, "тариф на электричество"),
}


class PublicationError(Exception):
    """The month cannot be published. ``problems`` lists every reason found."""

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


class HouseStep(Protocol):
    """Candidates and last month's pins in, a house or an empty reason per cell out."""

    def __call__(
        self,
        *,
        city: CityConfig,
        candidates: Sequence[BuildingCandidate],
        previous: Sequence[PinnedHouse],
        period: YearMonth,
        fetcher: ContentFetcher,
    ) -> PinResult: ...


def enrich_and_pin(
    *,
    city: CityConfig,
    candidates: Sequence[BuildingCandidate],
    previous: Sequence[PinnedHouse],
    period: YearMonth,
    fetcher: ContentFetcher,
) -> PinResult:
    """The classifier of #36."""

    return pin_houses(
        city=city,
        candidates=enrich_candidates(city, candidates, fetcher),
        previous=previous,
        period=period,
    )


class _OpenDataFetcher(RecordingFetcher):
    """Open data only adds facts, so it may neither break nor stall the run.

    Whatever goes wrong is the ``FetchError`` the enrichment already expects, and
    once the time is spent every further request fails at once.
    """

    def __init__(self, inner: ContentFetcher, redactor: Redactor, budget_s: float) -> None:
        super().__init__(inner, redactor)
        self._deadline = time.monotonic() + budget_s
        self.problems: list[str] = []

    def _keep[T: (str, bytes)](
        self, method: str, url: str, call: Callable[[], T], request: str = ""
    ) -> T:
        try:
            if time.monotonic() >= self._deadline:
                raise FetchError(url, "время на открытые данные вышло")
            return super()._keep(method, url, call, request)
        except FetchError as exc:
            self._note(str(exc))
            raise
        except Exception as exc:
            self._note(f"{url}: {exc}")
            raise FetchError(url, str(exc)) from exc

    def _note(self, problem: str) -> None:
        if problem not in self.problems:
            self.problems.append(problem)


@dataclass(frozen=True)
class MonthOptions:
    """What to run. ``item`` collects one article and takes the rest from ``data/state``.

    ``issues`` is ``None`` when alerts are not to be sent. ``dry_run`` calculates
    everything, sends nothing, and writes nothing. ``open_data_budget_s`` caps the
    time one city spends on open data about houses. ``hide`` adds secret values
    to blank in stored answers on top of the ones in ``secrets``. ``display_rate``
    gives the dram and rouble rates the site converts euros with; ``None`` skips them.
    """

    period: YearMonth
    cities: Sequence[CityConfig]
    reference: ReferenceData
    fetcher: ContentFetcher
    data_root: Path
    item: Item | None = None
    issues: GitHubIssues | None = None
    secrets: SecretConfig = field(default_factory=SecretConfig)
    transport: ProxyTransport | None = None
    house_step: HouseStep = enrich_and_pin
    open_data_budget_s: float = 300.0
    display_rate: Callable[[YearMonth, ContentFetcher], DisplayRate] | None = None
    dry_run: bool = False
    now: datetime | None = None
    hide: Sequence[str] = ()
    log: Callable[[str], None] = lambda line: None


@dataclass(frozen=True)
class MonthReport:
    """``written`` is relative to the data root. ``warnings`` did not stop the run."""

    months: tuple[CityMonth, ...]
    alerts: tuple[DataAlert, ...]
    written: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ListingReading:
    site: ListingSite
    status: AttemptStatus
    candidates: tuple[BuildingCandidate, ...] = ()
    detail: str = ""


@dataclass
class _CityRun:
    """Everything one city produced before anything is written."""

    city: CityConfig
    connected: bool
    state: CityState
    saved_houses: CityHouses | None
    outcomes: list[ArticleOutcome] = field(default_factory=list)
    readings: list[tuple[Item, Reading]] = field(default_factory=list)
    context: list[NormalizedRecord] = field(default_factory=list)
    rate: ExchangeRate | None = None
    listings: list[_ListingReading] = field(default_factory=list)
    alerts: list[DataAlert] = field(default_factory=list)
    raw: dict[str, bytes] = field(default_factory=dict)
    saved_normalized: dict[str, Any] | None = None
    problems: list[str] = field(default_factory=list)
    houses: CityHouses | None = None
    month: CityMonth | None = None


def run_month(options: MonthOptions) -> MonthReport:
    """Run the month. Raises ``PublicationError`` or ``ConfigError`` and writes nothing."""

    return _Run(options).execute()


class _Run:
    def __init__(self, options: MonthOptions) -> None:
        self.options = options
        self.period = options.period
        self.now = options.now or datetime.now(UTC)
        secrets = options.secrets
        self.redactor = Redactor(
            (
                secrets.proxy_host,
                secrets.proxy_login,
                secrets.proxy_password,
                secrets.llm_api_key,
                *options.hide,
            )
        )
        self.warnings: list[str] = []

    def execute(self) -> MonthReport:
        for city in self.options.cities:
            self._check_adapters(city)
        runs = [self._collect(city) for city in self.options.cities]
        alerts = [alert for run in runs for alert in run.alerts]
        self._send(alerts)
        problems: list[str] = []
        for run in runs:
            self._calculate(run)
            problems.extend(f"{run.city.id.value}: {problem}" for problem in run.problems)
        if problems:
            raise PublicationError(problems)
        files = self._files(runs)
        files.update(self._display_rates())
        if not self.options.dry_run:
            _write_all(self.options.data_root, files)
        return MonthReport(
            months=tuple(run.month for run in runs if run.month is not None),
            alerts=tuple(alerts),
            written=() if self.options.dry_run else tuple(sorted(files)),
            warnings=tuple(self.warnings),
        )

    # Collection

    def _check_adapters(self, city: CityConfig) -> None:
        sources: list[SourceConfig | ListingSite] = [
            *city.fx_sources,
            *city.hdd_sources,
            *city.listing_sites,
        ]
        for article in city.articles:
            sources.extend(article.sources)
        for source in sources:
            if not source.enabled:
                continue
            try:
                get_adapter(source.adapter)
            except KeyError as exc:
                raise ConfigError(f"{city.id.value}: {source.id}: {exc.args[0]}") from exc

    def _collect(self, city: CityConfig) -> _CityRun:
        run = _CityRun(
            city=city,
            connected=any(
                eligible_sources(article.sources, self.options.secrets) for article in city.articles
            ),
            state=load_state(self.options.data_root, city.id),
            saved_houses=load_houses(self.options.data_root, city.id),
            saved_normalized=_read_normalized(
                self.options.data_root / self._normalized_name(city.id)
            ),
        )
        for article in city.articles:
            previous = run.state.article(article.item)
            if self.options.item not in (None, article.item):
                self._keep_saved(run, article.item, previous)
                continue
            readings = [
                self._read(run, article.item, source)
                for source in eligible_sources(article.sources, self.options.secrets)
            ]
            if not readings:
                self._keep_saved(run, article.item, previous)
                continue
            outcome = decide_article(
                city=city.id,
                item=article.item,
                period=self.period,
                sources=article.sources,
                readings=readings,
                previous=previous,
            )
            run.outcomes.append(outcome)
            run.alerts.extend(outcome.alerts)
        self._read_rate(run)
        if run.connected:
            # Without tariffs no cell is priced, so the archive is not asked in vain.
            self._read_degree_days(run)
        if run.connected and not self._houses_are_settled(run):
            run.listings = [
                self._read_listing(run, site) for site in city.listing_sites if site.enabled
            ]
        return run

    def _keep_saved(self, run: _CityRun, item: Item, previous: ArticleState | None) -> None:
        if previous is not None and _has_sources(run.city, item):
            run.outcomes.append(keep_article(previous, self.period))

    def _read(self, run: _CityRun, item: Item, source: SourceConfig) -> Reading:
        status, output, detail = self._call(run, source)
        records = tuple(output.records)
        if status is AttemptStatus.ok and not records:
            status, detail = AttemptStatus.empty, "источник не вернул записей"
        reading = Reading(source, status, records, detail)
        run.readings.append((item, reading))
        self._log_reading(run, item.value, source.id, status, detail)
        return reading

    def _read_listing(self, run: _CityRun, site: ListingSite) -> _ListingReading:
        status, output, detail = self._call(run, site)
        self._log_reading(run, "listings", site.id, status, detail)
        if status is not AttemptStatus.ok:
            self._warn(f"{run.city.id.value}: сайт объявлений {site.id}: {detail}")
        return _ListingReading(site, status, tuple(output.candidates), detail)

    def _call(
        self, run: _CityRun, source: SourceConfig | ListingSite
    ) -> tuple[AttemptStatus, AdapterOutput, str]:
        """One adapter call. Whatever it raises is a status, not a crash."""

        recorder: RecordingFetcher | None = None
        try:
            direct = self.options.fetcher
            if isinstance(source, SourceConfig):
                direct = fetcher_for_source(
                    source, direct, self.options.secrets, self.options.transport
                )
            recorder = RecordingFetcher(direct, self.redactor)
            output = get_adapter(source.adapter).collect(
                CollectContext(city=run.city, period=self.period, fetcher=recorder, source=source)
            )
            return AttemptStatus.ok, _checked(output), ""
        except ValidationError as exc:
            return AttemptStatus.invalid, AdapterOutput(), self._detail(exc)
        except Exception as exc:
            return AttemptStatus.error, AdapterOutput(), self._detail(exc)
        finally:
            if recorder is not None:
                self._keep_raw(run, source.id, recorder)

    def _keep_raw(self, run: _CityRun, source_id: str, recorder: RecordingFetcher) -> None:
        packed = recorder.archive()
        if packed is not None:
            run.raw[source_id] = packed

    def _read_rate(self, run: _CityRun) -> None:
        city = run.city
        reading = self._first_usable(run, Item.fx, city.fx_sources)
        if reading is not None:
            record = reading.records[0]
            run.context.append(record)
            run.rate = ExchangeRate(
                currency=city.currency,
                per_eur=record.value,
                month=str(self.period),
                source_url=record.source.url,
                source_name=record.source.name or reading.source.id,
            )
        elif city.currency is Currency.EUR:
            run.rate = ExchangeRate(
                currency=Currency.EUR,
                per_eur=1,
                month=str(self.period),
                source_url=_EURO_URL,
                source_name=_EURO_NAME,
            )

    def _read_degree_days(self, run: _CityRun) -> None:
        reading = self._first_usable(run, Item.hdd, run.city.hdd_sources)
        if reading is not None:
            run.context.extend(reading.records)

    def _first_usable(
        self, run: _CityRun, item: Item, sources: Sequence[SourceConfig]
    ) -> Reading | None:
        """First source, in order, that gave ``item`` for this very month."""

        for source in eligible_sources(list(sources), self.options.secrets):
            reading = self._read(run, item, source)
            records = _of_the_month(item, reading, run.city, self.period)
            run.alerts.append(_context_alert(run.city, item, self.period, reading, records))
            if records:
                return replace(reading, records=records)
        return None

    # Alerts

    def _send(self, alerts: Sequence[DataAlert]) -> None:
        if self.options.issues is None or self.options.dry_run:
            return
        try:
            sync_alerts(self.options.issues, alerts)
        except AlertDeliveryError as exc:
            self._warn(f"уведомления не отправлены: {self.redactor.text(str(exc))}")

    # Calculation

    def _houses_are_settled(self, run: _CityRun) -> bool:
        """Saved pins are used as they are: one-article runs and recounts of older months."""

        saved = run.saved_houses
        if saved is None:
            return False
        if self.options.item is not None:
            return True
        month = saved.month()
        return (month.year, month.month) > (self.period.year, self.period.month)

    def _calculate(self, run: _CityRun) -> None:
        if run.connected:
            pins = self._pin(run)
            if pins is None:
                return
        else:
            pins = PinResult(houses={}, empty_reasons=dict.fromkeys(_ALL_CELLS, NOT_CONNECTED))
        cells = [self._cell(run, pins, key) for key in _ALL_CELLS]
        if any(cell.status is CellStatus.ok for cell in cells if cell is not None):
            self._require_inputs(run)
        if run.problems:
            return
        if run.rate is None:
            self._warn(f"{run.city.id.value}: нет курса за {self.period}, месяц не опубликован")
            return
        run.month = CityMonth(
            city=run.city.id,
            month=str(self.period),
            updated_at=self.now,
            apartment=self.options.reference.apartment,
            exchange_rate=run.rate,
            paid_data_note=run.city.paid_data_note,
            cells=[cell for cell in cells if cell is not None],
        )
        filled = sum(cell.status is CellStatus.ok for cell in run.month.cells)
        self.options.log(f"{run.city.id.value}: ячеек заполнено {filled} из {len(_ALL_CELLS)}")

    def _pin(self, run: _CityRun) -> PinResult | None:
        saved = run.saved_houses
        if saved is not None and self._houses_are_settled(run):
            settled = PinResult(
                houses={(house.house_class, house.heating): house for house in saved.houses},
                empty_reasons={
                    (cell.house_class, cell.heating): cell.reason for cell in saved.empty
                },
            )
            if not _covers_the_grid(settled):
                run.problems.append("сохранённые дома не покрывают девять ячеек ровно по разу")
                return None
            return settled
        recorder = _OpenDataFetcher(
            self.options.fetcher, self.redactor, self.options.open_data_budget_s
        )
        try:
            pins = self.options.house_step(
                city=run.city,
                candidates=[
                    candidate for reading in run.listings for candidate in reading.candidates
                ],
                previous=saved.houses if saved is not None else [],
                period=self.period,
                fetcher=recorder,
            )
        except Exception as exc:
            run.problems.append(f"закрепление домов: {self._detail(exc)}")
            return None
        finally:
            self._keep_raw(run, OPEN_DATA_ID, recorder)
            for problem in recorder.problems:
                self._warn(
                    f"{run.city.id.value}: открытые данные о домах: {self.redactor.text(problem)}"
                )
        if not _covers_the_grid(pins):
            run.problems.append("закрепление домов не покрыло девять ячеек ровно по одному разу")
            return None
        run.houses = CityHouses(
            city=run.city.id,
            period=str(self.period),
            houses=[pins.houses[key] for key in _ALL_CELLS if key in pins.houses],
            empty=[
                EmptyCell(house_class=key[0], heating=key[1], reason=pins.empty_reasons[key])
                for key in _ALL_CELLS
                if key in pins.empty_reasons
            ],
        )
        return pins

    def _cell(self, run: _CityRun, pins: PinResult, key: CellKey) -> Cell | None:
        house_class, heating = key
        house = pins.houses.get(key)
        reason = pins.empty_reasons.get(key)
        item, label = _FUEL[heating]
        if house is not None and not _has_sources(run.city, item):
            house, reason = None, f"{label} для города ещё не подключён"
        records = [record for outcome in run.outcomes for record in outcome.records]
        marks: dict[Item, Sequence[Mark]] = {
            outcome.item: outcome.marks for outcome in run.outcomes if outcome.marks
        }
        try:
            cell = calculate_cell(
                period=self.period,
                city=run.city.id,
                house_class=house_class,
                heating=heating,
                house=house,
                records=[*records, *run.context],
                norms=self.options.reference.norms,
                apartment=self.options.reference.apartment,
                extra_marks=marks,
                empty_reason=reason,
                class_fees=run.city.class_fees,
            )
        except (CalculationError, ValidationError) as exc:
            run.problems.append(f"{house_class.value} × {heating.value}: {exc}")
            self._require_inputs(run)
            return None
        return _with_source_dates(cell, run.outcomes, self.period)

    def _require_inputs(self, run: _CityRun) -> None:
        """A filled cell must not silently lack an article, the rate, or degree-days."""

        found: list[str] = []
        outcomes = {outcome.item: outcome for outcome in run.outcomes}
        for article in run.city.articles:
            outcome = outcomes.get(article.item)
            if not _has_sources(run.city, article.item) or (outcome and outcome.records):
                continue
            if outcome is not None and outcome.known_from is not None:
                found.append(
                    f"статья {article.item.value}: самый ранний известный тариф "
                    f"действует с {outcome.known_from}, позже месяца {self.period}"
                )
            else:
                found.append(
                    f"статья {article.item.value}: ни один источник не дал тариф на месяц, "
                    "прошлого значения нет"
                )
        if run.rate is None:
            found.append(f"нет курса за {self.period}")
        if not _DEGREE_DAYS <= {record.item for record in run.context}:
            found.append(f"нет градусо-суток за {self.period}")
        run.problems.extend(problem for problem in found if problem not in run.problems)

    # Files

    def _files(self, runs: Sequence[_CityRun]) -> dict[str, bytes]:
        """Every file to write, by path relative to the data root."""

        root = self.options.data_root
        files: dict[str, bytes] = {}
        snapshots: dict[str, bytes] = {}
        for run in runs:
            city = run.city.id
            normalized, outlived = _merge_normalized(run.saved_normalized, self._normalized(run))
            files[self._normalized_name(city)] = self.redactor.data(_json(normalized))
            for source_id, packed in run.raw.items():
                name = f"raw/{self.period}/{city.value}/{source_id}.tar.xz"
                if source_id not in outlived or not (root / name).is_file():
                    files[name] = packed
            state = _next_state(run)
            if state.articles:
                files[state_path(root, city).relative_to(root).as_posix()] = self._stored(state)
            if run.houses is not None:
                files[houses_path(root, city).relative_to(root).as_posix()] = self._stored(
                    run.houses
                )
            if run.month is not None:
                snapshots[f"{city.value}-{self.period}.json"] = self._stored(run.month)
        files.update(snapshots)
        files["site-index.json"] = _json(_index_with(root, snapshots))
        return files

    def _display_rates(self) -> dict[str, bytes]:
        """The rates file with this month. Euros stay on the site whatever happens here."""

        if self.options.display_rate is None:
            return {}
        saved = load_display_rates(self.options.data_root)
        try:
            rate = self.options.display_rate(self.period, self.options.fetcher)
        except Exception as exc:
            self._warn(f"курсы для показа в других валютах не обновлены: {self._detail(exc)}")
            return {}
        return {DISPLAY_RATES_NAME: _json(saved.with_rate(rate))}

    def _normalized_name(self, city: CityId) -> str:
        return f"normalized/{self.period}/{city.value}.json"

    def _stored(self, document: BaseModel) -> bytes:
        """The file without secrets. A secret that is also ordinary data damages it."""

        body = _json(document)
        hidden = self.redactor.data(body)
        if hidden != body:
            try:
                type(document).model_validate_json(hidden)
            except ValidationError as exc:
                raise PublicationError(
                    ["секрет совпадает с обычными данными: файл без него не читается"]
                ) from exc
        return hidden

    def _normalized(self, run: _CityRun) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "city": run.city.id.value,
            "period": str(self.period),
            "collected_at": self.now.isoformat(),
            "sources": [
                {
                    "source_id": reading.source.id,
                    "item": item.value,
                    "status": reading.status.value,
                    "detail": reading.detail,
                    "records": [record.model_dump(mode="json") for record in reading.records],
                }
                for item, reading in run.readings
            ],
            "acting": {
                outcome.item.value: {
                    "source_id": outcome.source_id,
                    "marks": [mark.value for mark in outcome.marks],
                    "records": [record.model_dump(mode="json") for record in outcome.records],
                }
                for outcome in run.outcomes
            },
            "listings": [
                {
                    "site_id": reading.site.id,
                    "status": reading.status.value,
                    "detail": reading.detail,
                    "candidates": [
                        candidate.model_dump(mode="json") for candidate in reading.candidates
                    ],
                }
                for reading in run.listings
            ],
        }

    # Messages

    def _detail(self, exc: Exception) -> str:
        text = " ".join(self.redactor.text(str(exc) or type(exc).__name__).split())
        return text if len(text) <= 300 else f"{text[:297]}..."

    def _warn(self, message: str) -> None:
        self.warnings.append(message)
        self.options.log(f"ВНИМАНИЕ {message}")

    def _log_reading(
        self, run: _CityRun, what: str, source_id: str, status: AttemptStatus, detail: str
    ) -> None:
        line = f"{run.city.id.value}: {what}: {source_id}: {status.value}"
        self.options.log(f"{line} ({detail})" if detail else line)


def _has_sources(city: CityConfig, item: Item) -> bool:
    """The article is connected: at least one of its sources is switched on."""

    return any(
        source.enabled
        for article in city.articles
        if article.item is item
        for source in article.sources
    )


def _of_the_month(
    item: Item, reading: Reading, city: CityConfig, period: YearMonth
) -> tuple[NormalizedRecord, ...]:
    """The rate, or both degree-day totals, of this month. Empty when any is missing.

    Today's rate is not a monthly average: a record dated otherwise does not count.
    """

    wanted = _DEGREE_DAYS if item is Item.hdd else frozenset({item})
    owner = "all" if item is Item.fx else city.id
    currency = city.currency if item is Item.fx else None
    found = {
        record.item: record
        for record in reading.records
        if record.item in wanted
        and record.city == owner
        and record.currency is currency
        and record.valid_from == period.first_day
        and (item is not Item.fx or record.value > 0)
    }
    return tuple(found.values()) if set(found) == wanted else ()


def _context_alert(
    city: CityConfig,
    item: Item,
    period: YearMonth,
    reading: Reading,
    records: Sequence[NormalizedRecord],
) -> DataAlert:
    """Rates and degree-days do not go through the policy, but their outages are reported."""

    url = reading.source.settings.get("url")
    sample = next((record for record in records if record.item is item), None)
    return DataAlert(
        city=city.id,
        item=item,
        source_id=reading.source.id,
        reason=AlertReason.unavailable if sample is None else AlertReason.recovered,
        action=AlertAction.open if sample is None else AlertAction.resolve,
        period=period,
        old_value=None,
        new_value=None if sample is None else sample.value,
        unit=None if sample is None else sample.unit,
        url=url if isinstance(url, str) else None,
        detail="" if sample is not None else reading.detail or "данные не за месяц сбора",
    )


def _with_source_dates(cell: Cell, outcomes: Sequence[ArticleOutcome], period: YearMonth) -> Cell:
    """Show the source's own date on a line priced from a record stretched to the 1st."""

    dated = {url: day for outcome in outcomes for url, day in outcome.dated.items()}
    lines = [
        line.model_copy(update={"valid_from": dated[line.source_url]})
        if line.source_url in dated and line.valid_from == period.first_day
        else line
        for line in cell.lines
    ]
    return cell.model_copy(update={"lines": lines})


def _next_state(run: _CityRun) -> CityState:
    decided = {outcome.item: outcome.state for outcome in run.outcomes if outcome.state is not None}
    articles = [
        state
        for article in run.city.articles
        if (state := decided.get(article.item) or run.state.article(article.item)) is not None
    ]
    return CityState(city=run.city.id, articles=articles)


_NORMALIZED_GROUPS = (("sources", "source_id"), ("listings", "site_id"))


def _checked(output: object) -> AdapterOutput:
    """The adapter's answer, read again as the contract: a copy may skip validation."""

    if not isinstance(output, AdapterOutput):
        raise TypeError(f"адаптер вернул {type(output).__name__}, а не AdapterOutput")
    return AdapterOutput(
        records=[
            NormalizedRecord.model_validate(record.model_dump(warnings=False))
            for record in output.records
        ],
        candidates=[
            BuildingCandidate.model_validate(candidate.model_dump(warnings=False))
            for candidate in output.candidates
        ],
    )


def _covers_the_grid(pins: PinResult) -> bool:
    return set(pins.houses) | set(pins.empty_reasons) == set(_ALL_CELLS) and not (
        set(pins.houses) & set(pins.empty_reasons)
    )


def _read_normalized(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        for group, key in _NORMALIZED_GROUPS:
            if not all(isinstance(entry[key], str) for entry in saved.get(group, [])):
                raise ValueError(f"{group}: {key} is not a string")
    except (OSError, ValueError, AttributeError, KeyError, TypeError) as exc:
        raise ConfigError(f"{path}: {exc!r}") from exc
    return saved


def _merge_normalized(
    saved: dict[str, Any] | None, fresh: dict[str, Any]
) -> tuple[dict[str, Any], set[str]]:
    """Keep what an earlier run of the same month collected and this one did not.

    The ids of sources that answered then and failed now come second: the saved
    answer outlives the failure, which is noted in ``detail``.
    """

    outlived: set[str] = set()
    if saved is None:
        return fresh, outlived
    for group, key in _NORMALIZED_GROUPS:
        before = {entry[key]: entry for entry in saved.get(group, [])}
        merged = []
        for entry in fresh[group]:
            old = before.pop(entry[key], None)
            if old is None or old.get("status") != "ok" or entry["status"] == "ok":
                merged.append(entry)
                continue
            outlived.add(entry[key])
            failure = f"повторный сбор не удался: {entry['detail'] or entry['status']}"
            merged.append({**old, "detail": failure})
        fresh[group] = [*merged, *before.values()]
    return fresh, outlived


def _index_with(root: Path, snapshots: Mapping[str, bytes]) -> dict[str, object]:
    """The site index as it will be once ``snapshots`` are in place."""

    with tempfile.TemporaryDirectory() as scratch:
        staged = Path(scratch)
        for path in root.glob("*.json"):
            shutil.copy(path, staged / path.name)
        if (root / "examples").is_dir():
            shutil.copytree(root / "examples", staged / "examples")
        for name, body in snapshots.items():
            (staged / name).write_bytes(body)
        try:
            return build_site_index(staged)
        except UnicodeDecodeError as exc:
            broken = [path.name for path in staged.glob("*.json") if not _is_utf8(path)]
            raise ConfigError(f"{', '.join(broken)}: {exc}") from exc


def _is_utf8(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _json(document: BaseModel | Mapping[str, object]) -> bytes:
    payload = document.model_dump(mode="json") if isinstance(document, BaseModel) else document
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write_all(root: Path, files: Mapping[str, bytes]) -> None:
    """Write every file next to its target first, then move them all into place.

    A failure puts the files already moved back as they were.
    """

    staged: list[tuple[Path, Path]] = []
    moved: list[tuple[Path, bytes | None]] = []
    try:
        for name, body in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            part = target.with_name(f"{target.name}.part")
            staged.append((part, target))
            part.write_bytes(body)
        for part, target in staged:
            before = target.read_bytes() if target.is_file() else None
            part.replace(target)
            moved.append((target, before))
    except OSError as exc:
        for target, before in moved:
            if before is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(before)
        for part, _target in staged:
            part.unlink(missing_ok=True)
        raise PublicationError([f"файлы не записаны: {exc}"]) from exc
