"""Finanzas por obra: ingresos proyectados, facturas, gastos y margen."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from ..audit import registrar
from ..calculos import costo_por_actividad, resumen_financiero
from ..deps import Ctx, exigir_modulo
from ..models import Actividad, Asignacion, Contrato, Factura, Gasto, Obra, RegistroBitacora, Trabajador
from ..reglas import ejecutado_de_items, subitems_por_padre
from ..schemas import CATEGORIAS_GASTO, FacturaIn, GastoIn, IngresosIn
from .contrato import obra_de
from .nomina import cargar_cfg

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
    padres = {a.id: a for a in acts if not a.padre_id}
    rubros = [{"id": a.id, "nombre": (f"{a.nombre} · {a.ubicacion}" if a.ubicacion else a.nombre), "padre_id": a.padre_id,
               "padre": padres[a.padre_id].nombre if a.padre_id in padres else ""}
              for a in sorted(acts, key=lambda x: (x.grupo, x.padre_id or x.id, x.padre_id != "", x.nombre))]
    return {"obra": {"id": obra.id, "nombre": obra.nombre, "ciudad": obra.ciudad}, "tiene_contrato": c is not None,
            "resumen": resumen_financiero(obra, c, acts, regs, fac, gas, date.today()), "rubros": rubros,
            "facturas": [_f(x) for x in fac], "gastos": [_f(x) for x in gas],
            "meses": [{"mes": k, **v} for k, v in sorted(meses.items())], "categorias": CATEGORIAS_GASTO}


def _rubro_valido(ctx: Ctx, obra_id: str, actividad_id: str) -> str:
    """El ítem (o subítem) del contrato al que se imputa el movimiento debe ser de esta obra."""
    if not actividad_id:
        return ""
    a = ctx.db.get(Actividad, actividad_id)
    if not a or a.empresa_id != ctx.empresa_id or a.obra_id != obra_id:
        raise HTTPException(422, "La actividad no es de esta obra")
    return a.id


@router.get("/obras/{obra_id}/rubros")
def rubros(obra_id: str, ctx: Ctx = Depends(exigir_modulo("finanzas"))):
    """Cada ítem del contrato con lo que vale, lo ejecutado y lo que realmente ha costado (mano de obra, gastos y compras imputadas)."""
    obra = obra_de(ctx, obra_id)
    e, db, hoy = ctx.empresa_id, ctx.db, date.today()
    acts = list(db.scalars(select(Actividad).where(Actividad.obra_id == obra.id, Actividad.empresa_id == e).order_by(Actividad.grupo, Actividad.nombre)))
    regs = list(db.scalars(select(RegistroBitacora).where(RegistroBitacora.obra_id == obra.id, RegistroBitacora.empresa_id == e)))
    gas = list(db.scalars(select(Gasto).where(Gasto.obra_id == obra.id, Gasto.empresa_id == e)))
    fac = list(db.scalars(select(Factura).where(Factura.obra_id == obra.id, Factura.empresa_id == e)))
    ids = {t for r in regs for t in (r.trabajadores or [])}
    ts = {t.id: t for t in db.scalars(select(Trabajador).where(Trabajador.empresa_id == e, Trabajador.id.in_(ids)))} if ids else {}
    asig: dict[str, list] = {}
    for x in db.scalars(select(Asignacion).where(Asignacion.empresa_id == e, Asignacion.obra_id == obra.id)):
        asig.setdefault(x.trabajador_id, []).append(x)
    mo = {c["actividad_id"]: c["costo"] for c in costo_por_actividad(acts, regs, ts, date(2000, 1, 1), hoy, hoy, cargar_cfg(ctx, hoy.year), asig)}
    ejec = ejecutado_de_items(acts, regs)
    hijos = subitems_por_padre(acts)
    filas = []
    for a in acts:
        if a.padre_id:
            continue
        familia = {a.id, *(h.id for h in hijos.get(a.id, []))}
        hecho = min(ejec.get(a.id, 0.0), a.cantidad)
        mano = sum(mo.get(i, 0.0) for i in familia)
        gastos = sum(x.valor for x in gas if x.actividad_id in familia)
        facturado = sum(x.valor for x in fac if x.actividad_id in familia)
        valor_ejec = hecho * a.valor_unitario
        filas.append({"actividad_id": a.id, "item": a.item, "nombre": a.nombre, "ubicacion": a.ubicacion, "um": a.um, "cantidad": a.cantidad,
                      "ejecutado": hecho, "contratado": a.valor_total, "valor_ejecutado": valor_ejec, "mano_obra": mano, "gastos": gastos,
                      "facturado": facturado, "costo": mano + gastos, "margen": valor_ejec - (mano + gastos),
                      "subitems": len(hijos.get(a.id, []))})
    imputables = {a.id for a in acts}
    sin_imputar = {"gastos": sum(x.valor for x in gas if x.actividad_id not in imputables),
                   "facturado": sum(x.valor for x in fac if x.actividad_id not in imputables)}
    return {"obra": {"id": obra.id, "nombre": obra.nombre}, "rubros": filas, "sin_imputar": sin_imputar}


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
    vals = datos.model_dump()
    vals["actividad_id"] = _rubro_valido(ctx, o.id, vals["actividad_id"])
    ctx.db.add(Factura(empresa_id=ctx.empresa_id, obra_id=o.id, **vals))
    registrar(ctx.db, accion="factura.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=o.id, detalle={"valor": datos.valor}, request=request)
    ctx.db.commit()
    return _datos(ctx, o)


@router.post("/obras/{obra_id}/gastos", status_code=201)
def nuevo_gasto(obra_id: str, datos: GastoIn, request: Request, ctx: Ctx = Depends(exigir_modulo("finanzas", *ROLES))):
    o = obra_de(ctx, obra_id)
    if datos.categoria not in CATEGORIAS_GASTO:
        raise HTTPException(422, "Categoría no válida")
    vals = datos.model_dump()
    vals["actividad_id"] = _rubro_valido(ctx, o.id, vals["actividad_id"])
    ctx.db.add(Gasto(empresa_id=ctx.empresa_id, obra_id=o.id, **vals))
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
