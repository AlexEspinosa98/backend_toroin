"""Configuración de la empresa, calendario, control de personal y pagos (prenómina)."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from ..audit import registrar
from ..calculos import AUX_DEF, SMMLV_DEF, Cfg, calcular_periodo, costo_por_actividad, liquidar
from ..deps import Ctx, contexto, exigir_modulo
from ..festivos import festivos_colombia
from ..models import Actividad, Asignacion, Asistencia, ConfigEmpresa, Obra, ParametroLegal, RegistroBitacora, Trabajador
from ..schemas import ConfigIn, TarifaIn
from .contrato import acceso, obra_de

router = APIRouter(tags=["nomina"])


def cargar_cfg(ctx: Ctx, anio: int | None = None) -> Cfg:
    c = ctx.db.scalar(select(ConfigEmpresa).where(ConfigEmpresa.empresa_id == ctx.empresa_id))
    p = ctx.db.scalar(select(ParametroLegal).where(ParametroLegal.pais == ctx.empresa.pais).order_by(ParametroLegal.anio.desc()))
    if anio:
        exacto = ctx.db.scalar(select(ParametroLegal).where(ParametroLegal.pais == ctx.empresa.pais, ParametroLegal.anio == anio))
        p = exacto or p
    cfg = Cfg(smmlv=p.salario_minimo if p else SMMLV_DEF, aux_transporte=p.aux_transporte if p else AUX_DEF)
    if c:
        cfg.semana, cfg.regla_dominical, cfg.modalidad_pago = c.semana, c.regla_dominical, c.modalidad_pago
        cfg.jornal_base, cfg.factor_prestacional = c.jornal_base, c.factor_prestacional
        cfg.festivos_extra = frozenset(date.fromisoformat(x) for x in (c.festivos_extra or []))
    return cfg


def _cfg_json(cfg: Cfg) -> dict:
    return {"semana": cfg.semana, "regla_dominical": cfg.regla_dominical, "modalidad_pago": cfg.modalidad_pago, "jornal_base": cfg.jornal_base,
            "factor_prestacional": cfg.factor_prestacional, "festivos_extra": sorted(cfg.festivos_extra), "smmlv": cfg.smmlv,
            "aux_transporte": cfg.aux_transporte, "jornal_minimo": cfg.jornal_minimo}


@router.get("/config")
def ver_config(ctx: Ctx = Depends(contexto)):
    return _cfg_json(cargar_cfg(ctx))


@router.put("/config")
def guardar_config(datos: ConfigIn, request: Request, ctx: Ctx = Depends(exigir_modulo("control_personal", "admin", "gerencia"))):
    c = ctx.db.scalar(select(ConfigEmpresa).where(ConfigEmpresa.empresa_id == ctx.empresa_id))
    vals = datos.model_dump(exclude={"festivos_extra"})
    vals["festivos_extra"] = sorted({d.isoformat() for d in datos.festivos_extra})
    if c:
        for k, v in vals.items():
            setattr(c, k, v)
    else:
        ctx.db.add(ConfigEmpresa(empresa_id=ctx.empresa_id, **vals))
    registrar(ctx.db, accion="config.guardar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="empresa", entidad_id=ctx.empresa_id, detalle=vals, request=request)
    ctx.db.commit()
    return _cfg_json(cargar_cfg(ctx))


@router.get("/festivos")
def festivos(anio: int, ctx: Ctx = Depends(contexto)):
    if not 2000 <= anio <= 2100:
        raise HTTPException(422, "Año fuera de rango")
    return [{"fecha": f, "nombre": n} for f, n in festivos_colombia(anio).items()]


def _datos_periodo(ctx: Ctx, desde: date, hasta: date, obra_id: str | None):
    if hasta < desde or (hasta - desde).days > 400:
        raise HTTPException(422, "Rango de fechas no válido")
    if obra_id:
        obra_de(ctx, obra_id)
    fa = [Asistencia.empresa_id == ctx.empresa_id, Asistencia.fecha >= desde, Asistencia.fecha <= hasta]
    fs = [Asignacion.empresa_id == ctx.empresa_id, Asignacion.fecha_ingreso <= hasta, (Asignacion.fecha_retiro.is_(None)) | (Asignacion.fecha_retiro >= desde)]
    if obra_id:
        fa.append(Asistencia.obra_id == obra_id)
        fs.append(Asignacion.obra_id == obra_id)
    asist: dict[str, list] = {}
    for a in ctx.db.scalars(select(Asistencia).where(*fa)):
        asist.setdefault(a.trabajador_id, []).append(a)
    asig: dict[str, list] = {}
    for a in ctx.db.scalars(select(Asignacion).where(*fs)):
        asig.setdefault(a.trabajador_id, []).append(a)
    ids = set(asist) | set(asig)
    ts = list(ctx.db.scalars(select(Trabajador).where(Trabajador.empresa_id == ctx.empresa_id, Trabajador.id.in_(ids)).order_by(Trabajador.nombre))) if ids else []
    return ts, asist, asig


@router.get("/control-personal")
def control_personal(desde: date, hasta: date, obra_id: str | None = None, corte: date | None = None, ctx: Ctx = Depends(contexto)):
    acceso(ctx, "control_personal")
    cfg = cargar_cfg(ctx, hasta.year)
    corte = corte or date.today()
    ts, asist, asig = _datos_periodo(ctx, desde, hasta, obra_id)
    filas = []
    for t in ts:
        r = calcular_periodo(desde, hasta, asist.get(t.id, []), asig.get(t.id, []), cfg, corte)
        filas.append({"trabajador_id": t.id, "nombre": t.nombre, "cedula": t.cedula, "cargo": t.cargo, **r})
    return {"config": _cfg_json(cfg), "filas": filas}


@router.get("/pagos")
def pagos(desde: date, hasta: date, obra_id: str | None = None, corte: date | None = None, ctx: Ctx = Depends(contexto)):
    acceso(ctx, "pagos")
    cfg = cargar_cfg(ctx, hasta.year)
    corte = corte or date.today()
    ts, asist, asig = _datos_periodo(ctx, desde, hasta, obra_id)
    q = [RegistroBitacora.empresa_id == ctx.empresa_id, RegistroBitacora.fecha >= desde, RegistroBitacora.fecha <= hasta]
    qa = [Actividad.empresa_id == ctx.empresa_id]
    if obra_id:
        q.append(RegistroBitacora.obra_id == obra_id)
        qa.append(Actividad.obra_id == obra_id)
    regs = list(ctx.db.scalars(select(RegistroBitacora).where(*q)))
    acts_l = list(ctx.db.scalars(select(Actividad).where(*qa)))
    acts = {a.id: a for a in acts_l}
    filas = [liquidar(t, desde, hasta, corte, asist.get(t.id, []), asig.get(t.id, []), regs, acts, cfg) for t in ts]
    tmap = {t.id: t for t in ts}
    costos = costo_por_actividad(acts_l, regs, tmap, desde, hasta, corte, cfg)
    tot = {k: sum(f[k] for f in filas) for k in ("devengado", "auxilio", "salud", "pension", "neto")}
    return {"config": _cfg_json(cfg), "filas": filas, "totales": tot, "costos": costos, "sin_tarifa": sum(f["sin_tarifa"] for f in filas)}


@router.patch("/actividades/{aid}/tarifa")
def fijar_tarifa(aid: str, datos: TarifaIn, request: Request, ctx: Ctx = Depends(exigir_modulo("pagos", "admin", "gerencia"))):
    a = ctx.db.get(Actividad, aid)
    if not a or a.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "No encontrado")
    a.tarifa = datos.tarifa
    registrar(ctx.db, accion="actividad.tarifa", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="actividad", entidad_id=a.id, detalle={"tarifa": datos.tarifa}, request=request)
    ctx.db.commit()
    return {"id": a.id, "tarifa": a.tarifa}


@router.get("/tarifas")
def tarifas(obra_id: str, ctx: Ctx = Depends(contexto)):
    acceso(ctx, "pagos")
    obra_de(ctx, obra_id)
    acts = ctx.db.scalars(select(Actividad).where(Actividad.empresa_id == ctx.empresa_id, Actividad.obra_id == obra_id).order_by(Actividad.grupo, Actividad.nombre))
    return [{"id": a.id, "nombre": a.nombre, "ubicacion": a.ubicacion, "um": a.um, "valor_unitario": a.valor_unitario, "tarifa": a.tarifa} for a in acts]
