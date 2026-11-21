"""Presupuesto vs. real, cronograma (ruta crítica) y utilidades de inventario. Funciones puras, sin base de datos."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

CATEGORIAS_PRES = ("Mano de obra", "Materiales e insumos", "Herramientas y equipos", "Transporte", "Subcontratos y cortes", "Otros")
# Categoría de los gastos de Finanzas → categoría del presupuesto
MAPA_GASTOS = {"Compra de insumos": "Materiales e insumos", "Herramientas y equipos": "Herramientas y equipos", "Transporte": "Transporte",
               "Cortes de obra": "Subcontratos y cortes", "Otros": "Otros"}


# ---------------- Presupuesto vs. real ----------------
def presupuesto_vs_real(items, gastos, costo_mo_por_actividad: dict[str, float], avance_fisico: float) -> dict:
    presup = defaultdict(float)
    for it in items:
        presup[it.categoria] += it.valor
    real = defaultdict(float)
    real["Mano de obra"] = sum(costo_mo_por_actividad.values())
    for g in gastos:
        real[MAPA_GASTOS.get(g.categoria, "Otros")] += g.valor
    categorias = []
    for c in CATEGORIAS_PRES:
        p, r = presup.get(c, 0.0), real.get(c, 0.0)
        categorias.append({"categoria": c, "presupuestado": p, "real": r, "desviacion": r - p, "consumido": (r / p) if p else None})
    por_actividad = []
    for it in items:
        if it.categoria == "Mano de obra" and it.actividad_id:
            r = costo_mo_por_actividad.get(it.actividad_id, 0.0)
            por_actividad.append({"actividad_id": it.actividad_id, "presupuestado": it.valor, "real": r, "desviacion": r - it.valor, "consumido": (r / it.valor) if it.valor else None})
    total_p, total_r = sum(presup.values()), sum(real.values())
    valor_ganado = total_p * avance_fisico                       # costo que "debería" llevar según el avance
    cpi = (valor_ganado / total_r) if total_r > 0 else None       # >1: se gasta menos de lo que avanza la obra
    confiable = avance_fisico >= 0.1
    estimado_final = (total_r / avance_fisico) if confiable and avance_fisico > 0 else total_r
    if not total_p:
        semaforo = "sin-presupuesto"
    elif cpi is None or not confiable:
        semaforo = "al-dia" if total_r <= total_p else "sobrecosto"
    else:
        semaforo = "al-dia" if cpi >= 0.95 else "atencion" if cpi >= 0.85 else "sobrecosto"
    return {"categorias": categorias, "por_actividad": por_actividad,
            "resumen": {"presupuesto": total_p, "real": total_r, "desviacion": total_r - total_p, "consumido": (total_r / total_p) if total_p else None,
                        "avance_fisico": avance_fisico, "valor_ganado": valor_ganado, "cpi": cpi, "estimado_final": estimado_final,
                        "desviacion_proyectada": (estimado_final - total_p) if total_p else 0, "confiable": confiable, "semaforo": semaforo}}


# ---------------- Cronograma ----------------
def hay_ciclo(preds: dict[str, list[str]]) -> bool:
    estado: dict[str, int] = {}

    def visita(n: str) -> bool:
        estado[n] = 1
        for p in preds.get(n, []):
            if estado.get(p) == 1 or (p not in estado and visita(p)):
                return True
        estado[n] = 2
        return False

    return any(n not in estado and visita(n) for n in preds)


def ruta_critica(tareas: dict[str, tuple[date, date]], preds: dict[str, list[str]]) -> dict[str, dict]:
    """CPM con fechas planeadas: paso hacia adelante (respeta predecesoras) y hacia atrás. holgura 0 = ruta crítica."""
    dur = {k: (f - i).days + 1 for k, (i, f) in tareas.items()}
    ES: dict[str, date] = {}

    def es(k: str) -> date:
        if k not in ES:
            ini = tareas[k][0]
            for p in preds.get(k, []):
                if p in tareas:
                    ini = max(ini, es(p) + timedelta(days=dur[p]))
            ES[k] = ini
        return ES[k]

    for k in tareas:
        es(k)
    EF = {k: ES[k] + timedelta(days=dur[k] - 1) for k in tareas}
    fin_proyecto = max(EF.values()) if EF else None
    sucesoras: dict[str, list[str]] = defaultdict(list)
    for k, ps in preds.items():
        for p in ps:
            if p in tareas and k in tareas:
                sucesoras[p].append(k)
    LF: dict[str, date] = {}

    def lf(k: str) -> date:
        if k not in LF:
            LF[k] = min((lf(s) - timedelta(days=dur[s]) for s in sucesoras.get(k, [])), default=fin_proyecto)
        return LF[k]

    out = {}
    for k in tareas:
        ls = lf(k) - timedelta(days=dur[k] - 1)
        holgura = (ls - ES[k]).days
        out[k] = {"inicio_calculado": ES[k], "fin_calculado": EF[k], "holgura": holgura, "critica": holgura == 0}
    return out


def estado_tarea(avance: float, inicio: date, fin: date, hoy: date) -> tuple[str, float]:
    dur = (fin - inicio).days + 1
    esperado = min(1.0, max(0.0, ((hoy - inicio).days + 1) / dur))
    if avance >= 0.999:
        return "Terminada", esperado
    if hoy < inicio:
        return "Por iniciar", esperado
    if hoy > fin or esperado - avance > 0.15:
        return "Atrasada", esperado
    return ("En curso" if avance > 0 else "Por iniciar"), esperado


def calcular_cronograma(tareas, actividades: dict, regs, hoy: date) -> dict:
    """tareas: CronogramaTarea; actividades: id → Actividad; regs: RegistroBitacora de la obra."""
    por_act: dict[str, list] = defaultdict(list)
    for r in sorted(regs, key=lambda x: x.fecha):
        por_act[r.actividad_id].append(r)
    planeadas = {t.actividad_id: (t.inicio, t.fin) for t in tareas if t.actividad_id in actividades}
    preds = {t.actividad_id: [p for p in (t.predecesoras or []) if p in planeadas] for t in tareas if t.actividad_id in planeadas}
    cpm = ruta_critica(planeadas, preds) if planeadas else {}
    filas = []
    for t in tareas:
        a = actividades.get(t.actividad_id)
        if not a:
            continue
        rs = por_act.get(a.id, [])
        acumulado, inicio_real, fin_real = 0.0, (rs[0].fecha if rs else None), None
        for r in rs:
            acumulado += r.cantidad
            if fin_real is None and acumulado >= a.cantidad - 1e-9:
                fin_real = r.fecha
        avance = min(1.0, acumulado / a.cantidad) if a.cantidad else 0.0
        estado, esperado = estado_tarea(avance, t.inicio, t.fin, hoy)
        fin_proy = None
        if avance >= 0.999:
            fin_proy = fin_real
        elif inicio_real and avance > 0:
            fin_proy = inicio_real + timedelta(days=round(((hoy - inicio_real).days + 1) / avance) - 1)
        elif hoy > t.inicio:
            fin_proy = t.fin + timedelta(days=(hoy - t.inicio).days)  # no ha empezado: se corre lo que ya se retrasó
        atraso = max(0, (fin_proy - t.fin).days) if fin_proy and avance < 0.999 else 0
        c = cpm.get(a.id, {})
        filas.append({"id": t.id, "actividad_id": a.id, "nombre": a.nombre, "ubicacion": a.ubicacion, "um": a.um, "cantidad": a.cantidad, "grupo": a.grupo,
                      "inicio": t.inicio, "fin": t.fin, "predecesoras": t.predecesoras or [], "notas": t.notas, "avance": avance, "esperado": esperado,
                      "estado": estado, "inicio_real": inicio_real, "fin_real": fin_real, "fin_proyectado": fin_proy, "atraso_dias": atraso,
                      "critica": c.get("critica", False), "holgura": c.get("holgura"), "inicio_calculado": c.get("inicio_calculado"), "fin_calculado": c.get("fin_calculado")})
    filas.sort(key=lambda f: (f["inicio"], f["grupo"], f["nombre"]))
    fin_plan = max((f["fin"] for f in filas), default=None)  # lo que programó el usuario
    # el fin proyectado suma los atrasos reales y lo que las predecesoras empujan más allá de las fechas programadas
    fin_proy = max((max(f["fin_proyectado"] or f["fin"], f["fin_calculado"] or f["fin"]) for f in filas), default=None)
    return {"tareas": filas, "inicio": min((f["inicio"] for f in filas), default=None), "fin_plan": fin_plan, "fin_proyectado": fin_proy,
            "atraso_proyecto": max(0, (fin_proy - fin_plan).days) if fin_plan and fin_proy else 0,
            "atrasadas": sum(1 for f in filas if f["estado"] == "Atrasada"), "criticas_atrasadas": sum(1 for f in filas if f["estado"] == "Atrasada" and f["critica"]),
            "terminadas": sum(1 for f in filas if f["estado"] == "Terminada"),
            "sin_programar": [{"actividad_id": a.id, "nombre": a.nombre, "ubicacion": a.ubicacion} for a in actividades.values() if a.id not in planeadas]}


def proponer_cronograma(actividades, inicio: date, fin: date) -> list[dict]:
    """Reparte el plazo del contrato entre los grupos del presupuesto (en orden y en proporción a su valor). Es solo un punto de partida."""
    grupos: dict[int, list] = defaultdict(list)
    for a in actividades:
        grupos[a.grupo].append(a)
    total_dias = max(1, (fin - inicio).days + 1)
    valores = {g: sum(a.valor_total for a in acts) or 1.0 for g, acts in grupos.items()}
    suma = sum(valores.values())
    out, cursor = [], 0.0
    ultimo = None
    for g in sorted(grupos):
        dias = max(1.0, total_dias * valores[g] / suma)
        ini = inicio + timedelta(days=int(round(cursor)))
        fn = min(fin, inicio + timedelta(days=int(round(cursor + dias)) - 1))
        fn = max(fn, ini)
        for a in grupos[g]:
            out.append({"actividad_id": a.id, "inicio": ini, "fin": fn, "predecesoras": [], "notas": ""})
        cursor += dias
        ultimo = g
    return out
