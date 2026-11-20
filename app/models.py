"""Modelo de datos multiempresa. Todas las tablas de negocio llevan empresa_id."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _id() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Empresa(Base):
    __tablename__ = "empresas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    slug: Mapped[str] = mapped_column(String(63), unique=True, index=True)  # subdominio
    nombre: Mapped[str] = mapped_column(String(200))
    nit: Mapped[str] = mapped_column(String(40), default="")
    pais: Mapped[str] = mapped_column(String(2), default="CO")
    moneda: Mapped[str] = mapped_column(String(3), default="COP")
    zona_horaria: Mapped[str] = mapped_column(String(60), default="America/Bogota")
    estado: Mapped[str] = mapped_column(String(20), default="activa")  # activa | suspendida | cancelada
    # Marca blanca (las imágenes se guardan como data URI base64; migrar a storage más adelante)
    nombre_app: Mapped[str] = mapped_column(String(100), default="")
    color_primario: Mapped[str] = mapped_column(String(7), default="#19185A")
    color_secundario: Mapped[str] = mapped_column(String(7), default="#F59E0B")
    logo_claro: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_oscuro: Mapped[str | None] = mapped_column(Text, nullable=True)
    icono: Mapped[str | None] = mapped_column(Text, nullable=True)
    pie: Mapped[str] = mapped_column(String(300), default="")
    notas_internas: Mapped[str] = mapped_column(Text, default="")
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    usuarios: Mapped[list[Usuario]] = relationship(back_populates="empresa")


class Usuario(Base):
    __tablename__ = "usuarios"
    __table_args__ = (UniqueConstraint("empresa_id", "correo", name="uq_usuario_empresa_correo"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str | None] = mapped_column(ForeignKey("empresas.id"), nullable=True, index=True)  # null = plataforma
    correo: Mapped[str] = mapped_column(String(200), index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(200))
    rol: Mapped[str] = mapped_column(String(20))  # super_admin | soporte | admin | gerencia | rrhh | sst | residente
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    debe_cambiar_password: Mapped[bool] = mapped_column(Boolean, default=False)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ultimo_acceso: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    empresa: Mapped[Empresa | None] = relationship(back_populates="usuarios")


class Plan(Base):
    __tablename__ = "planes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    clave: Mapped[str] = mapped_column(String(40), unique=True)
    nombre: Mapped[str] = mapped_column(String(100))
    descripcion: Mapped[str] = mapped_column(String(300), default="")
    modulos: Mapped[list] = mapped_column(JSON, default=list)
    limites: Mapped[dict] = mapped_column(JSON, default=dict)  # max_usuarios, max_obras, max_trabajadores (null = sin límite)
    precio_ref: Mapped[float] = mapped_column(Float, default=0)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)


class Licencia(Base):
    """Historial de licencias de una empresa. La vigente es la de mayor `numero`."""

    __tablename__ = "licencias"
    __table_args__ = (UniqueConstraint("empresa_id", "numero", name="uq_licencia_numero"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    numero: Mapped[int] = mapped_column(Integer)
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("planes.id"), nullable=True)
    tipo: Mapped[str] = mapped_column(String(20))  # prueba | periodo | hasta_fecha | ilimitada
    inicio: Mapped[date] = mapped_column(Date)
    fin: Mapped[date | None] = mapped_column(Date, nullable=True)  # null = ilimitada
    gracia_dias: Mapped[int] = mapped_column(Integer, default=7)
    suspendida: Mapped[bool] = mapped_column(Boolean, default=False)
    cancelada: Mapped[bool] = mapped_column(Boolean, default=False)
    limites: Mapped[dict] = mapped_column(JSON, default=dict)  # sobrescribe los del plan
    notas: Mapped[str] = mapped_column(Text, default="")
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    plan: Mapped[Plan | None] = relationship()
    modulos: Mapped[list[LicenciaModulo]] = relationship(back_populates="licencia", cascade="all, delete-orphan")


class LicenciaModulo(Base):
    """Control fino por módulo sobre lo que da el plan (activar, desactivar, o fijar vigencia)."""

    __tablename__ = "licencia_modulos"
    __table_args__ = (UniqueConstraint("licencia_id", "modulo", name="uq_licmod"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    licencia_id: Mapped[str] = mapped_column(ForeignKey("licencias.id"), index=True)
    modulo: Mapped[str] = mapped_column(String(40))
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    fin: Mapped[date | None] = mapped_column(Date, nullable=True)

    licencia: Mapped[Licencia] = relationship(back_populates="modulos")


class Auditoria(Base):
    __tablename__ = "auditoria"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    usuario_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    usuario_correo: Mapped[str] = mapped_column(String(200), default="")
    accion: Mapped[str] = mapped_column(String(80))
    entidad: Mapped[str] = mapped_column(String(60), default="")
    entidad_id: Mapped[str] = mapped_column(String(60), default="")
    detalle: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(60), default="")
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class ParametroLegal(Base):
    """Constantes legales por país y año (SMMLV, auxilio de transporte...). Editables por el super admin."""

    __tablename__ = "parametros_legales"
    __table_args__ = (UniqueConstraint("pais", "anio", name="uq_param_pais_anio"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    pais: Mapped[str] = mapped_column(String(2))
    anio: Mapped[int] = mapped_column(Integer)
    salario_minimo: Mapped[float] = mapped_column(Float)
    aux_transporte: Mapped[float] = mapped_column(Float, default=0)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)


# ---------------- Tablas de negocio (tenant) ----------------
class Obra(Base):
    __tablename__ = "obras"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    ciudad: Mapped[str] = mapped_column(String(100), default="")
    estado: Mapped[str] = mapped_column(String(20), default="activa")
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Trabajador(Base):
    __tablename__ = "trabajadores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    cedula: Mapped[str] = mapped_column(String(30), default="")
    cargo: Mapped[str] = mapped_column(String(100), default="")
    telefono: Mapped[str] = mapped_column(String(40), default="")
    examen_fecha: Mapped[date | None] = mapped_column(Date, nullable=True)
    examen_vence: Mapped[date | None] = mapped_column(Date, nullable=True)
    alturas_fecha: Mapped[date | None] = mapped_column(Date, nullable=True)
    alturas_vence: Mapped[date | None] = mapped_column(Date, nullable=True)
    jornal: Mapped[float | None] = mapped_column(Float, nullable=True)
    notas: Mapped[list] = mapped_column(JSON, default=list)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Asignacion(Base):
    """Trabajador asignado a una obra desde fecha_ingreso (hasta fecha_retiro, si ya salió)."""
    __tablename__ = "asignaciones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    trabajador_id: Mapped[str] = mapped_column(ForeignKey("trabajadores.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    fecha_ingreso: Mapped[date] = mapped_column(Date)
    fecha_retiro: Mapped[date | None] = mapped_column(Date, nullable=True)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Asistencia(Base):
    """Marca diaria de un trabajador en una obra (una por obra, trabajador y fecha)."""
    __tablename__ = "asistencias"
    __table_args__ = (UniqueConstraint("obra_id", "trabajador_id", "fecha", name="uq_asistencia_dia"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    trabajador_id: Mapped[str] = mapped_column(ForeignKey("trabajadores.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    asistio: Mapped[bool] = mapped_column(Boolean)
    motivo: Mapped[str | None] = mapped_column(String(40), nullable=True)
    detalle: Mapped[str] = mapped_column(String(300), default="")
    registrado_por: Mapped[str] = mapped_column(String(200), default="")
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
