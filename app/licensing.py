"""Reglas de licencias: estado, módulos efectivos y límites de una empresa."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalog
from .models import Empresa, Licencia

DIAS_AVISO = 30

# Estados en los que la empresa puede registrar datos / solo consultar / nada
ESTADOS_ESCRITURA = {"prueba", "activa", "por_vencer", "gracia"}
ESTADOS_LECTURA = {"vencida"}


@dataclass
class EstadoEmpresa:
    estado: str
    escritura: bool
    lectura: bool
    fin: date | None
    dias_restantes: int | None
    modulos: set[str] = field(default_factory=set)
    limites: dict = field(default_factory=dict)
    licencia: Licencia | None = None
    mensaje: str = ""


def licencia_vigente(db: Session, empresa_id: str) -> Licencia | None:
    return db.scalars(
        select(Licencia).where(Licencia.empresa_id == empresa_id).order_by(Licencia.numero.desc()).limit(1)
    ).first()


def estado_licencia(lic: Licencia | None, empresa: Empresa, hoy: date) -> str:
    if empresa.estado == "cancelada":
        return "cancelada"
    if empresa.estado == "suspendida":
        return "suspendida"
    if lic is None:
        return "sin_licencia"
    if lic.cancelada:
        return "cancelada"
    if lic.suspendida:
        return "suspendida"
    if lic.inicio > hoy:
        return "sin_licencia"
    if lic.fin is None:
        return "activa"
    if hoy <= lic.fin:
        if lic.tipo == "prueba":
            return "prueba"
        return "por_vencer" if (lic.fin - hoy).days <= DIAS_AVISO else "activa"
    if hoy <= lic.fin + timedelta(days=lic.gracia_dias):
        return "gracia"
    return "vencida"


def modulos_efectivos(lic: Licencia | None, hoy: date, podar: bool = True) -> set[str]:
    """Plan + control por módulo. Un control explícito manda sobre el plan:
    activo=True dentro de su ventana -> ON; activo=True con ventana vencida o activo=False -> OFF."""
    if lic is None:
        return set(catalog.NUCLEO)
    activos = set(lic.plan.modulos) if lic.plan else set()
    for ov in lic.modulos:
        dentro = (ov.inicio is None or ov.inicio <= hoy) and (ov.fin is None or hoy <= ov.fin)
        if ov.activo and dentro:
            activos.add(ov.modulo)
        else:
            activos.discard(ov.modulo)
    activos &= set(catalog.activables())
    activos |= set(catalog.NUCLEO)
    return catalog.podar(activos) if podar else activos


def limites_efectivos(lic: Licencia | None) -> dict:
    base = dict(lic.plan.limites) if lic and lic.plan else {}
    if lic:
        base.update(lic.limites or {})
    return base


def resolver(db: Session, empresa: Empresa, hoy: date | None = None) -> EstadoEmpresa:
    hoy = hoy or date.today()
    lic = licencia_vigente(db, empresa.id)
    est = estado_licencia(lic, empresa, hoy)
    dias = (lic.fin - hoy).days if lic and lic.fin else None
    mensajes = {
        "prueba": "Licencia de prueba",
        "por_vencer": f"La licencia vence en {dias} días",
        "gracia": "Licencia vencida: periodo de gracia. Renueva para no perder el acceso",
        "vencida": "Licencia vencida: solo consulta. Contacta al proveedor para renovar",
        "suspendida": "Cuenta suspendida. Contacta al proveedor",
        "cancelada": "Cuenta cancelada",
        "sin_licencia": "Sin licencia vigente",
    }
    return EstadoEmpresa(
        estado=est,
        escritura=est in ESTADOS_ESCRITURA or est == "activa",
        lectura=est in ESTADOS_ESCRITURA or est == "activa" or est in ESTADOS_LECTURA,
        fin=lic.fin if lic else None,
        dias_restantes=dias,
        modulos=modulos_efectivos(lic, hoy) if est not in ("suspendida", "cancelada", "sin_licencia") else set(),
        limites=limites_efectivos(lic),
        licencia=lic,
        mensaje=mensajes.get(est, ""),
    )
