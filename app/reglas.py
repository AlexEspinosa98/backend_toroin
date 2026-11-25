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


# ---------------- Avance de obra ----------------
def ejecutado_por_actividad(registros) -> dict[str, float]:
    m: dict[str, float] = {}
    for r in registros:
        m[r.actividad_id] = m.get(r.actividad_id, 0.0) + r.cantidad
    return m


def subitems_por_padre(actividades) -> dict[str, list]:
    """Subítems agrupados por el ítem del contrato al que pertenecen."""
    m: dict[str, list] = {}
    for a in actividades:
        if getattr(a, "padre_id", ""):
            m.setdefault(a.padre_id, []).append(a)
    return m


def aporte_al_padre(sub, ejecutado: float) -> float:
    """Cuánto de la unidad del ítem del contrato representa lo ejecutado en un subítem."""
    if not getattr(sub, "aporta_avance", True) or not sub.cantidad:
        return 0.0
    return (ejecutado / sub.cantidad) * (getattr(sub, "equivale", 0.0) or 0.0)


def ejecutado_de_items(actividades, registros) -> dict[str, float]:
    """Ejecutado de cada ítem del contrato, en su propia unidad: lo registrado directo más lo que aportan sus subítems."""
    bruto = ejecutado_por_actividad(registros)
    hijos = subitems_por_padre(actividades)
    out: dict[str, float] = {}
    for a in actividades:
        if getattr(a, "padre_id", ""):
            continue
        out[a.id] = bruto.get(a.id, 0.0) + sum(aporte_al_padre(h, bruto.get(h.id, 0.0)) for h in hijos.get(a.id, []))
    return out


def resumen_obra(contrato, actividades, registros, fecha: date) -> dict:
    """Avance físico (valorizado), avance en tiempo, índice y semáforo. Los subítems suman a su ítem, no aparte."""
    if not contrato:
        return {"semaforo": "sin-contrato", "valor_contrato": 0, "valor_ejecutado": 0, "avance_fisico": 0, "avance_tiempo": 0, "dias_restantes": 0, "indice": 0}
    items = [a for a in actividades if not getattr(a, "padre_id", "")]
    ejec = ejecutado_de_items(actividades, registros)
    valor_contrato = sum(a.valor_total for a in items)
    valor_ejec = sum(min(ejec.get(a.id, 0.0), a.cantidad) * a.valor_unitario for a in items)
    plazo = max(1, (contrato.fecha_fin - contrato.fecha_inicio).days)
    transcurrido = min(max(0, (fecha - contrato.fecha_inicio).days), plazo)
    fisico = valor_ejec / valor_contrato if valor_contrato else 0
    tiempo = transcurrido / plazo
    indice = fisico / tiempo if tiempo > 0 else 1
    semaforo = "al-dia" if tiempo == 0 or indice >= 0.95 else "atencion" if indice >= 0.85 else "atrasada"
    return {"semaforo": semaforo, "valor_contrato": valor_contrato, "valor_ejecutado": valor_ejec, "avance_fisico": fisico, "avance_tiempo": tiempo,
            "dias_restantes": (contrato.fecha_fin - fecha).days, "indice": indice}


def tarea_vigente(tareas, trabajador_id: str, fecha: date):
    """La de `desde` más reciente (y, a igual fecha, la más nueva). Sin actividad = sin tarea."""
    mejor = None
    for t in tareas:
        if t.trabajador_id != trabajador_id or t.desde > fecha or (t.hasta and fecha > t.hasta):
            continue
        if mejor is None or t.desde > mejor.desde or (t.desde == mejor.desde and t.creado > mejor.creado):
            mejor = t
    return mejor if mejor and mejor.actividad_id else None


MOTIVOS_ATRASO = ("Material", "Herramientas", "Transporte", "Personal", "Clima", "Otros")
