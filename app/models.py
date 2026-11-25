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
    # Permisos que define el admin de la empresa. None = los que trae su rol por defecto.
    modulos: Mapped[list | None] = mapped_column(JSON, nullable=True)
    alertas: Mapped[list | None] = mapped_column(JSON, nullable=True)  # módulos de los que recibe alertas; None = los que ve

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
    ingresos_proyectados: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    modalidad: Mapped[str] = mapped_column(String(10), default="")  # "" = la de la empresa | jornal | destajo
    jornal: Mapped[float | None] = mapped_column(Float, nullable=True)  # jornal propio en esta obra (si no, el del trabajador)
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


class Contrato(Base):
    """Contrato de una obra (uno por obra), tal como lo entrega el contratante."""
    __tablename__ = "contratos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), unique=True)
    numero: Mapped[str] = mapped_column(String(60), default="")
    contratante: Mapped[str] = mapped_column(String(200), default="")
    nit_contratante: Mapped[str] = mapped_column(String(40), default="")
    contratista: Mapped[str] = mapped_column(String(200), default="")
    proyecto: Mapped[str] = mapped_column(String(300), default="")
    descripcion: Mapped[str] = mapped_column(Text, default="")
    tipo: Mapped[str] = mapped_column(String(80), default="")
    fecha_inicio: Mapped[date] = mapped_column(Date)
    fecha_fin: Mapped[date] = mapped_column(Date)
    valor_total: Mapped[float] = mapped_column(Float, default=0)
    valor_antes_aiu: Mapped[float] = mapped_column(Float, default=0)
    aiu: Mapped[float] = mapped_column(Float, default=0)
    pct_administracion: Mapped[float] = mapped_column(Float, default=0)
    pct_imprevistos: Mapped[float] = mapped_column(Float, default=0)
    pct_utilidad: Mapped[float] = mapped_column(Float, default=0)
    iva_utilidad: Mapped[float] = mapped_column(Float, default=0)
    pct_rete_garantia: Mapped[float] = mapped_column(Float, default=0)
    rete_garantia: Mapped[float] = mapped_column(Float, default=0)
    valor_neto: Mapped[float] = mapped_column(Float, default=0)
    forma_pago: Mapped[str] = mapped_column(String(120), default="")
    pagos: Mapped[list] = mapped_column(JSON, default=list)
    advertencias: Mapped[list] = mapped_column(JSON, default=list)
    archivo: Mapped[str] = mapped_column(String(200), default="")
    actualizado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Actividad(Base):
    __tablename__ = "actividades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    item: Mapped[str] = mapped_column(String(30), default="")
    grupo: Mapped[int] = mapped_column(Integer, default=0)
    capitulo: Mapped[str] = mapped_column(String(120), default="")
    nombre: Mapped[str] = mapped_column(String(300))
    ubicacion: Mapped[str] = mapped_column(String(60), default="")
    um: Mapped[str] = mapped_column(String(12), default="UND")
    cantidad: Mapped[float] = mapped_column(Float)
    valor_unitario: Mapped[float] = mapped_column(Float, default=0)
    valor_total: Mapped[float] = mapped_column(Float, default=0)
    tarifa: Mapped[float | None] = mapped_column(Float, nullable=True)  # destajo por unidad que se paga a la cuadrilla
    # Subítems: porción del ítem del contrato medida en las unidades con que la empresa le paga al trabajador.
    # padre_id vacío = ítem del contrato. equivale = cuánto del padre representa, en la unidad del padre
    # (ej. «Apartamento 101» = 1 APTO = 76 M2 del ítem). aporta_avance=False: solo carga costo al rubro (acarreo, andamio).
    padre_id: Mapped[str] = mapped_column(String(36), default="", index=True)
    equivale: Mapped[float] = mapped_column(Float, default=0)
    aporta_avance: Mapped[bool] = mapped_column(Boolean, default=True)
    advertencias: Mapped[list] = mapped_column(JSON, default=list)


class RegistroBitacora(Base):
    __tablename__ = "bitacora"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    actividad_id: Mapped[str] = mapped_column(ForeignKey("actividades.id"), index=True)
    cantidad: Mapped[float] = mapped_column(Float, default=0)
    personas: Mapped[int] = mapped_column(Integer, default=0)
    trabajadores: Mapped[list] = mapped_column(JSON, default=list)
    por_trabajador: Mapped[dict] = mapped_column(JSON, default=dict)
    horas_atraso: Mapped[float] = mapped_column(Float, default=0)
    motivo_atraso: Mapped[str | None] = mapped_column(String(30), nullable=True)
    acciones: Mapped[str] = mapped_column(Text, default="")
    observaciones: Mapped[str] = mapped_column(Text, default="")
    fotos: Mapped[list] = mapped_column(JSON, default=list)
    registrado_por: Mapped[str] = mapped_column(String(200), default="")
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Tarea(Base):
    """Actividad asignada a un trabajador desde `desde` hasta `hasta` (o hasta nuevo aviso). actividad_id vacío = sin tarea."""
    __tablename__ = "tareas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    trabajador_id: Mapped[str] = mapped_column(ForeignKey("trabajadores.id"), index=True)
    actividad_id: Mapped[str] = mapped_column(String(36), default="")
    desde: Mapped[date] = mapped_column(Date)
    hasta: Mapped[date | None] = mapped_column(Date, nullable=True)
    instruccion: Mapped[str] = mapped_column(String(300), default="")
    asignado_por: Mapped[str] = mapped_column(String(200), default="")
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Foto(Base):
    """Foto de evidencia en base64 (data URI), comprimida por el cliente."""
    __tablename__ = "fotos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    datos: Mapped[str] = mapped_column(Text)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ConfigEmpresa(Base):
    """Reglas de nómina y calendario de cada empresa (una fila por empresa)."""
    __tablename__ = "config_empresa"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), unique=True)
    semana: Mapped[str] = mapped_column(String(2), default="LS")  # LS lunes-sábado | LV lunes-viernes
    regla_dominical: Mapped[str] = mapped_column(String(10), default="legal")  # legal | literal
    modalidad_pago: Mapped[str] = mapped_column(String(10), default="jornal")  # jornal | destajo
    jornal_base: Mapped[float] = mapped_column(Float, default=58364)
    factor_prestacional: Mapped[float] = mapped_column(Float, default=1.0)
    festivos_extra: Mapped[list] = mapped_column(JSON, default=list)  # fechas ISO no laborables adicionales


class SolicitudEPP(Base):
    __tablename__ = "solicitudes_epp"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    trabajador_id: Mapped[str] = mapped_column(ForeignKey("trabajadores.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    elemento: Mapped[str] = mapped_column(String(80))
    talla: Mapped[str] = mapped_column(String(20), default="")
    cantidad: Mapped[int] = mapped_column(Integer, default=1)
    motivo: Mapped[str] = mapped_column(String(40), default="Ingreso a obra")
    estado: Mapped[str] = mapped_column(String(20), default="Solicitado")  # Solicitado | Aprobado | Entregado | Rechazado
    observaciones: Mapped[str] = mapped_column(String(300), default="")
    solicitado_por: Mapped[str] = mapped_column(String(200), default="")
    fecha_solicitud: Mapped[date] = mapped_column(Date)
    fecha_aprobacion: Mapped[date | None] = mapped_column(Date, nullable=True)
    fecha_entrega: Mapped[date | None] = mapped_column(Date, nullable=True)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Factura(Base):
    __tablename__ = "facturas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    numero: Mapped[str] = mapped_column(String(40), default="")
    fecha: Mapped[date] = mapped_column(Date)
    concepto: Mapped[str] = mapped_column(String(200), default="")
    valor: Mapped[float] = mapped_column(Float)
    actividad_id: Mapped[str] = mapped_column(String(36), default="", index=True)  # ítem del contrato al que se imputa
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Gasto(Base):
    __tablename__ = "gastos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date)
    categoria: Mapped[str] = mapped_column(String(60), default="Otros")
    descripcion: Mapped[str] = mapped_column(String(200), default="")
    valor: Mapped[float] = mapped_column(Float)
    origen: Mapped[str] = mapped_column(String(60), default="")  # "oc:<id>" si lo generó una compra recibida
    actividad_id: Mapped[str] = mapped_column(String(36), default="", index=True)  # ítem del contrato al que se imputa
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------- Presupuesto vs. real ----------------
class PresupuestoItem(Base):
    __tablename__ = "presupuesto_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    categoria: Mapped[str] = mapped_column(String(40))
    concepto: Mapped[str] = mapped_column(String(200), default="")
    valor: Mapped[float] = mapped_column(Float, default=0)
    actividad_id: Mapped[str] = mapped_column(String(36), default="")  # opcional: costo de mano de obra de una actividad


# ---------------- Cronograma ----------------
class CronogramaTarea(Base):
    __tablename__ = "cronograma_tareas"
    __table_args__ = (UniqueConstraint("obra_id", "actividad_id", name="uq_cronograma_actividad"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    actividad_id: Mapped[str] = mapped_column(String(36))
    inicio: Mapped[date] = mapped_column(Date)
    fin: Mapped[date] = mapped_column(Date)
    predecesoras: Mapped[list] = mapped_column(JSON, default=list)  # ids de actividad que deben terminar antes
    notas: Mapped[str] = mapped_column(String(300), default="")


# ---------------- Inventario y bodega ----------------
class Material(Base):
    __tablename__ = "materiales"
    __table_args__ = (UniqueConstraint("empresa_id", "nombre", name="uq_material_nombre"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    unidad: Mapped[str] = mapped_column(String(12), default="UND")
    categoria: Mapped[str] = mapped_column(String(60), default="")
    stock_minimo: Mapped[float] = mapped_column(Float, default=0)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MovimientoInv(Base):
    """Kardex: cantidad con signo (entrada +, salida −). El saldo de una bodega es la suma de sus movimientos."""
    __tablename__ = "movimientos_inv"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materiales.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    fecha: Mapped[date] = mapped_column(Date, index=True)
    tipo: Mapped[str] = mapped_column(String(12))  # entrada | salida | ajuste | traslado
    cantidad: Mapped[float] = mapped_column(Float)
    costo_unitario: Mapped[float] = mapped_column(Float, default=0)
    actividad_id: Mapped[str] = mapped_column(String(36), default="")  # consumo por actividad (salidas)
    referencia: Mapped[str] = mapped_column(String(120), default="")
    detalle: Mapped[str] = mapped_column(String(300), default="")
    traslado_id: Mapped[str] = mapped_column(String(36), default="")
    registrado_por: Mapped[str] = mapped_column(String(200), default="")
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------- Compras y proveedores ----------------
class Proveedor(Base):
    __tablename__ = "proveedores"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    nombre: Mapped[str] = mapped_column(String(200))
    nit: Mapped[str] = mapped_column(String(40), default="")
    contacto: Mapped[str] = mapped_column(String(120), default="")
    telefono: Mapped[str] = mapped_column(String(40), default="")
    correo: Mapped[str] = mapped_column(String(200), default="")
    direccion: Mapped[str] = mapped_column(String(200), default="")
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Requisicion(Base):
    __tablename__ = "requisiciones"
    __table_args__ = (UniqueConstraint("empresa_id", "numero", name="uq_requisicion_numero"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    numero: Mapped[int] = mapped_column(Integer)
    fecha: Mapped[date] = mapped_column(Date)
    solicitante: Mapped[str] = mapped_column(String(200), default="")
    estado: Mapped[str] = mapped_column(String(20), default="Solicitada")  # Solicitada | Aprobada | Rechazada | Ordenada
    items: Mapped[list] = mapped_column(JSON, default=list)  # [{material_id, descripcion, unidad, cantidad}]
    observaciones: Mapped[str] = mapped_column(String(300), default="")
    resuelto_por: Mapped[str] = mapped_column(String(200), default="")
    fecha_resolucion: Mapped[date | None] = mapped_column(Date, nullable=True)
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OrdenCompra(Base):
    __tablename__ = "ordenes_compra"
    __table_args__ = (UniqueConstraint("empresa_id", "numero", name="uq_orden_numero"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_id)
    empresa_id: Mapped[str] = mapped_column(ForeignKey("empresas.id"), index=True)
    obra_id: Mapped[str] = mapped_column(ForeignKey("obras.id"), index=True)
    proveedor_id: Mapped[str] = mapped_column(ForeignKey("proveedores.id"), index=True)
    requisicion_id: Mapped[str] = mapped_column(String(36), default="")
    numero: Mapped[int] = mapped_column(Integer)
    fecha: Mapped[date] = mapped_column(Date)
    fecha_entrega: Mapped[date | None] = mapped_column(Date, nullable=True)
    estado: Mapped[str] = mapped_column(String(20), default="Borrador")  # Borrador | Emitida | Parcial | Recibida | Cancelada
    items: Mapped[list] = mapped_column(JSON, default=list)  # [{material_id, descripcion, unidad, cantidad, precio_unitario, recibido}]
    iva_pct: Mapped[float] = mapped_column(Float, default=19)
    condiciones: Mapped[str] = mapped_column(String(300), default="")
    observaciones: Mapped[str] = mapped_column(String(300), default="")
    creado_por: Mapped[str] = mapped_column(String(200), default="")
    creado: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
