"""Cronograma de obra: programación por actividad, avance real, ruta crítica y atrasos."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from ..audit import registrar
from ..calculos_extra import calcular_cronograma, hay_ciclo, proponer_cronograma
from ..deps import Ctx, exigir_modulo
from ..models import Actividad, Contrato, CronogramaTarea, Obra, RegistroBitacora
from ..schemas import CronogramaIn
from .contrato import obra_de

router = APIRouter(tags=["cronograma"])
ROLES = ("admin", "gerencia", "residente")


def datos_cronograma(ctx: Ctx, obra: Obra, hoy: date | None = None) -> dict:
    e, db = ctx.empresa_id, ctx.db
    acts = {a.id: a for a in db.scalars(select(Actividad).where(Actividad.obra_id == obra.id, Actividad.empresa_id == e))}
    tareas = list(db.scalars(select(CronogramaTarea).where(CronogramaTarea.obra_id == obra.id, CronogramaTarea.empresa_id == e)))
    regs = list(db.scalars(select(RegistroBitacora).where(RegistroBitacora.obra_id == obra.id, RegistroBitacora.empresa_id == e)))
    c = db.scalar(select(Contrato).where(Contrato.obra_id == obra.id, Contrato.empresa_id == e))
    r = calcular_cronograma(tareas, acts, regs, hoy or date.today())
    return {"obra": {"id": obra.id, "nombre": obra.nombre, "ciudad": obra.ciudad}, "contrato": {"inicio": c.fecha_inicio, "fin": c.fecha_fin} if c else None,
            "hoy": hoy or date.today(), **r}


@router.get("/cronograma")
def general(ctx: Ctx = Depends(exigir_modulo("cronograma"))):
    out = []
    for o in ctx.db.scalars(select(Obra).where(Obra.empresa_id == ctx.empresa_id).order_by(Obra.nombre)):
        d = datos_cronograma(ctx, o)
        out.append({"obra": d["obra"], "contrato": d["contrato"], "tareas": len(d["tareas"]), "atrasadas": d["atrasadas"], "criticas_atrasadas": d["criticas_atrasadas"],
                    "terminadas": d["terminadas"], "fin_plan": d["fin_plan"], "fin_proyectado": d["fin_proyectado"], "atraso_proyecto": d["atraso_proyecto"]})
    return out


@router.get("/obras/{obra_id}/cronograma")
def de_la_obra(obra_id: str, ctx: Ctx = Depends(exigir_modulo("cronograma"))):
    return datos_cronograma(ctx, obra_de(ctx, obra_id))


@router.get("/obras/{obra_id}/cronograma/propuesta")
def propuesta(obra_id: str, ctx: Ctx = Depends(exigir_modulo("cronograma", *ROLES))):
    """Cronograma inicial sugerido a partir del contrato (no se guarda: el usuario lo ajusta y lo confirma)."""
    obra = obra_de(ctx, obra_id)
    c = ctx.db.scalar(select(Contrato).where(Contrato.obra_id == obra.id, Contrato.empresa_id == ctx.empresa_id))
    if not c:
        raise HTTPException(409, "La obra no tiene contrato cargado")
    acts = list(ctx.db.scalars(select(Actividad).where(Actividad.obra_id == obra.id, Actividad.empresa_id == ctx.empresa_id)))
    if not acts:
        raise HTTPException(409, "El contrato no tiene actividades")
    return proponer_cronograma(acts, c.fecha_inicio, c.fecha_fin)


@router.put("/obras/{obra_id}/cronograma")
def guardar(obra_id: str, datos: CronogramaIn, request: Request, ctx: Ctx = Depends(exigir_modulo("cronograma", *ROLES))):
    obra = obra_de(ctx, obra_id)
    validas = set(ctx.db.scalars(select(Actividad.id).where(Actividad.obra_id == obra.id, Actividad.empresa_id == ctx.empresa_id)))
    vistos: set[str] = set()
    for t in datos.tareas:
        if t.actividad_id not in validas:
            raise HTTPException(422, "Una actividad no pertenece a esta obra")
        if t.actividad_id in vistos:
            raise HTTPException(422, "Una actividad está programada dos veces")
        vistos.add(t.actividad_id)
    for t in datos.tareas:
        if any(p not in vistos or p == t.actividad_id for p in t.predecesoras):
            raise HTTPException(422, "Una predecesora no es válida (debe ser otra actividad programada)")
    if hay_ciclo({t.actividad_id: list(t.predecesoras) for t in datos.tareas}):
        raise HTTPException(422, "Las predecesoras forman un ciclo: una actividad dependería de sí misma")
    for viejo in ctx.db.scalars(select(CronogramaTarea).where(CronogramaTarea.obra_id == obra.id, CronogramaTarea.empresa_id == ctx.empresa_id)):
        ctx.db.delete(viejo)
    ctx.db.flush()
    for t in datos.tareas:
        ctx.db.add(CronogramaTarea(empresa_id=ctx.empresa_id, obra_id=obra.id, **t.model_dump()))
    registrar(ctx.db, accion="cronograma.guardar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=obra.id, detalle={"tareas": len(datos.tareas)}, request=request)
    ctx.db.commit()
    return datos_cronograma(ctx, obra)
