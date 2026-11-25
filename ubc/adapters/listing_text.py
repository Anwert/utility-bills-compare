"""What the free text of a Yerevan ad states outright, in Armenian, Russian or English.

Only narrow wording counts, sentence by sentence. A sentence with a negation or a
possibility ("a boiler can be installed") says nothing, and an amenity in a sentence
about the neighbourhood is not the building's own. The words for heating go to
``heating_raw``; the city's heating rules decide what they mean.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ubc.schemas import Amenity

STALINKA = "stalinka"

_SENTENCE_END = re.compile(r"[.!?։;\n]+")
_UNSAID = re.compile(
    r"\b(?:no|not|without|нет|без|не|չկա|չունի|առանց)\b"
    r"|possib|can be|возможн|հնարավոր"
)
_ELSEWHERE = re.compile(
    r"near|close to|neighbo|minute|walking distance|рядом|вблизи|недалеко|поблизости|неподалеку"
    r"|соседн|в шаговой|минут|մոտակա|\bմոտ\b|հարեւան|կողքի|րոպե"
)
_AMENITIES = {
    Amenity.swimming_pool: re.compile(r"swimming[- ]pool|бассейн|լողավազան"),
    Amenity.gym: re.compile(
        r"\bgym\b|fitness|тренажерн|фитнес|спортзал|спортивный зал|մարզասրահ|ֆիտնես"
    ),
    Amenity.tennis: re.compile(r"tennis court|теннисн\w+ корт|թենիսի (?:կորտ|դաշտ)"),
    Amenity.concierge: re.compile(
        r"concierge|24[/-](?:7|hour) security|консьерж|круглосуточн\w+ охран|կոնսիերժ"
        r"|շուրջօրյա (?:անվտանգ|պահպան|հսկող)"
    ),
}
_STALIN_ERA = re.compile(r"stalinka|stalin[- ]era|stalinist|сталинк|сталинск|ստալինկա|ստալինյան")

_GAS_BOILER = re.compile(
    r"gas(?:[- ]fired)? (?:heating )?boiler|газов\w+ (?:отопительн\w+ )?кот(?:ел|л)"
    r"|գազ(?:ի|ային) կաթսա|գազով աշխատող կաթսա|գազակաթսա|\bbaxi\b|\bбакси\b|\bբաքսի\b"
)
_INDIVIDUAL_HEATING = re.compile(
    r"(?:individual|autonomous) (?:gas )?heating"
    r"|(?:индивидуальн|автономн)\w+ (?:систем\w+ )?(?:газов\w+ )?отоплен"
    r"|անհատական (?:գազային )?ջեռուց|ջեռուցում\w* անհատական"
)
_GAS_SUPPLY = re.compile(r"gasifi|газифи|գազաֆիկաց")
_ELECTRIC_HEATING = re.compile(
    r"electric(?:al)? (?:heating|boiler)"
    r"|электрическ\w+ (?:отоплен|кот(?:ел|л))|электро(?:отоплен|кот(?:ел|л))"
    r"|отопление[ :—-]+электрическ"
    r"|էլեկտրական (?:ջեռուց|կաթսա)|էլեկտրակաթսա|ջեռուցումը[ ՝]+էլեկտրական"
)
_HEATING = re.compile(r"\bheat|отаплив|отоплен|обогрев|ջեռուց")
# The English "by" that ties heating to a device: not "heated, with air conditioner".
_HEATED_BY = (
    r"(?:heated (?:\w+ )?(?:by|with|through|via)"
    r"|heating\b.{0,40}?\b(?:by|through|via|means of))\b.*?"
)
_BY_AIR = re.compile(
    rf"{_HEATED_BY}air[- ]?condition|fan[- ]?coil|фан-?ко[ий]л|ֆան[ -]?կոյ?լ"
    r"|кондиционер(?:ом|ами)|(?:через|от|с помощью) кондиционер"
    r"|օդորակիչ(?:ով|ներով|ի միջոցով|ների միջոցով)"
)
_BY_FLOOR = re.compile(
    rf"{_HEATED_BY}(?:under ?-?floor|floor heating|heated floor)"
    r"|тепл(?:ым|ыми) пол(?:ом|ами)|(?:через|от) тепл\w+ пол"
    r"|տաք(?:ացվող)? հատակ(?:ով|ներով|ի միջոցով)"
)
# Water floors and fan coils fed by the flat's own boiler are not a heat source.
_BOILER_FED = re.compile(
    r"boiler|котел|котл|կաթսա|\bbaxi\b|individual heating|by gas|gas heating"
    r"|индивидуальн|автономн|газом|газов\w+ отоплен|անհատական|գազով"
)


@dataclass(frozen=True, slots=True)
class AdText:
    heating: tuple[str, ...]
    amenities: tuple[Amenity, ...]
    series: str | None


def read_text(text: str) -> AdText:
    plain = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    said = [
        " ".join(part.split())
        for part in _SENTENCE_END.split(plain)
        if part.strip() and not _UNSAID.search(part)
    ]
    own = [part for part in said if not _ELSEWHERE.search(part)]
    heated = [part for part in said if _HEATING.search(part) and not _BOILER_FED.search(part)]
    evidence = {
        "text_gas_boiler": _any(_GAS_BOILER, said),
        "text_individual_heating": _any(_INDIVIDUAL_HEATING, said),
        "text_gas_supply": _any(_GAS_SUPPLY, said),
        "text_air_heating": _any(_BY_AIR, heated),
        "text_floor_heating": _any(_BY_FLOOR, heated),
        "text_electric_heating": _any(_ELECTRIC_HEATING, said),
    }
    return AdText(
        heating=tuple(word for word, found in evidence.items() if found),
        amenities=tuple(item for item, words in _AMENITIES.items() if _any(words, own)),
        series=STALINKA if _any(_STALIN_ERA, said) else None,
    )


def _any(pattern: re.Pattern[str], sentences: list[str]) -> bool:
    return any(pattern.search(sentence) for sentence in sentences)
