"""Data contracts shared by adapters, the monthly calculation, and the static site.

Invalid documents raise ``pydantic.ValidationError``. The pipeline treats that as a
hard failure and does not publish.

JSON files use these English identifiers. Russian domain names live in GLOSSARY.md.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = 1

# Degree-days use this ASCII unit (the Eurostat K·day). Exchange rates use "<CCY>/EUR".
HDD_UNIT = "K*day"

# Default text for CityConfig.paid_data_note. Spec #18 shows this sentence on
# figures whose reliability is statistics. The MVP decision uses the same
# sentence for third-party figures and for values standing in for the disabled
# LLM parser. Each city file can replace the wording; when the site shows it
# is that later ticket's choice, and the dollar amount stays in the text.
DEFAULT_PAID_DATA_NOTE = (
    "оценка по статистике; точные данные поставщика возможны за доплату "
    "(~$1–2 в месяц на страну через резидентный прокси)"
)

_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CURRENCY_CODE = r"^[A-Z]{3}$"


def _require_http_url(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(("https://", "http://")):
        raise ValueError("url must be http(s)")
    return value


def _optional_http_url(value: str | None) -> str | None:
    if value is None:
        return None
    return _require_http_url(value)


@dataclass(frozen=True, slots=True)
class YearMonth:
    """The month an adapter collects. ``first_day`` is that month's 1st.

    FX adapters turn this into ``startPeriod=YYYY-MM``. Degree-day adapters
    turn ``first_day`` and ``last_day`` into the Open-Meteo date range.
    """

    year: int
    month: int

    def __post_init__(self) -> None:
        if isinstance(self.year, bool) or not isinstance(self.year, int):
            raise ValueError("year must be an integer")
        if isinstance(self.month, bool) or not isinstance(self.month, int):
            raise ValueError("month must be an integer")
        if not 1990 <= self.year <= 2100:
            raise ValueError("year out of range")
        if not 1 <= self.month <= 12:
            raise ValueError("month must be 1..12")

    @classmethod
    def parse(cls, text: str) -> YearMonth:
        if not _MONTH.fullmatch(text):
            raise ValueError("period must be YYYY-MM")
        year_text, month_text = text.split("-")
        return cls(int(year_text), int(month_text))

    @property
    def first_day(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def last_day(self) -> date:
        if self.month == 12:
            return date(self.year, 12, 31)
        return date(self.year, self.month + 1, 1) - timedelta(days=1)

    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


class CityId(StrEnum):
    yerevan = "yerevan"
    tbilisi = "tbilisi"
    belgrade = "belgrade"
    warsaw = "warsaw"
    lisbon = "lisbon"
    berlin = "berlin"


class HouseClass(StrEnum):
    """Rows of the 3×3 grid, top to bottom."""

    soviet_panel = "soviet_panel"
    typical_newbuild = "typical_newbuild"
    business = "business"


class HeatingType(StrEnum):
    """Columns of the 3×3 grid, left to right."""

    district = "district"
    gas_boiler = "gas_boiler"
    electric = "electric"


class Amenity(StrEnum):
    """What a residential complex offers its tenants beyond a yard and a lift."""

    swimming_pool = "swimming_pool"
    gym = "gym"
    tennis = "tennis"
    concierge = "concierge"


class Item(StrEnum):
    maintenance = "maintenance"
    heating = "heating"
    electricity = "electricity"
    gas = "gas"
    water = "water"
    hot_water = "hot_water"
    sewage = "sewage"
    waste = "waste"
    fx = "fx"
    hdd = "hdd"
    hdd_annual = "hdd_annual"


class Category(StrEnum):
    housing = "housing"
    utility = "utility"
    context = "context"


class Currency(StrEnum):
    AMD = "AMD"
    GEL = "GEL"
    RSD = "RSD"
    PLN = "PLN"
    EUR = "EUR"


class SourceKind(StrEnum):
    """How a source is reached. This is also the priority band."""

    proxy_direct = "proxy_direct"
    official = "official"
    third_party = "third_party"
    statistics = "statistics"


class Reliability(StrEnum):
    official = "official"
    third_party = "third_party"
    statistics = "statistics"


class ExtractionMethod(StrEnum):
    regex = "regex"
    llm_text = "llm_text"
    llm_vision = "llm_vision"
    api = "api"


class Mark(StrEnum):
    """Badges on a published line. Derived at calculation time, not by the adapter."""

    fixed = "fixed"
    norm_based = "norm_based"
    statistics_estimate = "statistics_estimate"
    third_party = "third_party"
    llm_extracted = "llm_extracted"
    stale = "stale"


class Basis(StrEnum):
    per_m2 = "per_m2"
    per_person = "per_person"
    per_dwelling = "per_dwelling"


class FeeBasis(StrEnum):
    per_m2 = "per_m2"
    per_dwelling = "per_dwelling"


class FeePeriod(StrEnum):
    month = "month"
    year = "year"


class CellStatus(StrEnum):
    ok = "ok"
    empty = "empty"


# Bill lines only. fx and hdd are context records, not articles in a city file.
CHARGE_ITEMS: tuple[Item, ...] = (
    Item.maintenance,
    Item.heating,
    Item.electricity,
    Item.gas,
    Item.water,
    Item.hot_water,
    Item.sewage,
    Item.waste,
)

ITEM_CATEGORY: dict[Item, Category] = {
    Item.maintenance: Category.housing,
    Item.heating: Category.utility,
    Item.electricity: Category.utility,
    Item.gas: Category.utility,
    Item.water: Category.utility,
    Item.hot_water: Category.utility,
    Item.sewage: Category.utility,
    Item.waste: Category.utility,
    Item.fx: Category.context,
    Item.hdd: Category.context,
    Item.hdd_annual: Category.context,
}

_HDD_ITEMS = frozenset({Item.hdd, Item.hdd_annual})

# Lower rank is tried first. Within a rank, SourceConfig.priority then file order.
SOURCE_KIND_RANK: dict[SourceKind, int] = {
    SourceKind.proxy_direct: 0,
    SourceKind.official: 1,
    SourceKind.third_party: 2,
    SourceKind.statistics: 3,
}

_KIND_RELIABILITY: dict[SourceKind, frozenset[Reliability]] = {
    SourceKind.official: frozenset({Reliability.official}),
    SourceKind.third_party: frozenset({Reliability.third_party}),
    SourceKind.statistics: frozenset({Reliability.statistics}),
    # A proxy only changes transport. Reliability is the upstream page's.
    SourceKind.proxy_direct: frozenset({Reliability.official, Reliability.third_party}),
}

HOUSE_CLASS_LABELS_RU: dict[HouseClass, str] = {
    HouseClass.soviet_panel: "Советский / панельный фонд",
    HouseClass.typical_newbuild: "Типовая новостройка 2000–2010-х",
    HouseClass.business: "Бизнес-класс",
}

HEATING_LABELS_RU: dict[HeatingType, str] = {
    HeatingType.district: "Центральное",
    HeatingType.gas_boiler: "Индивидуальный газовый котёл",
    HeatingType.electric: "Электрическое",
}

ITEM_LABELS_RU: dict[Item, str] = {
    Item.maintenance: "Обслуживание дома",
    Item.heating: "Отопление",
    Item.electricity: "Электричество",
    Item.gas: "Газ",
    Item.water: "Вода",
    Item.hot_water: "Горячая вода",
    Item.sewage: "Канализация",
    Item.waste: "Мусор",
    Item.fx: "Курс",
    Item.hdd: "Градусо-сутки",
    Item.hdd_annual: "Градусо-сутки за отопительный год",
}

MARK_LABELS_RU: dict[Mark, str] = {
    Mark.fixed: "фиксированный платёж",
    Mark.norm_based: "по норме расхода",
    Mark.statistics_estimate: "оценка по статистике",
    Mark.third_party: "сторонний сайт",
    Mark.llm_extracted: "извлечено моделью",
    Mark.stale: "значение не обновилось",
}

CITY_LABELS_RU: dict[CityId, str] = {
    CityId.yerevan: "Ереван",
    CityId.tbilisi: "Тбилиси",
    CityId.belgrade: "Белград",
    CityId.warsaw: "Варшава",
    CityId.lisbon: "Лиссабон",
    CityId.berlin: "Берлин",
}


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("number required")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("number must be finite")
    return number


def _parse_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}", text):
            return date.fromisoformat(f"{text}-01")
        return date.fromisoformat(text)
    raise ValueError("date must be YYYY-MM-DD or YYYY-MM")


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed = datetime.fromisoformat(text)
        elif re.fullmatch(r"\d{4}-\d{2}", text):
            parsed = datetime.fromisoformat(f"{text}-01")
        else:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        raise ValueError("datetime required")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


Finite = Annotated[float, BeforeValidator(_finite_number)]
FlexDate = Annotated[date, BeforeValidator(_parse_date)]
FlexDateTime = Annotated[datetime, BeforeValidator(_parse_datetime)]
HttpUrlString = Annotated[str, Field(min_length=1), AfterValidator(_require_http_url)]
OptionalHttpUrlString = Annotated[str | None, AfterValidator(_optional_http_url)]


def _check_coordinates(latitude: float | None, longitude: float | None) -> None:
    if (latitude is None) ^ (longitude is None):
        raise ValueError("latitude and longitude are set together")
    if latitude is not None and not -90 <= latitude <= 90:
        raise ValueError("latitude out of range")
    if longitude is not None and not -180 <= longitude <= 180:
        raise ValueError("longitude out of range")


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Tier(ContractModel):
    """One step of a stepped tariff.

    ``up_to`` is the exclusive upper bound in the record unit. Null is the open last step.
    """

    up_to: Finite | None = None
    value: Finite
    unit: str | None = None
    label: str | None = None

    @field_validator("up_to")
    @classmethod
    def _bound_positive(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("tier bound must be positive")
        return value


class SourceInfo(ContractModel):
    """Provenance stored on a normalized record."""

    kind: SourceKind
    url: HttpUrlString
    fetched_at: FlexDateTime
    name: str | None = None


class NormalizedRecord(ContractModel):
    """One tariff, fee, exchange rate, or degree-day figure.

    Adapters return these and stop. They do not know about cells or the monthly bill.
    """

    city: CityId | Literal["all"]
    item: Item
    category: Category
    value: Finite
    unit: str = Field(min_length=1, max_length=64)
    currency: Currency | None = None
    tiers: list[Tier] = Field(default_factory=list)
    valid_from: FlexDate
    valid_to: FlexDate | None = None
    source: SourceInfo
    method: ExtractionMethod
    reliability: Reliability
    note: str = ""

    @model_validator(mode="after")
    def _check_record(self) -> NormalizedRecord:
        expected = ITEM_CATEGORY[self.item]
        if self.category is not expected:
            raise ValueError(
                f"{self.item.value} belongs to category {expected.value}, got {self.category.value}"
            )
        if self.item is Item.fx:
            if self.city != "all":
                raise ValueError("exchange-rate records use city 'all'")
            if self.currency is None:
                raise ValueError("fx record needs a currency")
            expected_unit = f"{self.currency.value}/EUR"
            if self.unit != expected_unit:
                raise ValueError(f"fx unit must be {expected_unit}")
        elif self.item in _HDD_ITEMS:
            if self.city == "all":
                raise ValueError("degree-day records name a city")
            if self.unit != HDD_UNIT:
                raise ValueError(f"degree-day unit must be {HDD_UNIT}")
            if self.currency is not None:
                raise ValueError("degree-day records have no currency")
        elif self.city == "all":
            raise ValueError("only fx records may use city 'all'")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to is earlier than valid_from")
        if self.reliability not in _KIND_RELIABILITY[self.source.kind]:
            raise ValueError(
                f"reliability {self.reliability.value} does not match "
                f"source kind {self.source.kind.value}"
            )
        if self.value < 0:
            raise ValueError("value cannot be negative")
        _check_tiers(self.tiers)
        if self.currency is None and self.item not in _HDD_ITEMS and self.item is not Item.fx:
            raise ValueError("record needs a currency")
        return self


def _check_tiers(tiers: list[Tier]) -> None:
    if not tiers:
        return
    open_ended = [index for index, tier in enumerate(tiers) if tier.up_to is None]
    if open_ended and open_ended != [len(tiers) - 1]:
        raise ValueError("only the last tier may omit up_to")
    bounds = [tier.up_to for tier in tiers if tier.up_to is not None]
    if bounds != sorted(bounds) or len(bounds) != len(set(bounds)):
        raise ValueError("tier bounds must strictly increase")


class HousingFee(ContractModel):
    """Maintenance fee copied from a listing, with its own currency and period."""

    amount: Finite
    currency: Currency
    period: FeePeriod
    per: FeeBasis

    @field_validator("amount")
    @classmethod
    def _non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("housing fee cannot be negative")
        return value


class SalePrice(ContractModel):
    """Asking price of one square metre. Listings quote USD, so any ISO code is allowed."""

    amount: Finite = Field(gt=0)
    currency: str = Field(pattern=_CURRENCY_CODE)


class BuildingCandidate(ContractModel):
    """One apartment listing, before it is classified into a cell.

    Heating, year, and fees are often missing on the source site. Leave those null.
    ``heating_raw`` keeps the site's own word (``CG``, ``Elektrisch``); ``heating`` is
    set only when the adapter already knows the mapping. ``building_type`` is the
    site's construction word (Panel, MONOLITH), not a house class. ``block_type`` is
    the built form of the whole block from open data (Berlin Umweltatlas).
    ``amenities`` belong to the complex, not to the neighbourhood; an empty list means
    the ad names none. ``unfinished`` is a bare shell, so its price says nothing about
    the building.
    """

    id: str = Field(min_length=1, max_length=128)
    city: CityId
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    heating_raw: str | None = None
    heating: HeatingType | None = None
    year_built: int | None = None
    year_from: int | None = None
    year_to: int | None = None
    series: str | None = None
    building_type: str | None = None
    block_type: str | None = None
    is_newbuild: bool | None = None
    floor: int | None = None
    floors: int | None = None
    amenities: list[Amenity] = Field(default_factory=list)
    unfinished: bool | None = None
    area_m2: float | None = None
    housing_fee: HousingFee | None = None
    price_per_m2: SalePrice | None = None
    url: HttpUrlString
    seen_at: FlexDate
    note: str = ""

    @model_validator(mode="after")
    def _check_candidate(self) -> BuildingCandidate:
        _check_coordinates(self.latitude, self.longitude)
        for year in (self.year_built, self.year_from, self.year_to):
            if year is not None and not 1800 <= year <= 2100:
                raise ValueError("year out of range")
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise ValueError("year_from is after year_to")
        if self.floors is not None and self.floors < 1:
            raise ValueError("floors must be at least 1")
        if self.area_m2 is not None and self.area_m2 <= 0:
            raise ValueError("area_m2 must be positive")
        return self


class PinnedHouse(ContractModel):
    """A house already classified into one cell and kept from month to month.

    ``BuildingCandidate`` is the raw listing. This is the house the classifier
    pins and the cell calculation bills. ``listing_url`` is the ad the facts came
    from and stays after the ad is gone; ``last_seen`` is the last sighting.
    """

    id: str = Field(min_length=1, max_length=128)
    city: CityId
    house_class: HouseClass
    heating: HeatingType
    housing_fees: list[HousingFee] = Field(default_factory=list)
    listing_url: OptionalHttpUrlString = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    first_seen: FlexDate
    last_seen: FlexDate
    note: str = ""

    @model_validator(mode="after")
    def _check_pinned(self) -> PinnedHouse:
        _check_coordinates(self.latitude, self.longitude)
        if self.last_seen < self.first_seen:
            raise ValueError("last_seen is earlier than first_seen")
        return self


class SourceConfig(ContractModel):
    """One configured source of an article, an exchange rate, or degree-days.

    ``adapter`` is the registry name. ``settings`` is defined by that adapter
    (usually a ``url``). ``priority`` breaks ties inside the same ``kind``;
    a lower number is tried first. Kind always outranks priority:
    proxy_direct, then official, then third_party, then statistics.
    """

    id: str = Field(pattern=_ID.pattern)
    adapter: str = Field(pattern=_ID.pattern)
    kind: SourceKind
    priority: int = Field(default=0, ge=0, le=999)
    enabled: bool = True
    reliability: Reliability
    settings: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _kind_matches_reliability(self) -> SourceConfig:
        allowed = _KIND_RELIABILITY[self.kind]
        if self.reliability not in allowed:
            raise ValueError(
                f"kind {self.kind.value} requires reliability {[item.value for item in allowed]}"
            )
        return self


class ListingSite(ContractModel):
    """A listings site the building search may call. Empty until that ticket fills it."""

    id: str = Field(pattern=_ID.pattern)
    adapter: str = Field(pattern=_ID.pattern)
    enabled: bool = True
    url: OptionalHttpUrlString = None
    settings: dict[str, Any] = Field(default_factory=dict)


class ArticleSources(ContractModel):
    item: Item
    sources: list[SourceConfig] = Field(default_factory=list)

    @field_validator("item")
    @classmethod
    def _bill_item(cls, value: Item) -> Item:
        if value not in CHARGE_ITEMS:
            raise ValueError("fx and degree-days are not articles; use fx_sources and hdd_sources")
        return value


class PriceReference(ContractModel):
    """Average price of 1 m2 in one district. ``district`` is the word the listings use."""

    district: str = Field(min_length=1)
    amount: Finite = Field(gt=0)
    currency: str = Field(pattern=_CURRENCY_CODE)
    source: str = Field(min_length=1)


class HouseClassRule(ContractModel):
    """Facts that place a listing in one house class.

    Every condition that is set must hold, and a fact the listing does not have
    never satisfies a condition. Words are compared without case; ``block_types``
    match a part of the text. Years must lie inside the bounds as a whole range.
    ``price_ratio_*`` compare the asking price with the district's ``PriceReference``.
    ``fee_per_m2_from`` is a monthly fee in the city currency. ``amenities_any`` needs
    one of them, ``amenities_none`` none of them. ``exclude_newbuild`` only rejects a
    listing that says it is new, ``exclude_series`` one that names the series.
    """

    house_class: HouseClass
    building_types: list[str] = Field(default_factory=list)
    series: list[str] = Field(default_factory=list)
    block_types: list[str] = Field(default_factory=list)
    is_newbuild: bool | None = None
    exclude_newbuild: bool = False
    exclude_series: list[str] = Field(default_factory=list)
    amenities_any: list[Amenity] = Field(default_factory=list)
    amenities_none: list[Amenity] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    floors_from: int | None = None
    floors_to: int | None = None
    price_ratio_from: Finite | None = None
    price_ratio_to: Finite | None = None
    fee_per_m2_from: Finite | None = None
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _has_a_condition(self) -> HouseClassRule:
        conditions = self.model_dump(
            exclude={
                "house_class",
                "reason",
                "exclude_newbuild",
                "exclude_series",
                "amenities_none",
            },
            exclude_defaults=True,
        )
        if not conditions:
            raise ValueError("a class rule needs at least one condition")
        return self


class HeatingRule(ContractModel):
    """Site words that mean one heating type. ``heating_raw`` is split at ``|``.

    ``explicit``: the ad states this heating outright, so its house is preferred.
    """

    heating: HeatingType
    explicit: bool = False
    all_of: list[str] = Field(min_length=1)
    none_of: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class HouseRules(ContractModel):
    """How one city's listings become cells. Thresholds live here, not in code.

    ``classes`` are tried in order and the first match wins. Heating rules must
    agree: two different types on one listing leave it unclassified.
    ``unavailable_heating`` names columns where no house is ever pinned, with the
    reason the empty cell shows.
    """

    classes: list[HouseClassRule] = Field(default_factory=list)
    heatings: list[HeatingRule] = Field(default_factory=list)
    price_references: list[PriceReference] = Field(default_factory=list)
    unavailable_heating: dict[HeatingType, str] = Field(default_factory=dict)


class OpenDataKind(StrEnum):
    nominatim = "nominatim"
    osm_levels = "osm_levels"
    berlin_alkis = "berlin_alkis"
    berlin_building_age = "berlin_building_age"


class OpenDataSource(ContractModel):
    """An open register that fills facts a listing left out."""

    kind: OpenDataKind
    enabled: bool = True
    url: HttpUrlString


class CityConfig(ContractModel):
    """Everything that differs per city. Adding a city is a new file, not a calculation change."""

    schema_version: Literal[1] = SCHEMA_VERSION
    id: CityId
    name_ru: str = Field(min_length=1)
    name_en: str = Field(min_length=1)
    currency: Currency
    latitude: float
    longitude: float
    paid_data_note: str = Field(min_length=1)
    articles: list[ArticleSources]
    listing_sites: list[ListingSite] = Field(default_factory=list)
    house_rules: HouseRules = Field(default_factory=HouseRules)
    open_data: list[OpenDataSource] = Field(default_factory=list)
    fx_sources: list[SourceConfig] = Field(default_factory=list)
    hdd_sources: list[SourceConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_city(self) -> CityConfig:
        if not -90 <= self.latitude <= 90 or not -180 <= self.longitude <= 180:
            raise ValueError("coordinates out of range")
        items = [article.item for article in self.articles]
        if len(items) != len(set(items)):
            raise ValueError("duplicate article")
        missing = [item.value for item in CHARGE_ITEMS if item not in items]
        if missing:
            raise ValueError(f"city is missing articles: {', '.join(missing)}")
        ids: list[str] = []
        for article in self.articles:
            ids.extend(source.id for source in article.sources)
        ids.extend(source.id for source in self.fx_sources)
        ids.extend(source.id for source in self.hdd_sources)
        ids.extend(site.id for site in self.listing_sites)
        if len(ids) != len(set(ids)):
            raise ValueError("source and listing ids must be unique inside a city")
        return self


class ReferenceApartment(ContractModel):
    """The same dwelling in every city. Locked to the decision in the map."""

    rooms: Literal[2] = 2
    area_m2: Literal[60] = 60
    occupants: Literal[2] = 2


class ConsumptionNorm(ContractModel):
    """Annual use for one article. Real TABULA/UNDP/INE figures replace the stubs.

    ``heating`` is null when the norm does not depend on the heating type
    (electricity and water per person).
    """

    city: CityId
    house_class: HouseClass
    heating: HeatingType | None = None
    item: Item
    annual_value: Finite
    unit: str = Field(min_length=1, max_length=64)
    basis: Basis
    source: str = Field(min_length=1)
    note: str = ""

    @field_validator("item")
    @classmethod
    def _consumable(cls, value: Item) -> Item:
        if value in {Item.fx, *_HDD_ITEMS}:
            raise ValueError("a consumption norm is not an exchange rate or degree-days")
        return value

    @field_validator("annual_value")
    @classmethod
    def _non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("annual_value cannot be negative")
        return value


class ReferenceData(ContractModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    apartment: ReferenceApartment
    norms: list[ConsumptionNorm]


class LineItem(ContractModel):
    """One article inside a published cell, already converted to euros."""

    item: Item
    category: Category
    amount_eur: Finite
    source_url: HttpUrlString
    source_kind: SourceKind
    valid_from: FlexDate
    marks: list[Mark] = Field(default_factory=list)
    note: str = ""

    @field_validator("item")
    @classmethod
    def _bill_item(cls, value: Item) -> Item:
        if value not in CHARGE_ITEMS:
            raise ValueError("a published line is a bill article")
        return value

    @model_validator(mode="after")
    def _category_matches_item(self) -> LineItem:
        expected = ITEM_CATEGORY[self.item]
        if self.category is not expected:
            raise ValueError(f"{self.item.value} belongs to category {expected.value}")
        if self.amount_eur < 0:
            raise ValueError("amount_eur cannot be negative")
        if len(self.marks) != len(set(self.marks)):
            raise ValueError("duplicate mark")
        return self


class CellBuilding(ContractModel):
    """The real building a filled cell is about. Address or listing url, or both."""

    id: str = Field(min_length=1)
    address: str | None = None
    url: OptionalHttpUrlString = None

    @model_validator(mode="after")
    def _address_or_url(self) -> CellBuilding:
        if not self.address and not self.url:
            raise ValueError("building needs an address or a listing url")
        return self


class Cell(ContractModel):
    house_class: HouseClass
    heating: HeatingType
    status: CellStatus
    empty_reason: str | None = None
    total_eur: float | None = None
    housing_eur: float | None = None
    utility_eur: float | None = None
    lines: list[LineItem] = Field(default_factory=list)
    building: CellBuilding | None = None

    @model_validator(mode="after")
    def _status_shape(self) -> Cell:
        if self.status is CellStatus.empty:
            if not self.empty_reason:
                raise ValueError("empty cell needs empty_reason")
            if (
                self.total_eur is not None
                or self.housing_eur is not None
                or self.utility_eur is not None
            ):
                raise ValueError("empty cell has no totals")
            if self.lines or self.building is not None:
                raise ValueError("empty cell has no lines and no building")
            return self
        if self.empty_reason:
            raise ValueError("filled cell has no empty_reason")
        if self.total_eur is None or self.housing_eur is None or self.utility_eur is None:
            raise ValueError("filled cell needs total_eur, housing_eur, and utility_eur")
        if self.building is None:
            raise ValueError("filled cell names the building it was calculated for")
        if not self.lines:
            raise ValueError("filled cell needs a breakdown")
        for amount in (self.total_eur, self.housing_eur, self.utility_eur):
            if not math.isfinite(amount) or amount < 0:
                raise ValueError("totals must be finite and non-negative")
        housing = sum(line.amount_eur for line in self.lines if line.category is Category.housing)
        utility = sum(line.amount_eur for line in self.lines if line.category is Category.utility)
        if not math.isclose(housing, self.housing_eur, abs_tol=0.02):
            raise ValueError("housing lines do not add up to housing_eur")
        if not math.isclose(utility, self.utility_eur, abs_tol=0.02):
            raise ValueError("utility lines do not add up to utility_eur")
        if not math.isclose(self.housing_eur + self.utility_eur, self.total_eur, abs_tol=0.02):
            raise ValueError("housing_eur + utility_eur must equal total_eur")
        return self


class ExchangeRate(ContractModel):
    """Local currency units for one euro, monthly average. EUR cities use 1."""

    currency: Currency
    per_eur: Finite
    month: str
    source_url: HttpUrlString
    source_name: str = Field(min_length=1)

    @field_validator("month")
    @classmethod
    def _month(cls, value: str) -> str:
        if not _MONTH.fullmatch(value):
            raise ValueError("month must be YYYY-MM")
        return value

    @model_validator(mode="after")
    def _rate(self) -> ExchangeRate:
        if self.per_eur <= 0:
            raise ValueError("per_eur must be positive")
        if self.currency is Currency.EUR and self.per_eur != 1:
            raise ValueError("EUR per_eur is 1")
        return self


class CityMonth(ContractModel):
    """Static JSON for one city and one month. The site reads only this shape.

    ``cells`` is exactly the nine combinations of house class and heating type.
    """

    schema_version: Literal[1] = SCHEMA_VERSION
    city: CityId
    month: str
    updated_at: FlexDateTime
    apartment: ReferenceApartment
    exchange_rate: ExchangeRate
    paid_data_note: str = Field(min_length=1)
    cells: list[Cell]

    @field_validator("month")
    @classmethod
    def _month(cls, value: str) -> str:
        if not _MONTH.fullmatch(value):
            raise ValueError("month must be YYYY-MM")
        return value

    @model_validator(mode="after")
    def _nine_cells(self) -> CityMonth:
        if self.exchange_rate.month != self.month:
            raise ValueError("exchange rate month must match the snapshot month")
        expected_currency = _currency_for(self.city)
        if self.exchange_rate.currency is not expected_currency:
            raise ValueError(f"{self.city.value} uses {expected_currency.value}")
        expected_pairs = {
            (house_class, heating) for house_class in HouseClass for heating in HeatingType
        }
        found = [(cell.house_class, cell.heating) for cell in self.cells]
        if len(found) != 9 or set(found) != expected_pairs:
            raise ValueError("snapshot must contain each of the 9 class × heating cells once")
        return self


def _currency_for(city: CityId) -> Currency:
    """Currency from that city's config file, the only copy of the mapping."""

    from ubc.load import load_city

    return load_city(city.value).currency


def sort_sources(sources: list[SourceConfig]) -> list[SourceConfig]:
    """Enabled and disabled sources, in the order the policy walks them.

    Disabled sources stay in the list. Call ``ordered_enabled_sources`` to drop them.
    """

    indexed = list(enumerate(sources))
    indexed.sort(key=lambda pair: (SOURCE_KIND_RANK[pair[1].kind], pair[1].priority, pair[0]))
    return [source for _, source in indexed]


def ordered_enabled_sources(sources: list[SourceConfig]) -> list[SourceConfig]:
    """Sources the selection policy may call, highest priority first."""

    return sort_sources([source for source in sources if source.enabled])
