"""Administración de la empresa por su propio admin: marca, usuarios y consulta de licencia."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from .. import catalog
from ..audit import registrar
from ..deps import Ctx, contexto, exigir_admin_empresa, exigir_modulo
from ..models import Auditoria, Obra, Trabajador, Usuario
from ..schemas import MarcaIn, UsuarioIn, UsuarioPatchIn
from ..security import hash_password, password_temporal
from .auth import _marca

router = APIRouter(prefix="/empresa", tags=["empresa"])


def _usuario_out(u: Usuario) -> dict:
    return {"id": u.id, "nombre": u.nombre, "correo": u.correo, "rol": u.rol, "activo": u.activo, "ultimo_acceso": u.ultimo_acceso}


@router.get("/marca")
def ver_marca(ctx: Ctx = Depends(contexto)):
    return _marca(ctx.empresa)


@router.put("/marca")
def guardar_marca(datos: MarcaIn, request: Request, ctx: Ctx = Depends(exigir_admin_empresa)):
    e = ctx.empresa
    for k, v in datos.model_dump().items():
        setattr(e, k, v)
    registrar(ctx.db, accion="marca.actualizar", usuario=ctx.usuario, empresa_id=e.id, entidad="empresa", entidad_id=e.id, request=request)
    ctx.db.commit()
    return _marca(e)


@router.get("/licencia")
def ver_licencia(ctx: Ctx = Depends(contexto)):
    est, db = ctx.est, ctx.db
    uso = {
        "usuarios": db.scalar(select(func.count()).select_from(Usuario).where(Usuario.empresa_id == ctx.empresa_id, Usuario.activo.is_(True))),
        "obras": db.scalar(select(func.count()).select_from(Obra).where(Obra.empresa_id == ctx.empresa_id)),
        "trabajadores": db.scalar(select(func.count()).select_from(Trabajador).where(Trabajador.empresa_id == ctx.empresa_id)),
    }
    return {
        "estado": est.estado, "mensaje": est.mensaje, "fin": est.fin, "dias_restantes": est.dias_restantes,
        "tipo": est.licencia.tipo if est.licencia else None,
        "plan": est.licencia.plan.nombre if est.licencia and est.licencia.plan else None,
        "modulos": [
            {"clave": k, "nombre": catalog.MODULOS[k].nombre, "activo": k in est.modulos}
            for k in catalog.MODULOS if catalog.MODULOS[k].disponible and not catalog.MODULOS[k].nucleo
        ],
        "limites": est.limites, "uso": uso,
    }


# ---------- Usuarios ----------
@router.get("/usuarios")
def listar_usuarios(ctx: Ctx = Depends(exigir_modulo("usuarios"))):
    us = ctx.db.scalars(select(Usuario).where(Usuario.empresa_id == ctx.empresa_id).order_by(Usuario.nombre)).all()
    return [_usuario_out(u) for u in us]


def _verificar_limite_usuarios(ctx: Ctx) -> None:
    tope = ctx.est.limites.get("max_usuarios")
    if tope is None:
        return
    n = ctx.db.scalar(select(func.count()).select_from(Usuario).where(Usuario.empresa_id == ctx.empresa_id, Usuario.activo.is_(True)))
    if n >= tope:
        raise HTTPException(402, f"Alcanzó el límite de {tope} usuarios de su licencia")


@router.post("/usuarios", status_code=201)
def crear_usuario(datos: UsuarioIn, request: Request, ctx: Ctx = Depends(exigir_modulo("usuarios"))):
    _verificar_limite_usuarios(ctx)
    correo = datos.correo.lower()
    if ctx.db.scalars(select(Usuario).where(Usuario.empresa_id == ctx.empresa_id, Usuario.correo == correo)).first():
        raise HTTPException(409, "Ya existe un usuario con ese correo")
    clave = password_temporal()
    u = Usuario(empresa_id=ctx.empresa_id, correo=correo, nombre=datos.nombre, rol=datos.rol,
                password_hash=hash_password(clave), debe_cambiar_password=True)
    ctx.db.add(u)
    ctx.db.flush()
    registrar(ctx.db, accion="usuario.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="usuario", entidad_id=u.id, detalle={"rol": u.rol}, request=request)
    ctx.db.commit()
    return {**_usuario_out(u), "password_temporal": clave}


@router.patch("/usuarios/{uid}")
def editar_usuario(uid: str, datos: UsuarioPatchIn, request: Request, ctx: Ctx = Depends(exigir_modulo("usuarios"))):
    u = ctx.db.get(Usuario, uid)
    if not u or u.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "Usuario no encontrado")
    cambios = datos.model_dump(exclude_none=True)
    if u.id == ctx.usuario.id and (cambios.get("activo") is False or ("rol" in cambios and cambios["rol"] != "admin")):
        raise HTTPException(400, "No puede desactivarse ni quitarse el rol de administrador a sí mismo")
    if cambios.get("activo") is True and not u.activo:
        _verificar_limite_usuarios(ctx)
    for k, v in cambios.items():
        setattr(u, k, v)
    registrar(ctx.db, accion="usuario.editar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="usuario", entidad_id=u.id, detalle=cambios, request=request)
    ctx.db.commit()
    return _usuario_out(u)


@router.post("/usuarios/{uid}/reiniciar-password")
def reiniciar_password(uid: str, request: Request, ctx: Ctx = Depends(exigir_modulo("usuarios"))):
    u = ctx.db.get(Usuario, uid)
    if not u or u.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "Usuario no encontrado")
    clave = password_temporal()
    u.password_hash = hash_password(clave)
    u.debe_cambiar_password = True
    registrar(ctx.db, accion="usuario.reiniciar_password", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="usuario", entidad_id=u.id, request=request)
    ctx.db.commit()
    return {"password_temporal": clave}


@router.get("/actividad")
def actividad(limite: int = 100, solo_errores: bool = False, ctx: Ctx = Depends(exigir_admin_empresa)):
    """Registro de actividad de la empresa (quién hizo qué) y errores del sistema."""
    q = select(Auditoria).where(Auditoria.empresa_id == ctx.empresa_id).order_by(Auditoria.fecha.desc()).limit(min(max(limite, 1), 300))
    if solo_errores:
        q = q.where(Auditoria.accion == "error.500")
    return [{"id": a.id, "fecha": a.fecha, "usuario": a.usuario_correo, "accion": a.accion, "entidad": a.entidad, "entidad_id": a.entidad_id,
             "detalle": {k: v for k, v in (a.detalle or {}).items() if k != "traza"}} for a in ctx.db.scalars(q)]
