"""Asignación de personal a obras y asistencia diaria con control de habilitación SST."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from ..audit import registrar
from ..deps import Ctx, exigir_modulo
from ..models import Asignacion, Asistencia, Obra, Trabajador
from ..reglas import MOTIVOS_INASISTENCIA, habilitacion
from ..schemas import AsignacionIn, AsistenciaIn, PagoAsignacionIn, RetiroIn

router = APIRouter(tags=["asistencia"])
ROLES_ESCRIBE_ASISTENCIA = ("admin", "gerencia", "sst", "residente")
# El SST pasa lista en la obra, el mismo día. Corregir días anteriores es de la administración.
ROLES_SOLO_HOY = ("sst",)
ROLES_ASIGNA = ("admin", "gerencia", "rrhh")


def _de_empresa(ctx: Ctx, modelo, id_: str):
    o = ctx.db.get(modelo, id_)
    if not o or o.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "No encontrado")
    return o


def _asig(a: Asignacion) -> dict:
    return {"id": a.id, "trabajador_id": a.trabajador_id, "obra_id": a.obra_id, "fecha_ingreso": a.fecha_ingreso, "fecha_retiro": a.fecha_retiro,
            "modalidad": a.modalidad, "jornal": a.jornal}


def asignados_en(db, empresa_id: str, obra_id: str, fecha: date) -> list[Asignacion]:
    q = select(Asignacion).where(
        Asignacion.empresa_id == empresa_id, Asignacion.obra_id == obra_id, Asignacion.fecha_ingreso <= fecha,
        (Asignacion.fecha_retiro.is_(None)) | (Asignacion.fecha_retiro >= fecha),
    )
    return list(db.scalars(q))


# ---------- Asignaciones (módulo personal) ----------
@router.get("/asignaciones")
def listar_asignaciones(obra_id: str | None = None, trabajador_id: str | None = None, ctx: Ctx = Depends(exigir_modulo("personal"))):
    q = select(Asignacion).where(Asignacion.empresa_id == ctx.empresa_id)
    if obra_id:
        q = q.where(Asignacion.obra_id == obra_id)
    if trabajador_id:
        q = q.where(Asignacion.trabajador_id == trabajador_id)
    return [_asig(a) for a in ctx.db.scalars(q.order_by(Asignacion.fecha_ingreso.desc()))]


@router.post("/asignaciones", status_code=201)
def asignar(datos: AsignacionIn, request: Request, ctx: Ctx = Depends(exigir_modulo("personal", *ROLES_ASIGNA))):
    _de_empresa(ctx, Trabajador, datos.trabajador_id)
    _de_empresa(ctx, Obra, datos.obra_id)
    abierta = ctx.db.scalar(select(Asignacion).where(
        Asignacion.empresa_id == ctx.empresa_id, Asignacion.trabajador_id == datos.trabajador_id,
        Asignacion.obra_id == datos.obra_id, Asignacion.fecha_retiro.is_(None)))
    if abierta:
        raise HTTPException(409, "El trabajador ya está asignado a esta obra")
    a = Asignacion(empresa_id=ctx.empresa_id, **datos.model_dump())
    ctx.db.add(a)
    ctx.db.flush()
    registrar(ctx.db, accion="asignacion.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="asignacion", entidad_id=a.id, request=request)
    ctx.db.commit()
    return _asig(a)


@router.patch("/asignaciones/{id_}/pago")
def cambiar_pago(id_: str, datos: PagoAsignacionIn, request: Request, ctx: Ctx = Depends(exigir_modulo("personal", *ROLES_ASIGNA))):
    """Cómo se le paga en esta obra: por jornal, por ejecutado, o lo que tenga la empresa."""
    a = _de_empresa(ctx, Asignacion, id_)
    # Solo se cambia lo que venga en la petición: mandar la modalidad sola no borra el jornal pactado
    if "modalidad" in datos.model_fields_set:
        a.modalidad = datos.modalidad
    if "jornal" in datos.model_fields_set:
        a.jornal = datos.jornal
    registrar(ctx.db, accion="asignacion.pago", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="asignacion", entidad_id=a.id,
              detalle={"modalidad": a.modalidad or "empresa", "jornal": a.jornal}, request=request)
    ctx.db.commit()
    return _asig(a)


@router.patch("/asignaciones/{id_}/retiro")
def retirar(id_: str, datos: RetiroIn, request: Request, ctx: Ctx = Depends(exigir_modulo("personal", *ROLES_ASIGNA))):
    a = _de_empresa(ctx, Asignacion, id_)
    if datos.fecha_retiro < a.fecha_ingreso:
        raise HTTPException(422, "El retiro no puede ser anterior al ingreso")
    a.fecha_retiro = datos.fecha_retiro
    registrar(ctx.db, accion="asignacion.retiro", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="asignacion", entidad_id=a.id, request=request)
    ctx.db.commit()
    return _asig(a)


@router.delete("/asignaciones/{id_}", status_code=204)
def borrar_asignacion(id_: str, request: Request, ctx: Ctx = Depends(exigir_modulo("personal", *ROLES_ASIGNA))):
    a = _de_empresa(ctx, Asignacion, id_)
    registrar(ctx.db, accion="asignacion.borrar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="asignacion", entidad_id=a.id, request=request)
    ctx.db.delete(a)
    ctx.db.commit()


# ---------- Asistencia ----------
@router.get("/asistencia")
def ver_asistencia(obra_id: str, fecha: date, ctx: Ctx = Depends(exigir_modulo("asistencia"))):
    """Personal asignado a la obra en esa fecha, con su habilitación y la marca ya registrada."""
    _de_empresa(ctx, Obra, obra_id)
    asign = asignados_en(ctx.db, ctx.empresa_id, obra_id, fecha)
    ids = {a.trabajador_id for a in asign}
    trabajadores = list(ctx.db.scalars(select(Trabajador).where(Trabajador.empresa_id == ctx.empresa_id, Trabajador.id.in_(ids)))) if ids else []
    marcas = {m.trabajador_id: m for m in ctx.db.scalars(select(Asistencia).where(
        Asistencia.empresa_id == ctx.empresa_id, Asistencia.obra_id == obra_id, Asistencia.fecha == fecha))}
    out = []
    for t in sorted(trabajadores, key=lambda x: x.nombre):
        h = habilitacion(t, fecha)
        m = marcas.get(t.id)
        out.append({
            "trabajador_id": t.id, "nombre": t.nombre, "cargo": t.cargo, "habilitacion": h.a_dict(), "puede_trabajar": h.puede_trabajar,
            "marca": {"asistio": m.asistio, "motivo": m.motivo, "detalle": m.detalle} if m else None,
        })
    return {"obra_id": obra_id, "fecha": fecha, "registrada": bool(marcas), "personal": out, "motivos": list(MOTIVOS_INASISTENCIA),
            "solo_hoy": ctx.usuario.rol in ROLES_SOLO_HOY, "hoy": date.today()}


@router.put("/asistencia")
def guardar_asistencia(datos: AsistenciaIn, request: Request, ctx: Ctx = Depends(exigir_modulo("asistencia", *ROLES_ESCRIBE_ASISTENCIA))):
    _de_empresa(ctx, Obra, datos.obra_id)
    hoy = date.today()
    if datos.fecha > hoy:
        raise HTTPException(422, "No se puede registrar asistencia de una fecha futura")
    if ctx.usuario.rol in ROLES_SOLO_HOY and datos.fecha != hoy:
        raise HTTPException(403, "Solo puede registrar la asistencia de hoy. Pida a administración que corrija los días anteriores.")
    asignados = {a.trabajador_id for a in asignados_en(ctx.db, ctx.empresa_id, datos.obra_id, datos.fecha)}
    trabajadores = {t.id: t for t in ctx.db.scalars(select(Trabajador).where(Trabajador.empresa_id == ctx.empresa_id, Trabajador.id.in_(asignados)))} if asignados else {}
    vistos: set[str] = set()
    for m in datos.marcas:
        if m.trabajador_id in vistos:
            raise HTTPException(422, "Trabajador repetido en la lista")
        vistos.add(m.trabajador_id)
        t = trabajadores.get(m.trabajador_id)
        if not t:
            raise HTTPException(422, "Hay personal que no está asignado a esta obra en esa fecha")
        if not habilitacion(t, datos.fecha).puede_trabajar:
            raise HTTPException(422, f"{t.nombre} no está habilitado: {habilitacion(t, datos.fecha).motivo}")
        if not m.asistio and m.motivo not in MOTIVOS_INASISTENCIA:
            raise HTTPException(422, f"Indique el motivo de inasistencia de {t.nombre}")
    existentes = {a.trabajador_id: a for a in ctx.db.scalars(select(Asistencia).where(
        Asistencia.empresa_id == ctx.empresa_id, Asistencia.obra_id == datos.obra_id, Asistencia.fecha == datos.fecha))}
    for m in datos.marcas:
        fila = existentes.get(m.trabajador_id)
        valores = {"asistio": m.asistio, "motivo": None if m.asistio else m.motivo, "detalle": "" if m.asistio else m.detalle, "registrado_por": ctx.usuario.correo}
        if fila:
            for k, v in valores.items():
                setattr(fila, k, v)
        else:
            ctx.db.add(Asistencia(empresa_id=ctx.empresa_id, obra_id=datos.obra_id, trabajador_id=m.trabajador_id, fecha=datos.fecha, **valores))
    registrar(ctx.db, accion="asistencia.guardar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=datos.obra_id,
              detalle={"fecha": str(datos.fecha), "marcas": len(datos.marcas)}, request=request)
    ctx.db.commit()
    return {"ok": True, "guardadas": len(datos.marcas)}
