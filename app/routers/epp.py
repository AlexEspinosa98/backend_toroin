"""EPP / SST: solicitud, aprobación y entrega de dotación."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from ..audit import registrar
from ..deps import Ctx, exigir_modulo
from ..models import Asignacion, SolicitudEPP, Trabajador
from ..schemas import ELEMENTOS_EPP, MOTIVOS_EPP, EstadoEPPIn, SolicitudEPPIn
from .contrato import obra_de

router = APIRouter(prefix="/epp", tags=["epp"])
ROLES = ("admin", "gerencia", "sst")


def _fila(s: SolicitudEPP) -> dict:
    return {c.name: getattr(s, c.name) for c in s.__table__.columns if c.name != "empresa_id"}


@router.get("/catalogo")
def catalogo(ctx: Ctx = Depends(exigir_modulo("epp"))):
    return {"elementos": ELEMENTOS_EPP, "motivos": MOTIVOS_EPP}


@router.get("")
def listar(obra_id: str | None = None, estado: str | None = None, ctx: Ctx = Depends(exigir_modulo("epp"))):
    q = select(SolicitudEPP).where(SolicitudEPP.empresa_id == ctx.empresa_id)
    if obra_id:
        q = q.where(SolicitudEPP.obra_id == obra_id)
    if estado:
        q = q.where(SolicitudEPP.estado == estado)
    return [_fila(s) for s in ctx.db.scalars(q.order_by(SolicitudEPP.fecha_solicitud.desc(), SolicitudEPP.creado.desc()))]


@router.post("", status_code=201)
def solicitar(datos: SolicitudEPPIn, request: Request, ctx: Ctx = Depends(exigir_modulo("epp", *ROLES))):
    obra_de(ctx, datos.obra_id)
    t = ctx.db.get(Trabajador, datos.trabajador_id)
    if not t or t.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "Trabajador no encontrado")
    if datos.motivo not in MOTIVOS_EPP:
        raise HTTPException(422, "Motivo no válido")
    asignado = ctx.db.scalar(select(Asignacion.id).where(Asignacion.empresa_id == ctx.empresa_id, Asignacion.obra_id == datos.obra_id,
                                                         Asignacion.trabajador_id == t.id).limit(1))
    if not asignado:
        raise HTTPException(422, "El trabajador debe estar asignado a la obra")
    s = SolicitudEPP(empresa_id=ctx.empresa_id, solicitado_por=ctx.usuario.correo, fecha_solicitud=date.today(), **datos.model_dump())
    ctx.db.add(s)
    registrar(ctx.db, accion="epp.solicitar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="trabajador", entidad_id=t.id,
              detalle={"elemento": datos.elemento, "cantidad": datos.cantidad}, request=request)
    ctx.db.commit()
    return _fila(s)


@router.patch("/{sid}")
def cambiar_estado(sid: str, datos: EstadoEPPIn, request: Request, ctx: Ctx = Depends(exigir_modulo("epp", *ROLES))):
    s = ctx.db.get(SolicitudEPP, sid)
    if not s or s.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "No encontrado")
    validas = {"Solicitado": ("Aprobado", "Rechazado"), "Aprobado": ("Entregado", "Rechazado")}
    if datos.estado not in validas.get(s.estado, ()):
        raise HTTPException(409, f"No se puede pasar de {s.estado} a {datos.estado}")
    s.estado = datos.estado
    if datos.estado == "Aprobado":
        s.fecha_aprobacion = date.today()
    if datos.estado == "Entregado":
        s.fecha_entrega = date.today()
    registrar(ctx.db, accion="epp." + datos.estado.lower(), usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="solicitud_epp", entidad_id=s.id, request=request)
    ctx.db.commit()
    return _fila(s)


@router.delete("/{sid}", status_code=204)
def eliminar(sid: str, request: Request, ctx: Ctx = Depends(exigir_modulo("epp", "admin", "gerencia"))):
    s = ctx.db.get(SolicitudEPP, sid)
    if not s or s.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "No encontrado")
    if s.estado == "Entregado":
        raise HTTPException(409, "Una entrega registrada no se elimina")
    ctx.db.delete(s)
    registrar(ctx.db, accion="epp.eliminar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="solicitud_epp", entidad_id=sid, request=request)
    ctx.db.commit()
