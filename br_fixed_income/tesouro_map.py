"""Map between the YAML bond codes and the names used by the Tesouro sources."""

import re


# Broker acronyms: LTN = PRE; NTN-F = PREJ; NTN-B Principal = IPCA;
# NTN-B = IPCAJ; LFT = SELIC; NTN-C = IGPMJ.
TESOURO_BONDS: dict[str, str] = {
    "PRE": "Tesouro Prefixado",
    "PREJ": "Tesouro Prefixado com Juros Semestrais",
    "IPCA": "Tesouro IPCA+",
    "IPCAJ": "Tesouro IPCA+ com Juros Semestrais",
    "SELIC": "Tesouro Selic",
    "IGPMJ": "Tesouro IGPM+ com Juros Semestrais",
    "RENDA": "Tesouro Renda+ Aposentadoria Extra",
    "EDUCA": "Tesouro Educa+",
    "RESERVA": "Tesouro Reserva",
}

_TRAILING_YEAR = re.compile(r"\s+\d{4}$")


def normalize_name(name: str) -> str:
    return " ".join(name.split()).casefold()


def strip_year(name: str) -> str:
    """'Tesouro Prefixado 2032' -> 'Tesouro Prefixado'."""
    return _TRAILING_YEAR.sub("", name.strip())
