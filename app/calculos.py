"""Control de personal, liquidación (jornal y destajo) y finanzas de obra. Portado del prototipo.

Regla "legal" (art. 173 CST): se reconoce el domingo si la persona asistió a todos los días laborables de la semana;
los festivos no son laborables y las ausencias justificadas no hacen perder el domingo.
Regla "literal": domingo si asistió más de 5 días en la semana.
Destajo (art. 132 CST): cantidad por persona × tarifa; domingos y festivos con el promedio diario (art. 176) y
garantía del salario mínimo proporcional.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .festivos import dow, es_festivo
from .reglas import MOTIVOS_JUSTIFICADOS, resumen_obra

SMMLV_DEF = 1_750_905.0
AUX_DEF = 249_095.0
PCT_SALUD = 0.04
PCT_PENSION = 0.04


@dataclass
class Cfg:
    semana: str = "LS"                    # LS lunes-sábado | LV lunes-viernes
    regla_dominical: str = "legal"        # legal | literal
    festivos_extra: frozenset = field(default_factory=frozenset)
    modalidad_pago: str = "jornal"        # jornal | destajo
    jornal_base: float = 58_364.0
    factor_prestacional: float = 1.0
    smmlv: float = SMMLV_DEF
    aux_transporte: float = AUX_DEF

    @property
    def jornal_minimo(self) -> float:
        return round(self.smmlv / 30)


def _rango(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _laborable(d: date, cfg: Cfg) -> bool:
    s = dow(d)
    if s == 0 or (cfg.semana == "LV" and s == 6):
        return False
    return es_festivo(d, cfg.festivos_extra) is None


def calcular_periodo(desde: date, fin_periodo: date, asistencias, asignaciones, cfg: Cfg, hasta: date) -> dict:
    """asistencias/asignaciones: solo de UN trabajador."""
    fin = min(fin_periodo, hasta)
    vinculado = lambda f: any(a.fecha_ingreso <= f and (not a.fecha_retiro or f <= a.fecha_retiro) for a in asignaciones)  # noqa: E731
    por_dia: dict[date, list] = {}
    for a in asistencias:
        por_dia.setdefault(a.fecha, []).append(a)
    asistio = lambda f: any(a.asistio for a in por_dia.get(f, []))  # noqa: E731
    justificada = lambda f: (not asistio(f)) and any((not a.asistio) and a.motivo in MOTIVOS_JUSTIFICADOS for a in por_dia.get(f, []))  # noqa: E731

    dias = list(_rango(desde, fin)) if desde <= fin else []
    dias_asistidos = sum(1 for f in dias if asistio(f))
    aus_j = sum(1 for f in dias if _laborable(f, cfg) and vinculado(f) and justificada(f))
    aus_i = sum(1 for f in dias if _laborable(f, cfg) and vinculado(f) and not asistio(f) and not justificada(f) and f in por_dia)
    festivos = sum(1 for f in dias if es_festivo(f, cfg.festivos_extra) and vinculado(f))

    semanas = []
    for dom in (f for f in dias if dow(f) == 0):
        semana = list(_rango(dom - timedelta(days=6), dom - timedelta(days=1)))
        laborables = [f for f in semana if _laborable(f, cfg) and vinculado(f)]
        fest = [f for f in semana if es_festivo(f, cfg.festivos_extra)]
        asistidos = sum(1 for f in semana if asistio(f))
        justificados = sum(1 for f in laborables if justificada(f))
        faltas = [f for f in laborables if not asistio(f) and not justificada(f)]
        if not vinculado(dom) and not laborables:
            rec, razon = False, "Sin vinculación a obra esa semana"
        elif cfg.regla_dominical == "literal":
            rec = asistidos > 5
            razon = f"Asistió {asistidos} días (> 5)" if rec else f"Asistió {asistidos} días (se requieren más de 5)"
        elif not laborables:
            rec, razon = False, "Sin días laborables en la semana"
        else:
            rec = not faltas
            razon = (f"Cumplió los {len(laborables)} días laborables" + (f" (semana con {len(fest)} festivo)" if fest else "")
                     + (f", {justificados} ausencia(s) justificada(s)" if justificados else "")) if rec else f"Faltó {len(faltas)} día(s) sin justificar"
        semanas.append({"domingo": dom, "laborables": len(laborables), "asistidos": asistidos, "justificados": justificados,
                        "faltas": faltas, "festivos": [{"fecha": f, "nombre": es_festivo(f, cfg.festivos_extra)} for f in fest], "reconocido": rec, "razon": razon})
    domingos = sum(1 for s in semanas if s["reconocido"])
    total = dias_asistidos + domingos + (festivos if cfg.regla_dominical == "legal" else 0)
    return {"dias_asistidos": dias_asistidos, "domingos": domingos, "festivos": festivos, "ausencias_justificadas": aus_j,
            "ausencias_injustificadas": aus_i, "total": total, "semanas": semanas}


def asignacion_en(asignaciones, obra_id: str, f: date):
    """La asignación de esa obra vigente ese día."""
    for a in asignaciones:
        if a.obra_id == obra_id and a.fecha_ingreso <= f and (not a.fecha_retiro or f <= a.fecha_retiro):
            return a
    return None


def modalidad_en(asignaciones, obra_id: str, f: date, cfg: Cfg) -> str:
    """Cómo se le paga en esa obra ese día: lo pactado al asignarlo o, si no se fijó, lo de la empresa."""
    a = asignacion_en(asignaciones, obra_id, f)
    return (getattr(a, "modalidad", "") if a else "") or cfg.modalidad_pago


def jornal_en(t, asignaciones, obra_id: str, f: date, cfg: Cfg) -> float:
    """Jornal del día: el pactado en esa obra, si no el del trabajador y si no el de la empresa."""
    a = asignacion_en(asignaciones, obra_id, f)
    if a is not None and getattr(a, "jornal", None) is not None:
        return a.jornal
    return t.jornal if t.jornal is not None else cfg.jornal_base


def liquidar(t, desde: date, hasta: date, corte: date, asistencias, asignaciones, registros, actividades: dict, cfg: Cfg) -> dict:
    """t: Trabajador; registros: RegistroBitacora del periodo; actividades: id → Actividad (con .tarifa y .obra_id).

    La forma de pago se toma de la asignación a cada obra: los días en obras por jornal se pagan por día y lo
    ejecutado en obras por destajo, por cantidad. Un mismo periodo puede mezclar las dos.
    """
    dias = calcular_periodo(desde, hasta, asistencias, asignaciones, cfg, corte)
    dias_descanso = dias["domingos"] + (dias["festivos"] if cfg.regla_dominical == "legal" else 0)
    jornal = t.jornal if t.jornal is not None else cfg.jornal_base
    tope = min(hasta, corte)

    # Día a día: dónde asistió y cómo se le paga allí
    presencia: dict[date, list] = {}
    for a in asistencias:
        if a.asistio and desde <= a.fecha <= tope:
            presencia.setdefault(a.fecha, []).append(a)
    dias_jornal = dias_destajo = 0
    basico_jornal = 0.0
    for f, marcas in sorted(presencia.items()):
        obra_id = next((m.obra_id for m in marcas if modalidad_en(asignaciones, m.obra_id, f, cfg) == "jornal"), marcas[0].obra_id)
        if modalidad_en(asignaciones, obra_id, f, cfg) == "jornal":
            dias_jornal += 1
            basico_jornal += jornal_en(t, asignaciones, obra_id, f, cfg)
        else:
            dias_destajo += 1

    detalle, sin_tarifa = [], 0
    for r in registros:
        if r.fecha < desde or r.fecha > hasta or r.fecha > corte or t.id not in (r.trabajadores or []):
            continue
        a = actividades.get(r.actividad_id)
        if not a:
            continue
        tarifa = a.tarifa or 0
        pagado = modalidad_en(asignaciones, r.obra_id, r.fecha, cfg) == "destajo"
        if pagado and not tarifa:
            sin_tarifa += 1
        medido = t.id in (r.por_trabajador or {})
        parte = r.por_trabajador[t.id] if medido else r.cantidad / (len(r.trabajadores) or 1)
        detalle.append({"fecha": r.fecha, "obra_id": r.obra_id, "actividad": a.nombre, "ubicacion": a.ubicacion, "um": a.um, "cantidad_total": r.cantidad,
                        "integrantes": len(r.trabajadores), "parte": parte, "medido": medido, "tarifa": tarifa, "valor": parte * tarifa, "pagado": pagado})
    detalle.sort(key=lambda x: x["fecha"])

    basico = basico_jornal + sum(d["valor"] for d in detalle if d["pagado"])
    promedio = basico / dias["dias_asistidos"] if dias["dias_asistidos"] else jornal
    descansos = dias_descanso * promedio
    hay_destajo = dias_destajo > 0 or any(d["pagado"] for d in detalle)
    # Con pago por ejecutado se garantiza el mínimo proporcional (art. 132 CST)
    ajuste_minimo = max(0.0, (dias["dias_asistidos"] + dias_descanso) * cfg.jornal_minimo - (basico + descansos)) if hay_destajo else 0.0
    devengado = basico + descansos + ajuste_minimo
    mensual_eq = max(promedio, cfg.jornal_minimo) * 30
    auxilio = (dias["dias_asistidos"] + dias_descanso) * (cfg.aux_transporte / 30) if mensual_eq <= 2 * cfg.smmlv else 0.0
    salud, pension = devengado * PCT_SALUD, devengado * PCT_PENSION
    modalidad = "mixta" if dias_jornal and hay_destajo else "destajo" if hay_destajo else "jornal"
    return {"trabajador_id": t.id, "nombre": t.nombre, "cedula": t.cedula, "cargo": t.cargo, "modalidad": modalidad, "jornal": jornal,
            "dias_jornal": dias_jornal, "dias_destajo": dias_destajo, "basico_jornal": basico_jornal,
            "dias": dias, "detalle": detalle, "sin_tarifa": sin_tarifa, "basico": basico, "descansos": descansos, "dias_descanso": dias_descanso,
            "promedio_diario": promedio, "ajuste_minimo": ajuste_minimo, "devengado": devengado, "auxilio": auxilio, "salud": salud, "pension": pension,
            "neto": devengado + auxilio - salud - pension}


def valor_unitario_efectivo(a, padre=None) -> float:
    """Lo que el cliente paga por cada unidad de esta actividad. En un subítem se deriva de lo que representa del ítem."""
    if padre is None:
        return a.valor_unitario
    if not getattr(a, "aporta_avance", True) or not a.cantidad:
        return 0.0
    return (getattr(a, "equivale", 0.0) or 0.0) * padre.valor_unitario / a.cantidad


def costo_por_actividad(actividades, registros, trabajadores: dict, desde: date, hasta: date, corte: date, cfg: Cfg) -> list[dict]:
    """Costo de mano de obra e ingreso de cada actividad con avance. Los subítems se reportan aparte de su ítem."""
    padres = {a.id: a for a in actividades if not getattr(a, "padre_id", "")}
    out = []
    for a in actividades:
        regs = [r for r in registros if r.actividad_id == a.id and desde <= r.fecha <= hasta and r.fecha <= corte]
        cantidad = sum(r.cantidad for r in regs)
        if cantidad <= 0:
            continue
        dias_persona = sum(len(r.trabajadores) if r.trabajadores else r.personas for r in regs)
        if cfg.modalidad_pago == "destajo":
            base = cantidad * (a.tarifa or 0)
        else:
            base = 0.0
            for r in regs:
                if r.trabajadores:
                    base += sum((trabajadores[i].jornal if i in trabajadores and trabajadores[i].jornal is not None else cfg.jornal_base) for i in r.trabajadores)
                else:
                    base += r.personas * cfg.jornal_base
        costo = base * cfg.factor_prestacional
        padre = padres.get(getattr(a, "padre_id", "") or "")
        vu = valor_unitario_efectivo(a, padre)
        ingreso = cantidad * vu
        out.append({"actividad_id": a.id, "nombre": a.nombre, "ubicacion": a.ubicacion, "um": a.um, "cantidad": cantidad, "ingreso": ingreso, "costo": costo,
                    "dias_persona": dias_persona, "margen": ingreso - costo, "costo_unitario": costo / cantidad, "valor_unitario": vu, "tarifa": a.tarifa,
                    "padre_id": getattr(a, "padre_id", "") or "", "padre": padre.nombre if padre else ""})
    return out


def resumen_financiero(obra, contrato, actividades, registros, facturas, gastos, hoy: date) -> dict:
    ro = resumen_obra(contrato, actividades, registros, hoy)
    ingresos = obra.ingresos_proyectados if obra.ingresos_proyectados is not None else (contrato.valor_total if contrato else 0)
    facturado = sum(f.valor for f in facturas)
    g = sum(x.valor for x in gastos)
    confiable = ro["avance_fisico"] >= 0.1
    gasto_proy = g / ro["avance_fisico"] if confiable else g
    util_proy = ingresos - gasto_proy
    ratio = (contrato.rete_garantia / contrato.valor_total) if contrato and contrato.valor_total else 0
    return {"ingresos_proyectados": ingresos, "facturado": facturado, "por_facturar": max(0.0, ro["valor_ejecutado"] - facturado), "gastos": g,
            "gasto_proyectado": gasto_proy, "utilidad_actual": facturado - g, "utilidad_proyectada": util_proy,
            "margen_proyectado": util_proy / ingresos if ingresos else 0, "rete_garantia_retenida": facturado * ratio, "obra_ejecutada": ro["valor_ejecutado"],
            "avance_fisico": ro["avance_fisico"],
            "metodo": (f"Gasto proyectado = gasto a la fecha ÷ avance físico ({ro['avance_fisico'] * 100:.1f} %)" if confiable
                       else "Avance menor al 10 %: aún no se proyecta el gasto, se usa el gasto a la fecha")}
