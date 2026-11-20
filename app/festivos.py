"""Festivos de Colombia (Ley 51 de 1983, Ley Emiliani) y utilidades de calendario."""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


def dow(d: date) -> int:
    """0 = domingo … 6 = sábado (como el prototipo)."""
    return (d.weekday() + 1) % 7


def _pascua(anio: int) -> date:
    a, b, c = anio % 19, anio // 100, anio % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes = (h + l - 7 * m + 114) // 31
    dia = ((h + l - 7 * m + 114) % 31) + 1
    return date(anio, mes, dia)


def _al_lunes(d: date) -> date:
    ds = dow(d)
    return d if ds == 1 else d + timedelta(days=(8 - ds) % 7)


@lru_cache(maxsize=32)
def festivos_colombia(anio: int) -> dict[date, str]:
    p = _pascua(anio)
    f = lambda m, d: date(anio, m, d)  # noqa: E731
    lista = [
        (f(1, 1), "Año Nuevo"), (_al_lunes(f(1, 6)), "Reyes Magos"), (_al_lunes(f(3, 19)), "San José"),
        (p - timedelta(days=3), "Jueves Santo"), (p - timedelta(days=2), "Viernes Santo"), (f(5, 1), "Día del Trabajo"),
        (p + timedelta(days=43), "Ascensión del Señor"), (p + timedelta(days=64), "Corpus Christi"), (p + timedelta(days=71), "Sagrado Corazón"),
        (_al_lunes(f(6, 29)), "San Pedro y San Pablo"), (f(7, 20), "Independencia"), (f(8, 7), "Batalla de Boyacá"),
        (_al_lunes(f(8, 15)), "Asunción de la Virgen"), (_al_lunes(f(10, 12)), "Día de la Raza"), (_al_lunes(f(11, 1)), "Todos los Santos"),
        (_al_lunes(f(11, 11)), "Independencia de Cartagena"), (f(12, 8), "Inmaculada Concepción"), (f(12, 25), "Navidad"),
    ]
    return dict(sorted(lista))


def es_festivo(d: date, extra: set[date] | frozenset[date] = frozenset()) -> str | None:
    n = festivos_colombia(d.year).get(d)
    if n:
        return n
    return "Festivo adicional" if d in extra else None
