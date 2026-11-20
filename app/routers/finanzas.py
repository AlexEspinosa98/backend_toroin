"""Finanzas por obra: ingresos proyectados, facturas, gastos y margen."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from ..audit import registrar
from ..calculos import resumen_financiero
from ..deps import Ctx, exigir_modulo
from ..models import Actividad, Contrato, Factura, Gasto, Obra, RegistroBitacora
from ..schemas import CATEGORIAS_GASTO, FacturaIn, GastoIn, IngresosIn
from .contrato import obra_de

router = APIRouter(tags=["finanzas"])
ROLES = ("admin", "gerencia")


def _f(x) -> dict:
    return {c.name: getattr(x, c.name) for c in x.__table__.columns if c.name != "empresa_id"}


def _datos(ctx: Ctx, obra: Obra) -> dict:
    e = ctx.empresa_id
    c = ctx.db.scalar(select(Contrato).where(Contrato.obra_id == obra.id, Contrato.empresa_id == e))
    acts = list(ctx.db.scalars(select(Actividad).where(Actividad.obra_id == obra.id, Actividad.empresa_id == e)))
    regs = list(ctx.db.scalars(select(RegistroBitacora).where(RegistroBitacora.obra_id == obra.id, RegistroBitacora.empresa_id == e)))
    fac = list(ctx.db.scalars(select(Factura).where(Factura.obra_id == obra.id, Factura.empresa_id == e).order_by(Factura.fecha)))
    gas = list(ctx.db.scalars(select(Gasto).where(Gasto.obra_id == obra.id, Gasto.empresa_id == e).order_by(Gasto.fecha)))
    meses: dict[str, dict] = {}
    for x in fac:
        meses.setdefault(x.fecha.strftime("%Y-%m"), {"facturado": 0.0, "gastos": 0.0})["facturado"] += x.valor
    for x in gas:
        meses.setdefault(x.fecha.strftime("%Y-%m"), {"facturado": 0.0, "gastos": 0.0})["gastos"] += x.valor
    return {"obra": {"id": obra.id, "nombre": obra.nombre, "ciudad": obra.ciudad}, "tiene_contrato": c is not None,
            "resumen": resumen_financiero(obra, c, acts, regs, fac, gas, date.today()),
            "facturas": [_f(x) for x in fac], "gastos": [_f(x) for x in gas],
            "meses": [{"mes": k, **v} for k, v in sorted(meses.items())], "categorias": CATEGORIAS_GASTO}


@router.get("/finanzas")
def general(ctx: Ctx = Depends(exigir_modulo("finanzas"))):
    obras = ctx.db.scalars(select(Obra).where(Obra.empresa_id == ctx.empresa_id).order_by(Obra.nombre))
    out = []
    for o in obras:
        d = _datos(ctx, o)
        out.append({"obra": d["obra"], "tiene_contrato": d["tiene_contrato"], "resumen": d["resumen"]})
    return out


@router.get("/obras/{obra_id}/finanzas")
def de_la_obra(obra_id: str, ctx: Ctx = Depends(exigir_modulo("finanzas"))):
    return _datos(ctx, obra_de(ctx, obra_id))


@router.put("/obras/{obra_id}/ingresos")
def ingresos(obra_id: str, datos: IngresosIn, request: Request, ctx: Ctx = Depends(exigir_modulo("finanzas", *ROLES))):
    o = obra_de(ctx, obra_id)
    o.ingresos_proyectados = datos.ingresos_proyectados
    registrar(ctx.db, accion="finanzas.ingresos", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=o.id, detalle=datos.model_dump(), request=request)
    ctx.db.commit()
    return _datos(ctx, o)


@router.post("/obras/{obra_id}/facturas", status_code=201)
def nueva_factura(obra_id: str, datos: FacturaIn, request: Request, ctx: Ctx = Depends(exigir_modulo("finanzas", *ROLES))):
    o = obra_de(ctx, obra_id)
    ctx.db.add(Factura(empresa_id=ctx.empresa_id, obra_id=o.id, **datos.model_dump()))
    registrar(ctx.db, accion="factura.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=o.id, detalle={"valor": datos.valor}, request=request)
    ctx.db.commit()
    return _datos(ctx, o)


@router.post("/obras/{obra_id}/gastos", status_code=201)
def nuevo_gasto(obra_id: str, datos: GastoIn, request: Request, ctx: Ctx = Depends(exigir_modulo("finanzas", *ROLES))):
    o = obra_de(ctx, obra_id)
    if datos.categoria not in CATEGORIAS_GASTO:
        raise HTTPException(422, "Categoría no válida")
    ctx.db.add(Gasto(empresa_id=ctx.empresa_id, obra_id=o.id, **datos.model_dump()))
    registrar(ctx.db, accion="gasto.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=o.id, detalle={"valor": datos.valor}, request=request)
    ctx.db.commit()
    return _datos(ctx, o)


@router.delete("/facturas/{fid}")
def borrar_factura(fid: str, request: Request, ctx: Ctx = Depends(exigir_modulo("finanzas", *ROLES))):
    x = ctx.db.get(Factura, fid)
    if not x or x.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "No encontrado")
    o = ctx.db.get(Obra, x.obra_id)
    ctx.db.delete(x)
    registrar(ctx.db, accion="factura.eliminar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="factura", entidad_id=fid, request=request)
    ctx.db.commit()
    return _datos(ctx, o)


@router.delete("/gastos/{gid}")
def borrar_gasto(gid: str, request: Request, ctx: Ctx = Depends(exigir_modulo("finanzas", *ROLES))):
    x = ctx.db.get(Gasto, gid)
    if not x or x.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "No encontrado")
    o = ctx.db.get(Obra, x.obra_id)
    ctx.db.delete(x)
    registrar(ctx.db, accion="gasto.eliminar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="gasto", entidad_id=gid, request=request)
    ctx.db.commit()
    return _datos(ctx, o)
