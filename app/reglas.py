"""Reglas de negocio puras (sin base de datos), portadas del prototipo."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

DIAS_ALERTA = 30

MOTIVOS_INASISTENCIA = ("Incapacidad", "Permiso", "Calamidad doméstica", "Licencia", "Sin justificar", "Otro")
# Ausencias que, según el art. 173 del CST, no hacen perder el dominical
MOTIVOS_JUSTIFICADOS = ("Incapacidad", "Permiso", "Calamidad doméstica", "Licencia")


@dataclass
class Habilitacion:
    estado: str  # habilitado | por-vencer | vencido | sin-datos
    motivo: str
    dias_examen: int | None = None
    dias_alturas: int | None = None

    @property
    def puede_trabajar(self) -> bool:
        return self.estado in ("habilitado", "por-vencer")

    def a_dict(self) -> dict:
        return {"estado": self.estado, "motivo": self.motivo, "dias_examen": self.dias_examen, "dias_alturas": self.dias_alturas}


def habilitacion(t, fecha: date) -> Habilitacion:
    """Examen médico y curso de alturas vigentes en `fecha` (t: Trabajador)."""
    if not t.examen_vence or not t.alturas_vence:
        return Habilitacion("sin-datos", "Faltan fechas de examen o curso de alturas")
    de = (t.examen_vence - fecha).days
    da = (t.alturas_vence - fecha).days
    antes_examen = bool(t.examen_fecha and fecha < t.examen_fecha)
    antes_alturas = bool(t.alturas_fecha and fecha < t.alturas_fecha)
    if de < 0 or antes_examen:
        return Habilitacion("vencido", "Examen médico aún no realizado" if antes_examen else f"Examen médico vencido hace {-de} días", de, da)
    if da < 0 or antes_alturas:
        return Habilitacion("vencido", "Curso de alturas aún no realizado" if antes_alturas else f"Curso de alturas vencido hace {-da} días", de, da)
    if min(de, da) <= DIAS_ALERTA:
        return Habilitacion("por-vencer", f"Examen médico vence en {de} días" if de <= da else f"Curso de alturas vence en {da} días", de, da)
    return Habilitacion("habilitado", "Examen y curso de alturas vigentes", de, da)
