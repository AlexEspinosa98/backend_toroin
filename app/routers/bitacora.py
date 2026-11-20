"""Bitácora de obra: tareas del día, avance por actividad y fotos de evidencia."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from ..audit import registrar
from ..deps import Ctx, exigir_modulo
from ..models import Actividad, Asistencia, Foto, RegistroBitacora, Tarea, Trabajador
from ..reglas import MOTIVOS_ATRASO, habilitacion, tarea_vigente
from ..schemas import AsignarTareasIn, FotoIn, RegistroBitacoraIn
from .asistencia import asignados_en
from .contrato import obra_de

router = APIRouter(tags=["bitacora"])
ROLES_ESCRIBE = ("admin", "gerencia", "residente")
TIPOS_FOTO = ("data:image/jpeg;base64,", "data:image/png;base64,", "data:image/webp;base64,")
MAX_FOTO = 700_000  # caracteres del data URI (~500 KB)


def _reg(r: RegistroBitacora) -> dict:
    return {c.name: getattr(r, c.name) for c in r.__table__.columns if c.name != "empresa_id"}


# ---------- Tareas ----------
@router.get("/tareas")
def tareas_del_dia(obra_id: str, fecha: date, ctx: Ctx = Depends(exigir_modulo("bitacora"))):
    """Tarea vigente de cada trabajador asignado a la obra en esa fecha."""
    obra_de(ctx, obra_id)
    ids = [a.trabajador_id for a in asignados_en(ctx.db, ctx.empresa_id, obra_id, fecha)]
    tareas = list(ctx.db.scalars(select(Tarea).where(Tarea.empresa_id == ctx.empresa_id, Tarea.obra_id == obra_id)))
    out = {}
    for tid in ids:
        t = tarea_vigente(tareas, tid, fecha)
        if t:
            out[tid] = {"id": t.id, "actividad_id": t.actividad_id, "desde": t.desde, "hasta": t.hasta, "instruccion": t.instruccion}
    return out


@router.post("/tareas/asignar", status_code=201)
def asignar_tareas(datos: AsignarTareasIn, request: Request, ctx: Ctx = Depends(exigir_modulo("bitacora", *ROLES_ESCRIBE))):
    obra_de(ctx, datos.obra_id)
    if datos.hasta and datos.hasta < datos.desde:
        raise HTTPException(422, "La fecha final no puede ser anterior a la inicial")
    if datos.actividad_id:
        a = ctx.db.get(Actividad, datos.actividad_id)
        if not a or a.empresa_id != ctx.empresa_id or a.obra_id != datos.obra_id:
            raise HTTPException(404, "Actividad no encontrada")
    validos = {a.trabajador_id for a in asignados_en(ctx.db, ctx.empresa_id, datos.obra_id, datos.desde)}
    if not set(datos.trabajador_ids) <= validos:
        raise HTTPException(422, "Hay personal que no está asignado a esta obra en esa fecha")
    for tid in datos.trabajador_ids:
        ctx.db.add(Tarea(empresa_id=ctx.empresa_id, obra_id=datos.obra_id, trabajador_id=tid, actividad_id=datos.actividad_id,
                         desde=datos.desde, hasta=datos.hasta, instruccion=datos.instruccion, asignado_por=ctx.usuario.correo))
    registrar(ctx.db, accion="tarea.asignar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=datos.obra_id,
              detalle={"personas": len(datos.trabajador_ids), "actividad": datos.actividad_id}, request=request)
    ctx.db.commit()
    return {"ok": True, "asignadas": len(datos.trabajador_ids)}


# ---------- Avance (registros de bitácora) ----------
@router.get("/bitacora")
def listar_bitacora(obra_id: str, fecha: date | None = None, desde: date | None = None, hasta: date | None = None, ctx: Ctx = Depends(exigir_modulo("bitacora"))):
    obra_de(ctx, obra_id)
    q = select(RegistroBitacora).where(RegistroBitacora.empresa_id == ctx.empresa_id, RegistroBitacora.obra_id == obra_id)
    if fecha:
        q = q.where(RegistroBitacora.fecha == fecha)
    if desde:
        q = q.where(RegistroBitacora.fecha >= desde)
    if hasta:
        q = q.where(RegistroBitacora.fecha <= hasta)
    return [_reg(r) for r in ctx.db.scalars(q.order_by(RegistroBitacora.fecha.desc(), RegistroBitacora.creado.desc()))]


@router.get("/bitacora/contexto")
def contexto_dia(obra_id: str, fecha: date, ctx: Ctx = Depends(exigir_modulo("bitacora"))):
    """Personal habilitado y disponible ese día (según asistencia si ya fue registrada)."""
    obra_de(ctx, obra_id)
    ids = {a.trabajador_id for a in asignados_en(ctx.db, ctx.empresa_id, obra_id, fecha)}
    ts = list(ctx.db.scalars(select(Trabajador).where(Trabajador.empresa_id == ctx.empresa_id, Trabajador.id.in_(ids)))) if ids else []
    asis = {a.trabajador_id: a.asistio for a in ctx.db.scalars(select(Asistencia).where(
        Asistencia.empresa_id == ctx.empresa_id, Asistencia.obra_id == obra_id, Asistencia.fecha == fecha))}
    personal = []
    for t in sorted(ts, key=lambda x: x.nombre):
        if not habilitacion(t, fecha).puede_trabajar:
            continue
        personal.append({"trabajador_id": t.id, "nombre": t.nombre, "cargo": t.cargo, "asistio": asis.get(t.id)})
    return {"asistencia_registrada": bool(asis), "personal": personal, "motivos_atraso": list(MOTIVOS_ATRASO)}


@router.put("/bitacora")
def guardar_registro(datos: RegistroBitacoraIn, request: Request, ctx: Ctx = Depends(exigir_modulo("bitacora", *ROLES_ESCRIBE))):
    obra_de(ctx, datos.obra_id)
    if datos.fecha > date.today():
        raise HTTPException(422, "No se puede registrar avance de una fecha futura")
    act = ctx.db.get(Actividad, datos.actividad_id)
    if not act or act.empresa_id != ctx.empresa_id or act.obra_id != datos.obra_id:
        raise HTTPException(404, "Actividad no encontrada")
    if datos.horas_atraso > 0 and datos.motivo_atraso not in MOTIVOS_ATRASO:
        raise HTTPException(422, "Indique el motivo del atraso")
    if datos.trabajadores:
        validos = {a.trabajador_id for a in asignados_en(ctx.db, ctx.empresa_id, datos.obra_id, datos.fecha)}
        if not set(datos.trabajadores) <= validos:
            raise HTTPException(422, "Hay personal que no está asignado a esta obra en esa fecha")
    if datos.por_trabajador:
        if not set(datos.por_trabajador) <= set(datos.trabajadores):
            raise HTTPException(422, "El avance por trabajador incluye a alguien que no está en la actividad")
        if any(v < 0 for v in datos.por_trabajador.values()):
            raise HTTPException(422, "El avance por trabajador no puede ser negativo")
        if abs(sum(datos.por_trabajador.values()) - datos.cantidad) > 0.01:
            raise HTTPException(422, "La suma del avance por trabajador debe ser igual a la cantidad total")
    if datos.fotos:
        n = len(set(ctx.db.scalars(select(Foto.id).where(Foto.empresa_id == ctx.empresa_id, Foto.obra_id == datos.obra_id, Foto.id.in_(datos.fotos)))))
        if n != len(set(datos.fotos)):
            raise HTTPException(422, "Alguna foto no existe")
    r = ctx.db.scalar(select(RegistroBitacora).where(RegistroBitacora.empresa_id == ctx.empresa_id, RegistroBitacora.obra_id == datos.obra_id,
                                                      RegistroBitacora.fecha == datos.fecha, RegistroBitacora.actividad_id == datos.actividad_id))
    vals = datos.model_dump(exclude={"obra_id", "fecha", "actividad_id"})
    vals["personas"] = len(datos.trabajadores) or datos.personas
    vals["registrado_por"] = ctx.usuario.correo
    if r:
        for k, v in vals.items():
            setattr(r, k, v)
    else:
        r = RegistroBitacora(empresa_id=ctx.empresa_id, obra_id=datos.obra_id, fecha=datos.fecha, actividad_id=datos.actividad_id, **vals)
        ctx.db.add(r)
    ctx.db.flush()
    registrar(ctx.db, accion="bitacora.guardar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="bitacora", entidad_id=r.id,
              detalle={"fecha": str(datos.fecha), "actividad": act.nombre, "cantidad": datos.cantidad}, request=request)
    ctx.db.commit()
    return _reg(r)


@router.delete("/bitacora/{id_}", status_code=204)
def borrar_registro(id_: str, request: Request, ctx: Ctx = Depends(exigir_modulo("bitacora", *ROLES_ESCRIBE))):
    r = ctx.db.get(RegistroBitacora, id_)
    if not r or r.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "No encontrado")
    for fid in r.fotos or []:
        f = ctx.db.get(Foto, fid)
        if f and f.empresa_id == ctx.empresa_id:
            ctx.db.delete(f)
    registrar(ctx.db, accion="bitacora.borrar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="bitacora", entidad_id=r.id, request=request)
    ctx.db.delete(r)
    ctx.db.commit()


# ---------- Fotos ----------
@router.post("/fotos", status_code=201)
def subir_foto(datos: FotoIn, ctx: Ctx = Depends(exigir_modulo("bitacora", *ROLES_ESCRIBE))):
    obra_de(ctx, datos.obra_id)
    if not datos.datos.startswith(TIPOS_FOTO) or len(datos.datos) > MAX_FOTO:
        raise HTTPException(422, "La foto debe ser JPG, PNG o WebP de hasta ~500 KB")
    f = Foto(empresa_id=ctx.empresa_id, obra_id=datos.obra_id, datos=datos.datos)
    ctx.db.add(f)
    ctx.db.commit()
    return {"id": f.id}


@router.get("/fotos/{id_}")
def ver_foto(id_: str, ctx: Ctx = Depends(exigir_modulo("bitacora"))):
    f = ctx.db.get(Foto, id_)
    if not f or f.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "No encontrado")
    return {"id": f.id, "datos": f.datos}
