"""Panel del super admin: empresas, marca, módulos, licencias, planes, suplantación y auditoría."""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import catalog
from ..audit import registrar
from ..db import get_db
from ..deps import plataforma
from ..licensing import licencia_vigente, limites_efectivos, modulos_efectivos, resolver
from ..models import Auditoria, Empresa, Licencia, LicenciaModulo, Obra, ParametroLegal, Plan, Trabajador, Usuario
from ..schemas import (
    EmpresaCrearIn, EmpresaEditarIn, ImpersonarIn, LicenciaIn, LicenciaPatchIn, MarcaIn,
    ModulosIn, ParametroIn, PlanIn, AdminIn,
)
from ..security import crear_token, hash_password, password_temporal

router = APIRouter(prefix="/platform", tags=["plataforma"])


# ---------- utilidades ----------
def _sumar_meses(d: date, m: int) -> date:
    y, mes = divmod(d.month - 1 + m, 12)
    y += d.year
    mes += 1
    return date(y, mes, min(d.day, calendar.monthrange(y, mes)[1]))


def _empresa(db: Session, id_: str) -> Empresa:
    e = db.get(Empresa, id_)
    if not e:
        raise HTTPException(404, "Empresa no encontrada")
    return e


def _plan_por_clave(db: Session, clave: str | None) -> Plan:
    if not clave:
        raise HTTPException(422, "Debe elegir un plan")
    p = db.scalars(select(Plan).where(Plan.clave == clave)).first()
    if not p:
        raise HTTPException(404, f"Plan «{clave}» no existe")
    return p


def _construir_licencia(db: Session, empresa: Empresa, d: LicenciaIn) -> Licencia:
    plan = _plan_por_clave(db, d.plan)
    inicio = d.inicio or date.today()
    if d.tipo == "prueba":
        fin = inicio + timedelta(days=d.dias_prueba or 14)
    elif d.tipo == "periodo":
        if not d.meses:
            raise HTTPException(422, "Indique los meses de la licencia")
        fin = _sumar_meses(inicio, d.meses) - timedelta(days=1)
    elif d.tipo == "hasta_fecha":
        if not d.fin or d.fin < inicio:
            raise HTTPException(422, "La fecha final debe ser posterior al inicio")
        fin = d.fin
    else:
        fin = None
    n = (db.scalar(select(func.max(Licencia.numero)).where(Licencia.empresa_id == empresa.id)) or 0) + 1
    lic = Licencia(empresa_id=empresa.id, numero=n, plan_id=plan.id, tipo=d.tipo, inicio=inicio, fin=fin,
                   gracia_dias=d.gracia_dias, limites=d.limites, notas=d.notas)
    db.add(lic)
    db.flush()
    db.refresh(lic)
    return lic


def _lic_out(l: Licencia | None) -> dict | None:
    if not l:
        return None
    return {
        "id": l.id, "numero": l.numero, "plan": l.plan.nombre if l.plan else None, "plan_clave": l.plan.clave if l.plan else None,
        "tipo": l.tipo, "inicio": l.inicio, "fin": l.fin, "gracia_dias": l.gracia_dias,
        "suspendida": l.suspendida, "cancelada": l.cancelada, "limites": l.limites, "notas": l.notas, "creado": l.creado,
    }


def _uso(db: Session, empresa_id: str) -> dict:
    def n(m, extra=None):
        q = select(func.count()).select_from(m).where(m.empresa_id == empresa_id)
        if extra is not None:
            q = q.where(extra)
        return db.scalar(q) or 0
    return {"usuarios": n(Usuario, Usuario.activo.is_(True)), "obras": n(Obra), "trabajadores": n(Trabajador)}


def _resumen(db: Session, e: Empresa) -> dict:
    est = resolver(db, e)
    return {
        "id": e.id, "slug": e.slug, "nombre": e.nombre, "nit": e.nit, "pais": e.pais, "estado_cuenta": e.estado,
        "licencia_estado": est.estado, "tipo": est.licencia.tipo if est.licencia else None,
        "plan": est.licencia.plan.nombre if est.licencia and est.licencia.plan else None,
        "fin": est.fin, "dias_restantes": est.dias_restantes,
        "modulos": sorted(k for k in est.modulos if k not in catalog.NUCLEO),
        "logo_claro": e.logo_claro, "color_primario": e.color_primario, "creado": e.creado,
    }


def _validar_modulos(claves) -> None:
    for k in claves:
        m = catalog.MODULOS.get(k)
        if not m:
            raise HTTPException(422, f"Módulo desconocido: {k}")
        if m.nucleo:
            raise HTTPException(422, f"«{m.nombre}» es núcleo y siempre está activo")
        if not m.disponible:
            raise HTTPException(422, f"«{m.nombre}» aún no está disponible")


def _dependencias_ok(crudo: set[str]) -> None:
    for k in crudo:
        faltan = [catalog.MODULOS[r].nombre for r in catalog.MODULOS[k].requiere if r not in crudo]
        if faltan:
            raise HTTPException(409, f"«{catalog.MODULOS[k].nombre}» requiere: {', '.join(faltan)}")


def _crear_admin(db: Session, empresa: Empresa, a: AdminIn) -> tuple[Usuario, str]:
    clave = password_temporal()
    correo = a.correo.lower()
    u = db.scalars(select(Usuario).where(Usuario.empresa_id == empresa.id, Usuario.correo == correo)).first()
    if u:
        u.rol, u.activo, u.nombre = "admin", True, a.nombre
    else:
        u = Usuario(empresa_id=empresa.id, correo=correo, nombre=a.nombre, rol="admin", password_hash="")
        db.add(u)
    u.password_hash = hash_password(clave)
    u.debe_cambiar_password = True
    db.flush()
    return u, clave


# ---------- catálogo y dashboard ----------
@router.get("/catalogo")
def catalogo(_=Depends(plataforma)):
    return [
        {"clave": m.clave, "nombre": m.nombre, "descripcion": m.descripcion, "requiere": list(m.requiere),
         "nucleo": m.nucleo, "disponible": m.disponible, "subfunciones": list(m.subfunciones)}
        for m in catalog.MODULOS.values()
    ]


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _=Depends(plataforma)):
    empresas = db.scalars(select(Empresa)).all()
    conteo: dict[str, int] = {}
    por_vencer = []
    for e in empresas:
        r = _resumen(db, e)
        conteo[r["licencia_estado"]] = conteo.get(r["licencia_estado"], 0) + 1
        if r["licencia_estado"] in ("por_vencer", "gracia", "prueba") and r["dias_restantes"] is not None:
            por_vencer.append({k: r[k] for k in ("id", "nombre", "slug", "licencia_estado", "fin", "dias_restantes")})
    por_vencer.sort(key=lambda x: x["dias_restantes"])
    return {
        "empresas": len(empresas), "por_estado": conteo, "por_vencer": por_vencer[:20],
        "usuarios": db.scalar(select(func.count()).select_from(Usuario).where(Usuario.empresa_id.is_not(None))) or 0,
        "obras": db.scalar(select(func.count()).select_from(Obra)) or 0,
        "trabajadores": db.scalar(select(func.count()).select_from(Trabajador)) or 0,
    }


# ---------- empresas ----------
@router.get("/empresas")
def listar_empresas(db: Session = Depends(get_db), _=Depends(plataforma)):
    return [_resumen(db, e) for e in db.scalars(select(Empresa).order_by(Empresa.nombre))]


@router.post("/empresas", status_code=201)
def crear_empresa(datos: EmpresaCrearIn, request: Request, db: Session = Depends(get_db), me: Usuario = Depends(plataforma)):
    if db.scalars(select(Empresa).where(Empresa.slug == datos.slug)).first():
        raise HTTPException(409, "Ese subdominio ya está en uso")
    e = Empresa(slug=datos.slug, nombre=datos.nombre, nit=datos.nit, pais=datos.pais.upper(),
                moneda=datos.moneda.upper(), zona_horaria=datos.zona_horaria, **datos.marca.model_dump())
    db.add(e)
    db.flush()
    lic = _construir_licencia(db, e, datos.licencia)
    admin, clave = _crear_admin(db, e, datos.admin)
    registrar(db, accion="empresa.crear", usuario=me, empresa_id=e.id, entidad="empresa", entidad_id=e.id,
              detalle={"plan": datos.licencia.plan, "tipo": lic.tipo}, request=request)
    db.commit()
    return {"empresa": _resumen(db, e), "admin": {"correo": admin.correo, "password_temporal": clave}}


@router.get("/empresas/{id_}")
def detalle_empresa(id_: str, db: Session = Depends(get_db), _=Depends(plataforma)):
    e = _empresa(db, id_)
    est = resolver(db, e)
    lic = est.licencia
    controles = {m.modulo: m for m in lic.modulos} if lic else {}
    plan_mods = set(lic.plan.modulos) if lic and lic.plan else set()
    modulos = []
    for m in catalog.MODULOS.values():
        c = controles.get(m.clave)
        modulos.append({
            "clave": m.clave, "nombre": m.nombre, "descripcion": m.descripcion, "requiere": list(m.requiere),
            "nucleo": m.nucleo, "disponible": m.disponible, "en_plan": m.clave in plan_mods,
            "control": {"activo": c.activo, "inicio": c.inicio, "fin": c.fin} if c else None,
            "activo": m.clave in est.modulos,
        })
    historial = db.scalars(select(Licencia).where(Licencia.empresa_id == e.id).order_by(Licencia.numero.desc())).all()
    usuarios = db.scalars(select(Usuario).where(Usuario.empresa_id == e.id).order_by(Usuario.nombre)).all()
    return {
        "id": e.id, "slug": e.slug, "nombre": e.nombre, "nit": e.nit, "pais": e.pais, "moneda": e.moneda,
        "zona_horaria": e.zona_horaria, "estado_cuenta": e.estado, "notas_internas": e.notas_internas, "creado": e.creado,
        "marca": {k: getattr(e, k) for k in ("nombre_app", "color_primario", "color_secundario", "logo_claro", "logo_oscuro", "icono", "pie")},
        "licencia": {**(_lic_out(lic) or {}), "estado": est.estado, "mensaje": est.mensaje, "dias_restantes": est.dias_restantes,
                     "limites_efectivos": limites_efectivos(lic)},
        "historial": [_lic_out(l) for l in historial],
        "modulos": modulos,
        "uso": _uso(db, e.id),
        "usuarios": [{"id": u.id, "nombre": u.nombre, "correo": u.correo, "rol": u.rol, "activo": u.activo, "ultimo_acceso": u.ultimo_acceso} for u in usuarios],
    }


@router.patch("/empresas/{id_}")
def editar_empresa(id_: str, datos: EmpresaEditarIn, request: Request, db: Session = Depends(get_db), me: Usuario = Depends(plataforma)):
    e = _empresa(db, id_)
    cambios = datos.model_dump(exclude_none=True)
    for k, v in cambios.items():
        setattr(e, k, v.upper() if k in ("pais", "moneda") else v)
    registrar(db, accion="empresa.editar", usuario=me, empresa_id=e.id, entidad="empresa", entidad_id=e.id, detalle=cambios, request=request)
    db.commit()
    return _resumen(db, e)


@router.put("/empresas/{id_}/marca")
def marca_empresa(id_: str, datos: MarcaIn, request: Request, db: Session = Depends(get_db), me: Usuario = Depends(plataforma)):
    e = _empresa(db, id_)
    for k, v in datos.model_dump().items():
        setattr(e, k, v)
    registrar(db, accion="marca.actualizar", usuario=me, empresa_id=e.id, entidad="empresa", entidad_id=e.id, request=request)
    db.commit()
    return {"ok": True}


def _cambiar_estado(db: Session, id_: str, estado: str, accion: str, request: Request, me: Usuario) -> dict:
    e = _empresa(db, id_)
    e.estado = estado
    registrar(db, accion=accion, usuario=me, empresa_id=e.id, entidad="empresa", entidad_id=e.id, request=request)
    db.commit()
    return _resumen(db, e)


@router.post("/empresas/{id_}/suspender")
def suspender(id_: str, request: Request, db: Session = Depends(get_db), me: Usuario = Depends(plataforma)):
    return _cambiar_estado(db, id_, "suspendida", "empresa.suspender", request, me)


@router.post("/empresas/{id_}/reactivar")
def reactivar(id_: str, request: Request, db: Session = Depends(get_db), me: Usuario = Depends(plataforma)):
    return _cambiar_estado(db, id_, "activa", "empresa.reactivar", request, me)


@router.post("/empresas/{id_}/admin")
def admin_empresa(id_: str, datos: AdminIn, request: Request, db: Session = Depends(get_db), me: Usuario = Depends(plataforma)):
    """Crea el administrador de la empresa, o reinicia su clave si el correo ya existe."""
    e = _empresa(db, id_)
    u, clave = _crear_admin(db, e, datos)
    registrar(db, accion="empresa.admin", usuario=me, empresa_id=e.id, entidad="usuario", entidad_id=u.id, request=request)
    db.commit()
    return {"correo": u.correo, "password_temporal": clave}


# ---------- licencias y módulos ----------
@router.post("/empresas/{id_}/licencias", status_code=201)
def nueva_licencia(id_: str, datos: LicenciaIn, request: Request, db: Session = Depends(get_db), me: Usuario = Depends(plataforma)):
    """Crea una nueva licencia (renovación, cambio de plan o de tipo). La anterior queda en el historial."""
    e = _empresa(db, id_)
    previa = licencia_vigente(db, e.id)
    lic = _construir_licencia(db, e, datos)
    if previa and not datos.limites:
        lic.limites = dict(previa.limites or {})
    if previa:  # conserva los controles por módulo que sigan vigentes
        for ov in previa.modulos:
            if ov.fin is None or ov.fin >= date.today():
                lic.modulos.append(LicenciaModulo(modulo=ov.modulo, activo=ov.activo, inicio=ov.inicio, fin=ov.fin))
    registrar(db, accion="licencia.nueva", usuario=me, empresa_id=e.id, entidad="licencia", entidad_id=lic.id,
              detalle={"plan": datos.plan, "tipo": datos.tipo, "fin": str(lic.fin)}, request=request)
    db.commit()
    return _lic_out(lic)


@router.patch("/empresas/{id_}/licencia")
def editar_licencia(id_: str, datos: LicenciaPatchIn, request: Request, db: Session = Depends(get_db), me: Usuario = Depends(plataforma)):
    e = _empresa(db, id_)
    lic = licencia_vigente(db, e.id)
    if not lic:
        raise HTTPException(404, "La empresa no tiene licencia")
    cambios = datos.model_dump(exclude_none=True)
    if "fin" in cambios:
        if lic.tipo == "ilimitada":
            raise HTTPException(409, "Una licencia ilimitada no tiene fecha final; cree una nueva")
        if cambios["fin"] < lic.inicio:
            raise HTTPException(422, "La fecha final no puede ser anterior al inicio")
    for k, v in cambios.items():
        setattr(lic, k, v)
    registrar(db, accion="licencia.editar", usuario=me, empresa_id=e.id, entidad="licencia", entidad_id=lic.id,
              detalle={k: str(v) for k, v in cambios.items()}, request=request)
    db.commit()
    return _lic_out(lic)


@router.put("/empresas/{id_}/modulos")
def controlar_modulos(id_: str, datos: ModulosIn, request: Request, db: Session = Depends(get_db), me: Usuario = Depends(plataforma)):
    """Activa, desactiva o fija vigencia por módulo (None = volver a lo que dice el plan)."""
    e = _empresa(db, id_)
    lic = licencia_vigente(db, e.id)
    if not lic:
        raise HTTPException(409, "Cree primero una licencia")
    _validar_modulos(datos.modulos.keys())
    actuales = {ov.modulo: ov for ov in lic.modulos}
    for clave, c in datos.modulos.items():
        ov = actuales.get(clave)
        if c is None:
            if ov:
                lic.modulos.remove(ov)
        else:
            if c.inicio and c.fin and c.fin < c.inicio:
                raise HTTPException(422, "La vigencia del módulo es inválida")
            if ov:
                ov.activo, ov.inicio, ov.fin = c.activo, c.inicio, c.fin
            else:
                lic.modulos.append(LicenciaModulo(modulo=clave, activo=c.activo, inicio=c.inicio, fin=c.fin))
    db.flush()
    try:
        _dependencias_ok(modulos_efectivos(lic, date.today(), podar=False))
    except HTTPException:
        db.rollback()
        raise
    registrar(db, accion="modulos.actualizar", usuario=me, empresa_id=e.id, entidad="licencia", entidad_id=lic.id,
              detalle={k: (v.model_dump(mode="json") if v else None) for k, v in datos.modulos.items()}, request=request)
    db.commit()
    return {"modulos": sorted(k for k in modulos_efectivos(lic, date.today()) if k not in catalog.NUCLEO)}


@router.get("/empresas/{id_}/uso")
def uso_empresa(id_: str, db: Session = Depends(get_db), _=Depends(plataforma)):
    e = _empresa(db, id_)
    return {"uso": _uso(db, e.id), "limites": resolver(db, e).limites}


# ---------- suplantación ----------
@router.post("/empresas/{id_}/impersonar")
def impersonar(id_: str, datos: ImpersonarIn, request: Request, db: Session = Depends(get_db), me: Usuario = Depends(plataforma)):
    """Token temporal para ver la app como la ve la empresa. Solo lectura por defecto y siempre auditado."""
    e = _empresa(db, id_)
    exp = datetime.now(timezone.utc) + timedelta(minutes=60)
    token = crear_token(me.id, me.rol, e.id, {"imp": True, "ro": datos.solo_lectura, "exp": exp})
    registrar(db, accion="suplantacion.iniciar", usuario=me, empresa_id=e.id, entidad="empresa", entidad_id=e.id,
              detalle={"motivo": datos.motivo, "solo_lectura": datos.solo_lectura}, request=request)
    db.commit()
    return {"token": token, "expira": exp}


# ---------- planes ----------
def _plan_out(p: Plan) -> dict:
    return {"id": p.id, "clave": p.clave, "nombre": p.nombre, "descripcion": p.descripcion, "modulos": p.modulos,
            "limites": p.limites, "precio_ref": p.precio_ref, "activo": p.activo}


def _validar_plan(d: PlanIn) -> None:
    _validar_modulos(d.modulos)
    _dependencias_ok(set(d.modulos) | set(catalog.NUCLEO))


@router.get("/planes")
def listar_planes(db: Session = Depends(get_db), _=Depends(plataforma)):
    return [_plan_out(p) for p in db.scalars(select(Plan).order_by(Plan.precio_ref, Plan.nombre))]


@router.post("/planes", status_code=201)
def crear_plan(datos: PlanIn, request: Request, db: Session = Depends(get_db), me: Usuario = Depends(plataforma)):
    if db.scalars(select(Plan).where(Plan.clave == datos.clave)).first():
        raise HTTPException(409, "Ya existe un plan con esa clave")
    _validar_plan(datos)
    p = Plan(**datos.model_dump())
    db.add(p)
    db.flush()
    registrar(db, accion="plan.crear", usuario=me, entidad="plan", entidad_id=p.id, request=request)
    db.commit()
    return _plan_out(p)


@router.put("/planes/{id_}")
def editar_plan(id_: str, datos: PlanIn, request: Request, db: Session = Depends(get_db), me: Usuario = Depends(plataforma)):
    p = db.get(Plan, id_)
    if not p:
        raise HTTPException(404, "Plan no encontrado")
    _validar_plan(datos)
    for k, v in datos.model_dump().items():
        setattr(p, k, v)
    registrar(db, accion="plan.editar", usuario=me, entidad="plan", entidad_id=p.id, request=request)
    db.commit()
    return _plan_out(p)


# ---------- parámetros legales ----------
@router.get("/parametros-legales")
def listar_parametros(db: Session = Depends(get_db), _=Depends(plataforma)):
    return [{"pais": p.pais, "anio": p.anio, "salario_minimo": p.salario_minimo, "aux_transporte": p.aux_transporte, "extra": p.extra}
            for p in db.scalars(select(ParametroLegal).order_by(ParametroLegal.pais, ParametroLegal.anio.desc()))]


@router.put("/parametros-legales")
def guardar_parametro(datos: ParametroIn, request: Request, db: Session = Depends(get_db), me: Usuario = Depends(plataforma)):
    pais = datos.pais.upper()
    p = db.scalars(select(ParametroLegal).where(ParametroLegal.pais == pais, ParametroLegal.anio == datos.anio)).first()
    if not p:
        p = ParametroLegal(pais=pais, anio=datos.anio, salario_minimo=datos.salario_minimo)
        db.add(p)
    p.salario_minimo, p.aux_transporte, p.extra = datos.salario_minimo, datos.aux_transporte, datos.extra
    registrar(db, accion="parametros.guardar", usuario=me, entidad="parametro_legal", entidad_id=f"{pais}-{datos.anio}", request=request)
    db.commit()
    return {"ok": True}


# ---------- auditoría ----------
@router.get("/auditoria")
def auditoria(empresa_id: str | None = None, limite: int = 100, db: Session = Depends(get_db), _=Depends(plataforma)):
    q = select(Auditoria).order_by(Auditoria.fecha.desc()).limit(min(max(limite, 1), 500))
    if empresa_id:
        q = q.where(Auditoria.empresa_id == empresa_id)
    return [{"id": a.id, "fecha": a.fecha, "usuario": a.usuario_correo, "accion": a.accion, "entidad": a.entidad,
             "entidad_id": a.entidad_id, "detalle": a.detalle, "empresa_id": a.empresa_id, "ip": a.ip}
            for a in db.scalars(q)]
