"""Rates the site converts euros with: dram and rouble per euro, by month.

The bill itself is calculated and stored in euros. These rates only let a visitor
read it in another currency, in any city, so they are not a city's ``fx_sources``
and a month without them is still published. One request to the Central Bank of
Armenia gives both: it quotes the euro and the rouble in drams.
"""

from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from ubc.adapters.fx_cba import daily_rates
from ubc.fetch import ContentFetcher
from ubc.load import ConfigError
from ubc.schemas import (
    SCHEMA_VERSION,
    ContractModel,
    Finite,
    HttpUrlString,
    YearMonth,
)

FILE_NAME = "display-rates.json"
_ENDPOINT = "https://api.cba.am/exchangerates.asmx"
_SOURCE_URL = "https://www.cba.am/"
_SOURCE_NAME = "Центральный банк Армении"


class DisplayRate(ContractModel):
    """Monthly average: how many drams and roubles one euro was."""

    month: str
    amd_per_eur: Finite = Field(gt=0)
    rub_per_eur: Finite = Field(gt=0)
    source_url: HttpUrlString
    source_name: str = Field(min_length=1)

    @field_validator("month")
    @classmethod
    def _month(cls, value: str) -> str:
        return str(YearMonth.parse(value))


class DisplayRates(ContractModel):
    """``data/display-rates.json``. The site reads only this shape."""

    schema_version: Literal[1] = SCHEMA_VERSION
    rates: list[DisplayRate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_per_month(self) -> DisplayRates:
        months = [rate.month for rate in self.rates]
        if months != sorted(set(months)):
            raise ValueError("rates are sorted by month, one per month")
        return self

    def with_rate(self, rate: DisplayRate) -> DisplayRates:
        """The same rates with this month added or replaced."""

        kept = [other for other in self.rates if other.month != rate.month]
        return DisplayRates(rates=sorted([*kept, rate], key=lambda one: one.month))


def collect_display_rate(period: YearMonth, fetcher: ContentFetcher) -> DisplayRate:
    """Ask the bank. The rouble is the mean of each banking day's cross rate."""

    quoted = daily_rates(fetcher, _ENDPOINT, period, ("EUR", "RUB"))
    euro, rouble = quoted["EUR"], quoted["RUB"]
    if set(euro) != set(rouble):
        raise ValueError("CBA quoted the euro and the rouble on different days")
    return DisplayRate(
        month=str(period),
        amd_per_eur=round(mean(euro.values()), 4),
        rub_per_eur=round(mean(euro[day] / rouble[day] for day in euro), 4),
        source_url=_SOURCE_URL,
        source_name=_SOURCE_NAME,
    )


def load_display_rates(data_root: Path) -> DisplayRates:
    """The saved rates. No file yet means no rates."""

    path = data_root / FILE_NAME
    if not path.is_file():
        return DisplayRates()
    try:
        return DisplayRates.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
