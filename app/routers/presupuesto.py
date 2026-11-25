"""Presupuesto de costos por obra y comparación contra lo real (mano de obra de la bitácora + gastos de Finanzas)."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from ..audit import registrar
from ..calculos import costo_por_actividad
from ..calculos_extra import CATEGORIAS_PRES, presupuesto_vs_real
from ..deps import Ctx, exigir_modulo
from ..models import Actividad, Asignacion, Contrato, Gasto, Obra, PresupuestoItem, RegistroBitacora, Trabajador
from ..reglas import resumen_obra
from ..schemas import PresupuestoIn
from .contrato import obra_de
from .nomina import cargar_cfg

router = APIRouter(tags=["presupuesto"])
ROLES = ("admin", "gerencia")


def datos_presupuesto(ctx: Ctx, obra: Obra, hoy: date | None = None) -> dict:
    hoy = hoy or date.today()
    e, db = ctx.empresa_id, ctx.db
    items = list(db.scalars(select(PresupuestoItem).where(PresupuestoItem.obra_id == obra.id, PresupuestoItem.empresa_id == e).order_by(PresupuestoItem.categoria, PresupuestoItem.concepto)))
    acts = list(db.scalars(select(Actividad).where(Actividad.obra_id == obra.id, Actividad.empresa_id == e).order_by(Actividad.grupo, Actividad.nombre)))
    regs = list(db.scalars(select(RegistroBitacora).where(RegistroBitacora.obra_id == obra.id, RegistroBitacora.empresa_id == e)))
    gastos = list(db.scalars(select(Gasto).where(Gasto.obra_id == obra.id, Gasto.empresa_id == e)))
    contrato = db.scalar(select(Contrato).where(Contrato.obra_id == obra.id, Contrato.empresa_id == e))
    ids = {t for r in regs for t in (r.trabajadores or [])}
    ts = {t.id: t for t in db.scalars(select(Trabajador).where(Trabajador.empresa_id == e, Trabajador.id.in_(ids)))} if ids else {}
    cfg = cargar_cfg(ctx, hoy.year)
    asig: dict[str, list] = {}
    for x in db.scalars(select(Asignacion).where(Asignacion.empresa_id == e, Asignacion.obra_id == obra.id)):
        asig.setdefault(x.trabajador_id, []).append(x)
    costos = costo_por_actividad(acts, regs, ts, date(2000, 1, 1), hoy, hoy, cfg, asig)
    mo = {c["actividad_id"]: c["costo"] for c in costos}
    avance = resumen_obra(contrato, acts, regs, hoy)["avance_fisico"]
    r = presupuesto_vs_real(items, gastos, mo, avance)
    nombres = {a.id: (f"{a.nombre} · {a.ubicacion}" if a.ubicacion else a.nombre) for a in acts}
    for x in r["por_actividad"]:
        x["actividad"] = nombres.get(x["actividad_id"], "(actividad eliminada)")
    return {"obra": {"id": obra.id, "nombre": obra.nombre, "ciudad": obra.ciudad}, "tiene_contrato": contrato is not None,
            "items": [{"id": i.id, "categoria": i.categoria, "concepto": i.concepto, "valor": i.valor, "actividad_id": i.actividad_id} for i in items],
            "actividades": [{"id": a.id, "nombre": nombres[a.id]} for a in acts], "categorias_pres": CATEGORIAS_PRES, **r}


@router.get("/presupuesto")
def general(ctx: Ctx = Depends(exigir_modulo("presupuesto"))):
    out = []
    for o in ctx.db.scalars(select(Obra).where(Obra.empresa_id == ctx.empresa_id).order_by(Obra.nombre)):
        d = datos_presupuesto(ctx, o)
        out.append({"obra": d["obra"], "resumen": d["resumen"]})
    return out


@router.get("/obras/{obra_id}/presupuesto")
def de_la_obra(obra_id: str, ctx: Ctx = Depends(exigir_modulo("presupuesto"))):
    return datos_presupuesto(ctx, obra_de(ctx, obra_id))


@router.put("/obras/{obra_id}/presupuesto")
def guardar(obra_id: str, datos: PresupuestoIn, request: Request, ctx: Ctx = Depends(exigir_modulo("presupuesto", *ROLES))):
    obra = obra_de(ctx, obra_id)
    validas = {a for a in ctx.db.scalars(select(Actividad.id).where(Actividad.obra_id == obra.id, Actividad.empresa_id == ctx.empresa_id))}
    for it in datos.items:
        if it.actividad_id and it.actividad_id not in validas:
            raise HTTPException(422, "Una actividad del presupuesto no pertenece a esta obra")
        if it.actividad_id and it.categoria != "Mano de obra":
            raise HTTPException(422, "Solo la mano de obra se puede ligar a una actividad")
    for viejo in ctx.db.scalars(select(PresupuestoItem).where(PresupuestoItem.obra_id == obra.id, PresupuestoItem.empresa_id == ctx.empresa_id)):
        ctx.db.delete(viejo)
    for it in datos.items:
        ctx.db.add(PresupuestoItem(empresa_id=ctx.empresa_id, obra_id=obra.id, **it.model_dump()))
    registrar(ctx.db, accion="presupuesto.guardar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=obra.id,
              detalle={"items": len(datos.items), "total": sum(i.valor for i in datos.items)}, request=request)
    ctx.db.commit()
    return datos_presupuesto(ctx, obra)
