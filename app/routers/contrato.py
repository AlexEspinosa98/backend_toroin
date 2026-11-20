"""Contrato de la obra, actividades contratadas y tablero de avance."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from .. import catalog
from ..audit import registrar
from ..deps import Ctx, contexto, exigir_modulo
from ..models import Actividad, Asistencia, Contrato, Obra, RegistroBitacora
from ..reglas import ejecutado_por_actividad, resumen_obra
from ..schemas import ContratoIn

router = APIRouter(tags=["contrato"])
ROLES_ESCRIBE = ("admin", "gerencia")


def acceso(ctx: Ctx, *modulos: str) -> None:
    """Permite el acceso si la empresa tiene licenciado alguno de los módulos y el rol puede verlo."""
    for m in modulos:
        if m in ctx.est.modulos and (ctx.usuario.rol == "super_admin" or ctx.usuario.rol in catalog.ROLES_POR_MODULO.get(m, ("admin",))):
            return
    raise HTTPException(403, "Su licencia o su rol no permiten ver esta información")


def obra_de(ctx: Ctx, obra_id: str) -> Obra:
    o = ctx.db.get(Obra, obra_id)
    if not o or o.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "No encontrado")
    return o


def _act(a: Actividad, ejec: float = 0.0) -> dict:
    return {"id": a.id, "item": a.item, "grupo": a.grupo, "capitulo": a.capitulo, "nombre": a.nombre, "ubicacion": a.ubicacion, "um": a.um,
            "cantidad": a.cantidad, "valor_unitario": a.valor_unitario, "valor_total": a.valor_total, "tarifa": a.tarifa,
            "advertencias": a.advertencias, "ejecutado": ejec}


def _contrato(c: Contrato) -> dict:
    d = {k.name: getattr(c, k.name) for k in c.__table__.columns if k.name not in ("empresa_id",)}
    return d


def datos_obra(ctx: Ctx, obra: Obra, fecha: date | None = None) -> dict:
    c = ctx.db.scalar(select(Contrato).where(Contrato.obra_id == obra.id, Contrato.empresa_id == ctx.empresa_id))
    acts = list(ctx.db.scalars(select(Actividad).where(Actividad.obra_id == obra.id, Actividad.empresa_id == ctx.empresa_id).order_by(Actividad.grupo, Actividad.nombre)))
    regs = list(ctx.db.scalars(select(RegistroBitacora).where(RegistroBitacora.obra_id == obra.id, RegistroBitacora.empresa_id == ctx.empresa_id)))
    ejec = ejecutado_por_actividad(regs)
    return {
        "obra": {"id": obra.id, "nombre": obra.nombre, "ciudad": obra.ciudad, "ingresos_proyectados": obra.ingresos_proyectados},
        "contrato": _contrato(c) if c else None,
        "actividades": [_act(a, ejec.get(a.id, 0.0)) for a in acts],
        "resumen": resumen_obra(c, acts, regs, fecha or date.today()),
    }


@router.get("/contratos")
def listar_contratos(ctx: Ctx = Depends(contexto)):
    """Obras de la empresa con el estado de su contrato (para el listado y el tablero general)."""
    acceso(ctx, "contrato", "finanzas", "pagos")
    obras = ctx.db.scalars(select(Obra).where(Obra.empresa_id == ctx.empresa_id).order_by(Obra.nombre))
    out = []
    for o in obras:
        d = datos_obra(ctx, o)
        c = d["contrato"]
        out.append({"obra": d["obra"], "resumen": d["resumen"],
                    "contrato": {k: c[k] for k in ("numero", "contratante", "fecha_inicio", "fecha_fin", "valor_total")} if c else None,
                    "actividades": len(d["actividades"])})
    return out


@router.get("/obras/{obra_id}/presentes")
def presentes_por_dia(obra_id: str, desde: date, hasta: date, ctx: Ctx = Depends(contexto)):
    acceso(ctx, "contrato", "asistencia")
    obra_de(ctx, obra_id)
    filas = ctx.db.scalars(select(Asistencia).where(Asistencia.empresa_id == ctx.empresa_id, Asistencia.obra_id == obra_id,
                                                    Asistencia.fecha >= desde, Asistencia.fecha <= hasta, Asistencia.asistio.is_(True)))
    cuenta: dict[str, int] = {}
    for a in filas:
        cuenta[str(a.fecha)] = cuenta.get(str(a.fecha), 0) + 1
    return cuenta


@router.get("/obras/{obra_id}/contrato")
def ver_contrato(obra_id: str, ctx: Ctx = Depends(contexto)):
    acceso(ctx, "contrato", "bitacora", "pagos", "finanzas", "control_personal")
    return datos_obra(ctx, obra_de(ctx, obra_id))


@router.put("/obras/{obra_id}/contrato")
def guardar_contrato(obra_id: str, datos: ContratoIn, request: Request, ctx: Ctx = Depends(exigir_modulo("contrato", *ROLES_ESCRIBE))):
    obra = obra_de(ctx, obra_id)
    c = ctx.db.scalar(select(Contrato).where(Contrato.obra_id == obra.id))
    cols = datos.model_dump(exclude={"actividades", "ingresos_proyectados", "pagos"})
    pagos = [p.model_dump(mode="json") for p in datos.pagos]
    suma = sum(round(a.cantidad * a.valor_unitario, 2) for a in datos.actividades)
    cols["valor_total"] = datos.valor_total or suma
    if c:
        for k, v in cols.items():
            setattr(c, k, v)
        c.pagos = pagos
    else:
        ctx.db.add(Contrato(empresa_id=ctx.empresa_id, obra_id=obra.id, pagos=pagos, **cols))

    existentes = {a.id: a for a in ctx.db.scalars(select(Actividad).where(Actividad.obra_id == obra.id, Actividad.empresa_id == ctx.empresa_id))}
    usados: set[str] = set()
    for a in datos.actividades:
        vals = a.model_dump(exclude={"id"})
        vals["valor_total"] = round(a.cantidad * a.valor_unitario, 2)
        fila = existentes.get(a.id) if a.id else None
        if fila:
            for k, v in vals.items():
                setattr(fila, k, v)
            usados.add(fila.id)
        else:
            ctx.db.add(Actividad(empresa_id=ctx.empresa_id, obra_id=obra.id, **vals))
    for id_, fila in existentes.items():
        if id_ in usados:
            continue
        if ctx.db.scalar(select(RegistroBitacora.id).where(RegistroBitacora.actividad_id == id_).limit(1)):
            raise HTTPException(409, f"No se puede quitar «{fila.nombre}»: ya tiene avance registrado en la bitácora")
        ctx.db.delete(fila)
    obra.ingresos_proyectados = datos.ingresos_proyectados
    registrar(ctx.db, accion="contrato.guardar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=obra.id,
              detalle={"actividades": len(datos.actividades), "valor": cols["valor_total"]}, request=request)
    ctx.db.commit()
    return datos_obra(ctx, obra)
