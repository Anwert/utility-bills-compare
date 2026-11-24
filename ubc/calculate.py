"""Monthly bill for one cell (house class × heating type).

Inputs are normalized records, the pinned house, consumption norms, and the
reference apartment. The result is one ``Cell`` of the site contract.

A missing house is an empty cell. A missing exchange rate, tariff, consumption
norm, or degree-day total raises ``CalculationError`` instead of publishing a
zero. Degree-days themselves are inputs; this module does not read temperatures.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from ubc.schemas import (
    ITEM_CATEGORY,
    Basis,
    Category,
    Cell,
    CellBuilding,
    CellStatus,
    CityId,
    ConsumptionNorm,
    Currency,
    ExtractionMethod,
    FeeBasis,
    FeePeriod,
    HeatingType,
    HouseClass,
    Item,
    LineItem,
    Mark,
    NormalizedRecord,
    PinnedHouse,
    ReferenceApartment,
    Reliability,
    YearMonth,
)

_EMPTY_REASON = "дом такого класса с таким отоплением автоматически не найден"
_MARK_ORDER = (
    Mark.fixed,
    Mark.norm_based,
    Mark.statistics_estimate,
    Mark.third_party,
    Mark.llm_extracted,
    Mark.stale,
)
_SUBUNIT: dict[str, tuple[Decimal, Currency]] = {
    "tetri": (Decimal("0.01"), Currency.GEL),
    "ct": (Decimal("0.01"), Currency.EUR),
    "eurocent": (Decimal("0.01"), Currency.EUR),
    "grosz": (Decimal("0.01"), Currency.PLN),
    "gr": (Decimal("0.01"), Currency.PLN),
}
_ENERGY = frozenset({"kwh", "gj"})
_KWH_TO_GJ = Decimal("0.0036")


class CalculationError(Exception):
    """The month cannot be priced. Nothing is published as zero."""


@dataclass(frozen=True, slots=True)
class _PriceUnit:
    scale: Decimal
    forced_currency: Currency | None
    commodity: str | None
    per: Literal["m2", "person", "dwelling"]
    period: Literal["month", "year"] | None


@dataclass(frozen=True, slots=True)
class _NormUnit:
    commodity: str
    per: Literal["m2", "person", "dwelling"]


def calculate_cell(
    *,
    period: YearMonth,
    city: CityId | str,
    house_class: HouseClass | str,
    heating: HeatingType | str,
    house: PinnedHouse | None,
    records: Sequence[NormalizedRecord],
    norms: Sequence[ConsumptionNorm],
    apartment: ReferenceApartment | None = None,
    extra_marks: Mapping[Item, Sequence[Mark]] | None = None,
    empty_reason: str | None = None,
) -> Cell:
    """Price one cell for ``period``.

    ``extra_marks`` carries badges the source-selection policy already decided
    (``stale``). Fixed, norm-based, statistics, third-party, and model-extracted
    badges are derived here.
    """

    city_id = CityId(city)
    row = HouseClass(house_class)
    column = HeatingType(heating)
    if house is None:
        return Cell(
            house_class=row,
            heating=column,
            status=CellStatus.empty,
            empty_reason=empty_reason or _EMPTY_REASON,
        )
    if house.city is not city_id or house.house_class is not row or house.heating is not column:
        raise CalculationError("дом не относится к этой ячейке")
    if not house.address and not house.listing_url:
        raise CalculationError("нет источника жилищных сборов")

    dwelling = apartment or ReferenceApartment()
    marks = extra_marks or {}
    month = _Month(period, city_id, list(records))
    lines: list[tuple[int, int, LineItem]] = []
    sequence = 0

    def add(item: Item, built: LineItem) -> None:
        nonlocal sequence
        lines.append((list(Item).index(item), sequence, built))
        sequence += 1

    if house.housing_fees:
        add(
            Item.maintenance,
            _housing_fee_line(house, dwelling, month, marks),
        )

    present = {
        record.item
        for record in records
        if record.city == city_id
        and record.item in ITEM_CATEGORY
        and ITEM_CATEGORY[record.item] is not Category.context
    }
    _require_fuel(column, present)

    for item in ITEM_CATEGORY:
        if ITEM_CATEGORY[item] is Category.context:
            continue
        if item is Item.heating and column is not HeatingType.district:
            continue
        if item is Item.maintenance and house.housing_fees:
            continue
        if item is Item.gas and column is not HeatingType.gas_boiler:
            if not _cooking_gas(norms, city_id, row):
                continue
        if item not in present:
            continue
        item_records = [
            record for record in records if record.city == city_id and record.item is item
        ]
        for kind, grouped in _families(item_records):
            if item is Item.gas and column is not HeatingType.gas_boiler and kind != "commodity":
                continue
            built = _price_family(
                item=item,
                grouped=grouped,
                column=column,
                norms=norms,
                city_id=city_id,
                row=row,
                dwelling=dwelling,
                month=month,
                marks=marks,
            )
            for built_item, built_line in built:
                add(built_item, built_line)

    ordered = [built for _, _, built in sorted(lines, key=lambda trio: (trio[0], trio[1]))]
    if not ordered:
        raise CalculationError("нет тарифа на месяц")
    housing = _total(ordered, Category.housing)
    utility = _total(ordered, Category.utility)
    return Cell(
        house_class=row,
        heating=column,
        status=CellStatus.ok,
        total_eur=float(housing + utility),
        housing_eur=float(housing),
        utility_eur=float(utility),
        lines=ordered,
        building=CellBuilding(id=house.id, address=house.address, url=house.listing_url),
    )


def _require_fuel(column: HeatingType, present: set[Item]) -> None:
    required = {
        HeatingType.district: Item.heating,
        HeatingType.gas_boiler: Item.gas,
        HeatingType.electric: Item.electricity,
    }[column]
    if required not in present:
        raise CalculationError("нет тарифа на месяц")


def _cooking_gas(norms: Sequence[ConsumptionNorm], city: CityId, row: HouseClass) -> bool:
    return _select_norm(norms, city, row, Item.gas, None) is not None


class _Month:
    def __init__(self, period: YearMonth, city: CityId, records: list[NormalizedRecord]) -> None:
        self.period = period
        self.city = city
        self.records = records
        self.days = _month_days(period)
        self._hdd: Decimal | None = None
        self._annual: Decimal | None = None

    def hdd(self) -> Decimal:
        if self._hdd is None:
            stamped = [
                record
                for record in self.records
                if record.item is Item.hdd
                and record.city == self.city
                and record.valid_from.year == self.period.year
                and record.valid_from.month == self.period.month
            ]
            if not stamped:
                raise CalculationError("нет градусо-суток на месяц")
            chosen = max(stamped, key=lambda record: (record.valid_from, record.source.fetched_at))
            self._hdd = Decimal(str(chosen.value))
        return self._hdd

    def hdd_annual(self) -> Decimal:
        if self._annual is None:
            last = self.period.last_day
            first = self.period.first_day
            overlapping = [
                record
                for record in self.records
                if record.item is Item.hdd_annual
                and record.city == self.city
                and record.valid_from <= last
                and (record.valid_to is None or record.valid_to >= first)
            ]
            if not overlapping:
                raise CalculationError("нет градусо-суток за отопительный год")
            chosen = max(
                overlapping,
                key=lambda record: (record.valid_from, record.source.fetched_at),
            )
            value = Decimal(str(chosen.value))
            if value <= 0:
                raise CalculationError("нет градусо-суток за отопительный год")
            self._annual = value
        return self._annual

    def rate(self, day: date, currency: Currency) -> Decimal:
        if currency is Currency.EUR and not any(
            record.item is Item.fx and record.currency is currency for record in self.records
        ):
            return Decimal(1)
        chosen = _pick(
            [
                record
                for record in self.records
                if record.item is Item.fx and record.currency is currency
            ],
            day,
            "нет курса на месяц",
        )
        rate = Decimal(str(chosen.value))
        if rate <= 0:
            raise CalculationError("нет курса на месяц")
        return rate

    def to_eur(self, local: Decimal, currency: Currency) -> Decimal:
        total = Decimal(0)
        count = Decimal(len(self.days))
        for day in self.days:
            total += (local / self.rate(day, currency)) / count
        return total


def _price_family(
    *,
    item: Item,
    grouped: list[NormalizedRecord],
    column: HeatingType,
    norms: Sequence[ConsumptionNorm],
    city_id: CityId,
    row: HouseClass,
    dwelling: ReferenceApartment,
    month: _Month,
    marks: Mapping[Item, Sequence[Mark]],
) -> list[tuple[Item, LineItem]]:
    sample = _parse_price(grouped[0].unit)
    chosen = [_pick(grouped, day, "нет тарифа на месяц") for day in month.days]
    if sample.commodity is None:
        amounts = _weighted(month, chosen, [None], dwelling)
        return [
            (
                item,
                _line(
                    item,
                    amounts[0],
                    chosen,
                    consumption=False,
                    norm=None,
                    marks=marks,
                    also=(),
                ),
            )
        ]

    if item is Item.electricity and column is HeatingType.electric:
        base_norm = _need_norm(norms, city_id, row, Item.electricity, None)
        heat_norm = _need_norm(norms, city_id, row, Item.heating, column)
        base_qty, base_commodity = _monthly_quantity(base_norm, dwelling, month)
        heat_qty, heat_commodity = _monthly_quantity(heat_norm, dwelling, month)
        base = _as_commodity(base_qty, base_commodity, sample.commodity)
        total = _as_commodity(
            _to_kwh(base_qty, base_commodity) + _to_kwh(heat_qty, heat_commodity),
            "kwh",
            sample.commodity,
        )
        base_eur, total_eur = _weighted(month, chosen, [base, total], dwelling)
        return [
            (
                Item.electricity,
                _line(
                    Item.electricity,
                    base_eur,
                    chosen,
                    consumption=True,
                    norm=base_norm,
                    marks=marks,
                    also=(),
                ),
            ),
            (
                Item.heating,
                _line(
                    Item.heating,
                    total_eur - base_eur,
                    chosen,
                    consumption=True,
                    norm=heat_norm,
                    marks=marks,
                    also=(Item.electricity,),
                ),
            ),
        ]

    quantity, norm = _consumption(
        item, sample.commodity, column, norms, city_id, row, dwelling, month
    )
    (amount,) = _weighted(month, chosen, [quantity], dwelling)
    return [
        (
            item,
            _line(item, amount, chosen, consumption=True, norm=norm, marks=marks, also=()),
        )
    ]


def _consumption(
    item: Item,
    commodity: str,
    column: HeatingType,
    norms: Sequence[ConsumptionNorm],
    city: CityId,
    row: HouseClass,
    dwelling: ReferenceApartment,
    month: _Month,
) -> tuple[Decimal, ConsumptionNorm]:
    if item is Item.sewage and commodity == "m3":
        chosen = _need_norm(norms, city, row, Item.water, None)
    elif item is Item.gas and column is HeatingType.gas_boiler:
        chosen = _select_norm(norms, city, row, Item.gas, column) or _select_norm(
            norms, city, row, Item.heating, column
        )
        if chosen is None:
            raise CalculationError("нет нормы расхода")
    elif item is Item.heating:
        chosen = _need_norm(norms, city, row, Item.heating, column)
    else:
        chosen = _need_norm(norms, city, row, item, None)
    quantity, source_commodity = _monthly_quantity(chosen, dwelling, month)
    return _as_commodity(quantity, source_commodity, commodity), chosen


def _weighted(
    month: _Month,
    chosen: list[NormalizedRecord],
    quantities: list[Decimal | None],
    dwelling: ReferenceApartment,
) -> list[Decimal]:
    totals = [Decimal(0) for _ in quantities]
    count = Decimal(len(month.days))
    for record, day in zip(chosen, month.days, strict=True):
        currency = record.currency
        if currency is None:
            raise CalculationError("нет тарифа на месяц")
        rate = month.rate(day, currency)
        for index, quantity in enumerate(quantities):
            local = _full_month(record, quantity, dwelling)
            totals[index] += (local / rate) / count
    return totals


def _full_month(
    record: NormalizedRecord,
    quantity: Decimal | None,
    dwelling: ReferenceApartment,
) -> Decimal:
    unit = _parse_price(record.unit)
    if unit.forced_currency is not None and record.currency is not unit.forced_currency:
        raise CalculationError("валюта записи не совпадает с единицей")
    if unit.commodity is not None:
        if quantity is None:
            raise CalculationError("нет нормы расхода")
        return _tiered(quantity, record) * unit.scale
    if record.tiers:
        raise CalculationError("ступени только у тарифа по расходу")
    if unit.period is None:
        raise CalculationError(f"неизвестная единица {record.unit}")
    factor = {
        "m2": Decimal(dwelling.area_m2),
        "person": Decimal(dwelling.occupants),
        "dwelling": Decimal(1),
    }[unit.per]
    divisor = Decimal(1) if unit.period == "month" else Decimal(12)
    return Decimal(str(record.value)) * factor * unit.scale / divisor


def _tiered(quantity: Decimal, record: NormalizedRecord) -> Decimal:
    if not record.tiers:
        return quantity * Decimal(str(record.value))
    remaining = quantity
    previous = Decimal(0)
    cost = Decimal(0)
    for tier in record.tiers:
        price = Decimal(str(tier.value))
        if tier.up_to is None:
            return cost + remaining * price
        width = Decimal(str(tier.up_to)) - previous
        take = remaining if remaining < width else width
        cost += take * price
        remaining -= take
        previous = Decimal(str(tier.up_to))
        if remaining <= 0:
            return cost
    raise CalculationError("расход выше последней ступени")


def _line(
    item: Item,
    amount: Decimal,
    chosen: list[NormalizedRecord],
    *,
    consumption: bool,
    norm: ConsumptionNorm | None,
    marks: Mapping[Item, Sequence[Mark]],
    also: tuple[Item, ...],
) -> LineItem:
    closing = chosen[-1]
    found = {Mark.norm_based if consumption else Mark.fixed}
    for record in chosen:
        if record.reliability is Reliability.statistics:
            found.add(Mark.statistics_estimate)
        elif record.reliability is Reliability.third_party:
            found.add(Mark.third_party)
        if record.method in {ExtractionMethod.llm_text, ExtractionMethod.llm_vision}:
            found.add(Mark.llm_extracted)
    for key in (item, *also):
        found.update(marks.get(key, ()))
    note_parts = [part for part in (closing.note, f"Норма: {norm.source}" if norm else "") if part]
    distinct = {id(record) for record in chosen}
    if len(distinct) > 1:
        note_parts.append("Тариф сменился внутри месяца, сумма пропорциональна дням.")
    return LineItem(
        item=item,
        category=ITEM_CATEGORY[item],
        amount_eur=_euros(amount),
        source_url=closing.source.url,
        source_kind=closing.source.kind,
        valid_from=closing.valid_from,
        marks=[mark for mark in _MARK_ORDER if mark in found],
        note=" ".join(note_parts),
    )


def _housing_fee_line(
    house: PinnedHouse,
    dwelling: ReferenceApartment,
    month: _Month,
    marks: Mapping[Item, Sequence[Mark]],
) -> LineItem:
    if not house.listing_url:
        raise CalculationError("нет источника жилищных сборов")
    total = Decimal(0)
    for fee in house.housing_fees:
        local = Decimal(str(fee.amount))
        if fee.per is FeeBasis.per_m2:
            local *= Decimal(dwelling.area_m2)
        if fee.period is FeePeriod.year:
            local /= Decimal(12)
        total += month.to_eur(local, fee.currency)
    found = {Mark.fixed, Mark.third_party}
    found.update(marks.get(Item.maintenance, ()))
    return LineItem(
        item=Item.maintenance,
        category=Category.housing,
        amount_eur=_euros(total),
        source_url=house.listing_url,
        source_kind="third_party",
        valid_from=house.last_seen,
        marks=[mark for mark in _MARK_ORDER if mark in found],
        note=house.note,
    )


def _families(records: list[NormalizedRecord]) -> list[tuple[str, list[NormalizedRecord]]]:
    groups: dict[tuple[str, ...], list[NormalizedRecord]] = {}
    seen: dict[tuple[str, ...], int] = {}
    for record in records:
        unit = _parse_price(record.unit)
        if unit.commodity:
            key = ("commodity", unit.commodity)
        else:
            key = ("fixed", unit.per, unit.period or "")
        if key not in groups:
            seen[key] = len(seen)
            groups[key] = []
        groups[key].append(record)
    commodities = [key for key in groups if key[0] == "commodity"]
    if len(commodities) > 1:
        raise CalculationError("несколько расходных тарифов")
    ordered = sorted(groups, key=lambda key: (key[0] != "fixed", seen[key]))
    return [(key[0], groups[key]) for key in ordered]


def _monthly_quantity(
    chosen: ConsumptionNorm,
    dwelling: ReferenceApartment,
    month: _Month,
) -> tuple[Decimal, str]:
    unit = _parse_norm(chosen.unit)
    expected = {
        Basis.per_m2: "m2",
        Basis.per_person: "person",
        Basis.per_dwelling: "dwelling",
    }[chosen.basis]
    if unit.per != expected:
        raise CalculationError("норма не совпадает с основанием")
    quantity = Decimal(str(chosen.annual_value))
    if chosen.basis is Basis.per_m2:
        quantity *= Decimal(dwelling.area_m2)
    elif chosen.basis is Basis.per_person:
        quantity *= Decimal(dwelling.occupants)
    if chosen.item is Item.heating or chosen.heating is not None:
        quantity *= month.hdd() / month.hdd_annual()
    else:
        quantity /= Decimal(12)
    return quantity, unit.commodity


def _need_norm(
    norms: Sequence[ConsumptionNorm],
    city: CityId,
    row: HouseClass,
    item: Item,
    heating: HeatingType | None,
) -> ConsumptionNorm:
    chosen = _select_norm(norms, city, row, item, heating)
    if chosen is None:
        raise CalculationError("нет нормы расхода")
    return chosen


def _select_norm(
    norms: Sequence[ConsumptionNorm],
    city: CityId,
    row: HouseClass,
    item: Item,
    heating: HeatingType | None,
) -> ConsumptionNorm | None:
    pool = [
        norm
        for norm in norms
        if norm.city is city and norm.house_class is row and norm.item is item
    ]
    exact = [norm for norm in pool if norm.heating == heating]
    if len(exact) > 1:
        raise CalculationError("несколько норм расхода")
    if len(exact) == 1:
        return exact[0]
    if heating is not None:
        generic = [norm for norm in pool if norm.heating is None]
        if len(generic) > 1:
            raise CalculationError("несколько норм расхода")
        if len(generic) == 1:
            return generic[0]
    return None


def _parse_price(text: str) -> _PriceUnit:
    parts = [part.strip().lower() for part in text.split("/") if part.strip()]
    if len(parts) < 2:
        raise CalculationError(f"неизвестная единица {text}")
    head, *dims = parts
    if head == "local":
        scale, forced = Decimal(1), None
    elif head in _SUBUNIT:
        scale, forced = _SUBUNIT[head]
    elif head.upper() in Currency.__members__:
        scale, forced = Decimal(1), Currency[head.upper()]
    else:
        raise CalculationError(f"неизвестная единица {text}")
    commodity: str | None = None
    per: Literal["m2", "person", "dwelling"] = "dwelling"
    period: Literal["month", "year"] | None = None
    for dim in dims:
        if dim in _ENERGY or dim == "m3":
            if commodity is not None:
                raise CalculationError(f"неизвестная единица {text}")
            commodity = dim
        elif dim in {"m2", "person", "dwelling"}:
            per = dim  # type: ignore[assignment]
        elif dim in {"month", "year"}:
            period = dim  # type: ignore[assignment]
        else:
            raise CalculationError(f"неизвестная единица {text}")
    if commodity is None and period is None:
        raise CalculationError(f"неизвестная единица {text}")
    return _PriceUnit(scale, forced, commodity, per, period)


def _parse_norm(text: str) -> _NormUnit:
    parts = [part.strip().lower() for part in text.split("/") if part.strip()]
    if not parts or parts[0] not in {"kwh", "m3", "gj"}:
        raise CalculationError(f"неизвестная единица нормы {text}")
    commodity = parts[0]
    per: Literal["m2", "person", "dwelling"] = "dwelling"
    period: str | None = None
    for dim in parts[1:]:
        if dim in {"m2", "person", "dwelling"}:
            per = dim  # type: ignore[assignment]
        elif dim == "year":
            period = dim
        else:
            raise CalculationError(f"неизвестная единица нормы {text}")
    if period != "year":
        raise CalculationError(f"норма {text} должна быть годовой")
    return _NormUnit(commodity, per)


def _as_commodity(quantity: Decimal, source: str, target: str) -> Decimal:
    if source == target:
        return quantity
    if source in _ENERGY and target in _ENERGY:
        return _from_kwh(_to_kwh(quantity, source), target)
    raise CalculationError("единица нормы не совпадает с тарифом")


def _to_kwh(quantity: Decimal, commodity: str) -> Decimal:
    if commodity == "kwh":
        return quantity
    if commodity == "gj":
        return quantity / _KWH_TO_GJ
    raise CalculationError("единица нормы не совпадает с тарифом")


def _from_kwh(quantity: Decimal, commodity: str) -> Decimal:
    if commodity == "kwh":
        return quantity
    if commodity == "gj":
        return quantity * _KWH_TO_GJ
    raise CalculationError("единица нормы не совпадает с тарифом")


def _pick(records: list[NormalizedRecord], day: date, missing: str) -> NormalizedRecord:
    covering = [
        record
        for record in records
        if record.valid_from <= day and (record.valid_to is None or record.valid_to >= day)
    ]
    if not covering:
        raise CalculationError(missing)
    return max(covering, key=lambda record: (record.valid_from, record.source.fetched_at))


def _month_days(period: YearMonth) -> list[date]:
    days: list[date] = []
    current = period.first_day
    while current <= period.last_day:
        days.append(current)
        current += timedelta(days=1)
    return days


def _euros(amount: Decimal) -> float:
    return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _total(lines: list[LineItem], category: Category) -> Decimal:
    return sum(
        (Decimal(f"{line.amount_eur:.2f}") for line in lines if line.category is category),
        Decimal(0),
    )
