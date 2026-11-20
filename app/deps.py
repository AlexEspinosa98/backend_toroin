"""Dependencias de seguridad: usuario actual, plataforma, contexto de empresa y módulos."""
from __future__ import annotations

from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import catalog
from .db import get_db
from .licensing import EstadoEmpresa, resolver
from .models import Empresa, Usuario
from .security import leer_token

bearer = HTTPBearer(auto_error=False)
METODOS_ESCRITURA = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class Sesion:
    usuario: Usuario
    solo_lectura: bool  # suplantación de solo lectura
    suplantando: bool
    empresa_id: str | None


def sesion_actual(
    cred: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> Sesion:
    if not cred:
        raise HTTPException(401, "No autenticado")
    try:
        data = leer_token(cred.credentials)
    except jwt.PyJWTError:
        raise HTTPException(401, "Sesión inválida o vencida")
    u = db.get(Usuario, data["sub"])
    if not u or not u.activo:
        raise HTTPException(401, "Usuario inactivo")
    imp = bool(data.get("imp"))
    # En suplantación el super admin actúa dentro de la empresa indicada en el token
    empresa_id = data.get("emp") if imp else u.empresa_id
    return Sesion(usuario=u, solo_lectura=bool(data.get("ro")), suplantando=imp, empresa_id=empresa_id)


def exigir_plataforma(s: Sesion = Depends(sesion_actual)) -> Usuario:
    if s.usuario.rol != "super_admin" or s.suplantando:
        raise HTTPException(403, "Solo el super admin de la plataforma")
    return s.usuario


def plataforma_lectura(s: Sesion = Depends(sesion_actual)) -> Usuario:
    if s.usuario.rol not in catalog.ROLES_PLATAFORMA or s.suplantando:
        raise HTTPException(403, "Solo personal de la plataforma")
    return s.usuario


def plataforma(request: Request, s: Sesion = Depends(sesion_actual)) -> Usuario:
    """Rutas de plataforma: consulta para super admin y soporte; cambios solo para el super admin."""
    if s.suplantando or s.usuario.rol not in catalog.ROLES_PLATAFORMA:
        raise HTTPException(403, "Solo personal de la plataforma")
    if request.method in METODOS_ESCRITURA and s.usuario.rol != "super_admin":
        raise HTTPException(403, "Solo el super admin puede modificar")
    return s.usuario


@dataclass
class Ctx:
    sesion: Sesion
    usuario: Usuario
    empresa: Empresa
    est: EstadoEmpresa
    db: Session

    @property
    def empresa_id(self) -> str:
        return self.empresa.id


def contexto(request: Request, s: Sesion = Depends(sesion_actual), db: Session = Depends(get_db)) -> Ctx:
    """Contexto de empresa: valida licencia y aplica el modo solo lectura."""
    if not s.empresa_id:
        raise HTTPException(403, "Esta ruta es de una empresa")
    empresa = db.get(Empresa, s.empresa_id)
    if not empresa:
        raise HTTPException(404, "Empresa no encontrada")
    est = resolver(db, empresa)
    if not est.lectura:
        raise HTTPException(403, est.mensaje or "Sin acceso")
    if request.method in METODOS_ESCRITURA:
        if s.solo_lectura:
            raise HTTPException(403, "Sesión de solo lectura (suplantación)")
        if not est.escritura:
            raise HTTPException(402, est.mensaje or "Licencia vencida: solo consulta")
    return Ctx(sesion=s, usuario=s.usuario, empresa=empresa, est=est, db=db)


def modulos_de_usuario(u: Usuario, est: EstadoEmpresa) -> set[str]:
    """Módulos que el usuario puede usar: lo licenciado, filtrado por lo que su admin le asignó (o por su rol si no le asignó nada)."""
    lic = set(est.modulos)
    if u.rol in ("super_admin", "admin"):
        return lic
    if u.modulos is None:
        return {m for m in lic if u.rol in catalog.ROLES_POR_MODULO.get(m, ("admin",))}
    return (set(u.modulos) & lic) - {"usuarios"}


def exigir_modulo(clave: str, *roles: str):
    """Dependencia: el módulo debe estar licenciado y el usuario autorizado.

    Autoriza el admin, o quien tenga el módulo (por su rol o porque el admin se lo asignó). Si el admin se lo asignó
    expresamente, puede usarlo completo; si solo lo tiene por su rol, escribe únicamente los roles indicados.
    Las obras son de consulta para todo el personal de la empresa (las necesitan asistencia, EPP y personal).
    """

    def dep(ctx: Ctx = Depends(contexto)) -> Ctx:
        if clave not in ctx.est.modulos:
            raise HTTPException(403, f"El módulo «{catalog.MODULOS[clave].nombre}» no está incluido en su licencia")
        u = ctx.usuario
        if u.rol in ("super_admin", "admin"):
            return ctx
        if clave == "obras" and not roles:
            return ctx
        if clave not in modulos_de_usuario(u, ctx.est):
            raise HTTPException(403, "No tiene acceso a este módulo")
        asignado = u.modulos is not None and clave in u.modulos
        if roles and u.rol not in roles and not asignado:
            raise HTTPException(403, "Su rol no puede modificar en este módulo")
        return ctx

    return dep


def exigir_admin_empresa(ctx: Ctx = Depends(contexto)) -> Ctx:
    if ctx.usuario.rol not in ("admin", "super_admin"):
        raise HTTPException(403, "Solo el administrador de la empresa")
    return ctx
