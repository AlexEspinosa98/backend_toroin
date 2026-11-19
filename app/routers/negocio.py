"""Módulos de negocio con aislamiento por empresa. Obras (núcleo) y Personal (licenciable) son la base
sobre la que se portan el resto de módulos del prototipo."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from ..audit import registrar
from ..deps import Ctx, exigir_modulo
from ..models import Obra, Trabajador
from ..schemas import ObraIn, TrabajadorIn

router = APIRouter(tags=["negocio"])


def _limite(ctx: Ctx, clave: str, modelo, nombre: str) -> None:
    tope = ctx.est.limites.get(clave)
    if tope is None:
        return
    n = ctx.db.scalar(select(func.count()).select_from(modelo).where(modelo.empresa_id == ctx.empresa_id))
    if n >= tope:
        raise HTTPException(402, f"Alcanzó el límite de {tope} {nombre} de su licencia")


def _de_empresa(ctx: Ctx, modelo, id_: str):
    o = ctx.db.get(modelo, id_)
    if not o or o.empresa_id != ctx.empresa_id:  # nunca revelar datos de otra empresa
        raise HTTPException(404, "No encontrado")
    return o


def _fila(o) -> dict:
    return {c.name: getattr(o, c.name) for c in o.__table__.columns if c.name != "empresa_id"}


# ---------- Obras ----------
@router.get("/obras")
def listar_obras(ctx: Ctx = Depends(exigir_modulo("obras"))):
    return [_fila(o) for o in ctx.db.scalars(select(Obra).where(Obra.empresa_id == ctx.empresa_id).order_by(Obra.nombre))]


@router.post("/obras", status_code=201)
def crear_obra(datos: ObraIn, request: Request, ctx: Ctx = Depends(exigir_modulo("obras", "admin", "gerencia"))):
    _limite(ctx, "max_obras", Obra, "obras")
    o = Obra(empresa_id=ctx.empresa_id, **datos.model_dump())
    ctx.db.add(o)
    ctx.db.flush()
    registrar(ctx.db, accion="obra.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=o.id, request=request)
    ctx.db.commit()
    return _fila(o)


@router.put("/obras/{id_}")
def editar_obra(id_: str, datos: ObraIn, request: Request, ctx: Ctx = Depends(exigir_modulo("obras", "admin", "gerencia"))):
    o = _de_empresa(ctx, Obra, id_)
    for k, v in datos.model_dump().items():
        setattr(o, k, v)
    registrar(ctx.db, accion="obra.editar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=o.id, request=request)
    ctx.db.commit()
    return _fila(o)


@router.delete("/obras/{id_}", status_code=204)
def borrar_obra(id_: str, request: Request, ctx: Ctx = Depends(exigir_modulo("obras", "admin", "gerencia"))):
    o = _de_empresa(ctx, Obra, id_)
    registrar(ctx.db, accion="obra.borrar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=o.id, detalle={"nombre": o.nombre}, request=request)
    ctx.db.delete(o)
    ctx.db.commit()


# ---------- Personal ----------
@router.get("/personal")
def listar_personal(ctx: Ctx = Depends(exigir_modulo("personal"))):
    return [_fila(t) for t in ctx.db.scalars(select(Trabajador).where(Trabajador.empresa_id == ctx.empresa_id).order_by(Trabajador.nombre))]


@router.post("/personal", status_code=201)
def crear_trabajador(datos: TrabajadorIn, request: Request, ctx: Ctx = Depends(exigir_modulo("personal", "admin", "gerencia", "rrhh"))):
    _limite(ctx, "max_trabajadores", Trabajador, "trabajadores")
    t = Trabajador(empresa_id=ctx.empresa_id, **datos.model_dump())
    ctx.db.add(t)
    ctx.db.flush()
    registrar(ctx.db, accion="trabajador.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="trabajador", entidad_id=t.id, request=request)
    ctx.db.commit()
    return _fila(t)


@router.put("/personal/{id_}")
def editar_trabajador(id_: str, datos: TrabajadorIn, request: Request, ctx: Ctx = Depends(exigir_modulo("personal", "admin", "gerencia", "rrhh", "sst"))):
    t = _de_empresa(ctx, Trabajador, id_)
    for k, v in datos.model_dump().items():
        setattr(t, k, v)
    registrar(ctx.db, accion="trabajador.editar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="trabajador", entidad_id=t.id, request=request)
    ctx.db.commit()
    return _fila(t)


@router.delete("/personal/{id_}", status_code=204)
def borrar_trabajador(id_: str, request: Request, ctx: Ctx = Depends(exigir_modulo("personal", "admin", "gerencia", "rrhh"))):
    t = _de_empresa(ctx, Trabajador, id_)
    registrar(ctx.db, accion="trabajador.borrar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="trabajador", entidad_id=t.id, request=request)
    ctx.db.delete(t)
    ctx.db.commit()
