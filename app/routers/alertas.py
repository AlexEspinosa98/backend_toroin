"""Alertas para el administrador de la empresa: lo que requiere atención hoy (se calcula al consultar)."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select

from .. import catalog
from ..calculos import resumen_financiero
from ..deps import Ctx, contexto, modulos_de_usuario
from ..festivos import dow, es_festivo
from ..models import (Actividad, Asignacion, Asistencia, Contrato, Factura, Gasto, Obra, OrdenCompra, RegistroBitacora, Requisicion, SolicitudEPP, Trabajador)
from ..reglas import habilitacion, resumen_obra
from .compras import _totales
from .cronograma import datos_cronograma
from .inventario import _materiales
from .nomina import cargar_cfg
from .presupuesto import datos_presupuesto

router = APIRouter(tags=["alertas"])
ORDEN = {"alta": 0, "media": 1, "baja": 2}


def _ve(ctx: Ctx, modulo: str) -> bool:
    """El usuario ve el módulo y no ha desactivado sus alertas (el admin elige de cuáles módulos recibe)."""
    if modulo not in modulos_de_usuario(ctx.usuario, ctx.est):
        return False
    return ctx.usuario.alertas is None or modulo in ctx.usuario.alertas


def calcular_alertas(ctx: Ctx, hoy: date) -> list[dict]:
    e, db, out = ctx.empresa_id, ctx.db, []

    def add(tipo, sev, titulo, detalle, href):
        out.append({"tipo": tipo, "severidad": sev, "titulo": titulo, "detalle": detalle, "href": href})

    lic = ctx.est
    quiere_lic = ctx.usuario.rol in ("admin", "super_admin") if ctx.usuario.alertas is None else "licencia" in ctx.usuario.alertas
    if quiere_lic and lic.estado in ("por_vencer", "gracia", "vencida", "prueba") and lic.mensaje:
        add("licencia", "alta" if lic.estado in ("gracia", "vencida") else "media", "Licencia", lic.mensaje, "/licencia")

    obras = {o.id: o for o in db.scalars(select(Obra).where(Obra.empresa_id == e, Obra.estado == "activa"))}
    asig = list(db.scalars(select(Asignacion).where(Asignacion.empresa_id == e, Asignacion.fecha_retiro.is_(None))))
    por_obra: dict[str, list[str]] = {}
    for a in asig:
        if a.obra_id in obras:
            por_obra.setdefault(a.obra_id, []).append(a.trabajador_id)

    if _ve(ctx, "personal") or _ve(ctx, "asistencia"):
        ids = {t for v in por_obra.values() for t in v}
        ts = list(db.scalars(select(Trabajador).where(Trabajador.empresa_id == e, Trabajador.id.in_(ids)))) if ids else []
        venc, prox, sin = [], [], []
        for t in ts:
            h = habilitacion(t, hoy)
            (venc if h.estado == "vencido" else prox if h.estado == "por-vencer" else sin if h.estado == "sin-datos" else []).append((t, h))
        if venc:
            add("sst", "alta", f"{len(venc)} persona(s) no habilitada(s) en obra", "; ".join(f"{t.nombre}: {h.motivo}" for t, h in venc[:4]) + ("…" if len(venc) > 4 else ""), "/personal")
        if prox:
            add("sst", "media", f"{len(prox)} habilitación(es) por vencer", "; ".join(f"{t.nombre}: {h.motivo}" for t, h in prox[:4]) + ("…" if len(prox) > 4 else ""), "/personal")
        if sin:
            add("sst", "baja", f"{len(sin)} persona(s) sin fechas de examen o alturas", ", ".join(t.nombre for t, _ in sin[:5]) + ("…" if len(sin) > 5 else ""), "/personal")

    if _ve(ctx, "contrato"):
        for c in db.scalars(select(Contrato).where(Contrato.empresa_id == e)):
            o = obras.get(c.obra_id)
            if not o:
                continue
            acts = list(db.scalars(select(Actividad).where(Actividad.obra_id == o.id)))
            regs = list(db.scalars(select(RegistroBitacora).where(RegistroBitacora.obra_id == o.id)))
            r = resumen_obra(c, acts, regs, hoy)
            if r["semaforo"] == "atrasada":
                add("obra", "alta", f"Obra atrasada: {o.nombre}", f"Avance físico {r['avance_fisico'] * 100:.0f} % frente a {r['avance_tiempo'] * 100:.0f} % del tiempo", f"/obras/{o.id}")
            elif r["semaforo"] == "atencion":
                add("obra", "media", f"Obra en atención: {o.nombre}", f"Avance físico {r['avance_fisico'] * 100:.0f} % frente a {r['avance_tiempo'] * 100:.0f} % del tiempo", f"/obras/{o.id}")
            if 0 <= r["dias_restantes"] <= 30 and r["avance_fisico"] < 0.95:
                add("obra", "media", f"Contrato por terminar: {o.nombre}", f"Faltan {r['dias_restantes']} días y el avance es {r['avance_fisico'] * 100:.0f} %", f"/obras/{o.id}")
        for o in obras.values():
            if por_obra.get(o.id) and not db.scalar(select(Contrato.id).where(Contrato.obra_id == o.id).limit(1)):
                add("obra", "baja", f"Sin contrato cargado: {o.nombre}", "Cargue el contrato para ver el avance", f"/obras/{o.id}/contrato")

    if _ve(ctx, "asistencia"):
        cfg = cargar_cfg(ctx)
        ayer = hoy - timedelta(days=1)
        while dow(ayer) == 0 or (cfg.semana == "LV" and dow(ayer) == 6) or es_festivo(ayer, cfg.festivos_extra):
            ayer -= timedelta(days=1)
        for oid, tids in por_obra.items():
            if not db.scalar(select(Asistencia.id).where(Asistencia.obra_id == oid, Asistencia.fecha == ayer).limit(1)):
                add("asistencia", "media", f"Asistencia sin registrar: {obras[oid].nombre}", f"No hay registro del {ayer.strftime('%d/%m/%Y')} ({len(tids)} asignados)", "/asistencia")

    if _ve(ctx, "epp"):
        pend = list(db.scalars(select(SolicitudEPP).where(SolicitudEPP.empresa_id == e, SolicitudEPP.estado.in_(("Solicitado", "Aprobado")))))
        viejas = [s for s in pend if (hoy - s.fecha_solicitud).days >= 3]
        if viejas:
            add("epp", "media", f"{len(viejas)} solicitud(es) de EPP sin atender", "Llevan 3 días o más sin entregarse", "/epp")
        elif pend:
            add("epp", "baja", f"{len(pend)} solicitud(es) de EPP pendientes", "Por aprobar o entregar", "/epp")

    if _ve(ctx, "pagos") and cargar_cfg(ctx).modalidad_pago == "destajo":
        sin = {a.id: a for a in db.scalars(select(Actividad).where(Actividad.empresa_id == e, (Actividad.tarifa.is_(None)) | (Actividad.tarifa == 0)))}
        usadas = {r.actividad_id for r in db.scalars(select(RegistroBitacora).where(RegistroBitacora.empresa_id == e, RegistroBitacora.fecha >= hoy - timedelta(days=30)))}
        n = len([i for i in sin if i in usadas])
        if n:
            add("pagos", "alta", f"{n} actividad(es) con avance y sin tarifa de destajo", "No se pagarán hasta definir la tarifa", "/pagos")

    if _ve(ctx, "finanzas"):
        for o in obras.values():
            c = db.scalar(select(Contrato).where(Contrato.obra_id == o.id))
            if not c:
                continue
            acts = list(db.scalars(select(Actividad).where(Actividad.obra_id == o.id)))
            regs = list(db.scalars(select(RegistroBitacora).where(RegistroBitacora.obra_id == o.id)))
            fac = list(db.scalars(select(Factura).where(Factura.obra_id == o.id)))
            gas = list(db.scalars(select(Gasto).where(Gasto.obra_id == o.id)))
            if not gas:
                continue
            r = resumen_financiero(o, c, acts, regs, fac, gas, hoy)
            if r["utilidad_proyectada"] < 0:
                add("finanzas", "alta", f"Pérdida proyectada: {o.nombre}", f"Utilidad proyectada {r['utilidad_proyectada']:,.0f} COP", f"/finanzas")
            elif r["margen_proyectado"] < 0.05:
                add("finanzas", "media", f"Margen bajo: {o.nombre}", f"Margen proyectado {r['margen_proyectado'] * 100:.1f} %", "/finanzas")

    if _ve(ctx, "presupuesto"):
        for o in obras.values():
            d = datos_presupuesto(ctx, o, hoy)["resumen"]
            if not d["presupuesto"]:
                continue
            if d["real"] > d["presupuesto"]:
                add("presupuesto", "alta", f"Presupuesto superado: {o.nombre}", f"Se ha gastado {d['real']:,.0f} de {d['presupuesto']:,.0f} COP", "/presupuesto")
            elif d["semaforo"] == "sobrecosto":
                add("presupuesto", "alta", f"Sobrecosto proyectado: {o.nombre}", f"Índice de costo {d['cpi']:.2f}: se gasta más de lo que avanza la obra" if d["cpi"] else "Gasto por encima del avance", "/presupuesto")
            elif d["semaforo"] == "atencion":
                add("presupuesto", "media", f"Costos en atención: {o.nombre}", f"Índice de costo {d['cpi']:.2f}", "/presupuesto")

    if _ve(ctx, "cronograma"):
        for o in obras.values():
            d = datos_cronograma(ctx, o, hoy)
            if not d["tareas"]:
                continue
            if d["criticas_atrasadas"]:
                add("cronograma", "alta", f"Ruta crítica atrasada: {o.nombre}", f"{d['criticas_atrasadas']} actividad(es) críticas atrasadas; el fin proyectado se corre {d['atraso_proyecto']} días", "/cronograma")
            elif d["atrasadas"]:
                add("cronograma", "media", f"Actividades atrasadas: {o.nombre}", f"{d['atrasadas']} actividad(es) fuera de programa", "/cronograma")
            if d["contrato"] and d["fin_proyectado"] and d["fin_proyectado"] > d["contrato"]["fin"]:
                add("cronograma", "alta", f"Terminaría después del contrato: {o.nombre}", f"Fin proyectado {d['fin_proyectado']:%d/%m/%Y} frente a {d['contrato']['fin']:%d/%m/%Y}", "/cronograma")

    if _ve(ctx, "inventario"):
        bajos = [m for m in _materiales(ctx, None) if m["activo"] and m["bajo_minimo"]]
        if bajos:
            add("inventario", "media", f"{len(bajos)} material(es) bajo el mínimo", ", ".join(m["nombre"] for m in bajos[:5]) + ("…" if len(bajos) > 5 else ""), "/inventario")

    if _ve(ctx, "compras"):
        reqs = list(db.scalars(select(Requisicion).where(Requisicion.empresa_id == e, Requisicion.estado.in_(("Solicitada", "Aprobada")))))
        sin_resolver = [r for r in reqs if r.estado == "Solicitada" and (hoy - r.fecha).days >= 2]
        por_ordenar = [r for r in reqs if r.estado == "Aprobada"]
        if sin_resolver:
            add("compras", "media", f"{len(sin_resolver)} requisición(es) sin resolver", "Llevan 2 días o más esperando aprobación", "/compras")
        if por_ordenar:
            add("compras", "baja", f"{len(por_ordenar)} requisición(es) aprobadas sin orden de compra", "Falta generar la orden al proveedor", "/compras")
        vencidas = [o for o in db.scalars(select(OrdenCompra).where(OrdenCompra.empresa_id == e, OrdenCompra.estado.in_(("Emitida", "Parcial")))) if o.fecha_entrega and o.fecha_entrega < hoy]
        if vencidas:
            add("compras", "alta", f"{len(vencidas)} orden(es) de compra con entrega vencida", ", ".join(f"OC-{o.numero:04d}" for o in vencidas[:5]), "/compras")

    out.sort(key=lambda a: ORDEN[a["severidad"]])
    return out


@router.get("/alertas")
def alertas(ctx: Ctx = Depends(contexto)):
    lista = calcular_alertas(ctx, date.today())
    return {"total": len(lista), "altas": sum(1 for a in lista if a["severidad"] == "alta"), "alertas": lista}
