from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])$")
SLUGS_RESERVADOS = {"www", "api", "admin", "app", "platform", "plataforma", "superadmin", "mail", "static",
                   "login", "obras", "personal", "usuarios", "marca", "licencia", "cambiar-password", "suplantar", "epp", "pagos", "finanzas", "control-personal", "ajustes", "asistencia", "contrato", "bitacora", "config", "festivos", "favicon", "_next"}
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
MAX_IMAGEN = 400_000  # caracteres del data URI (~300 KB)
TIPOS_IMAGEN = ("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,", "data:image/svg+xml;base64,")


def _validar_imagen(v: str | None) -> str | None:
    if v is None or v == "":
        return None
    if not v.startswith(TIPOS_IMAGEN):
        raise ValueError("La imagen debe ser PNG, JPG, WEBP o SVG en base64 (data URI)")
    if len(v) > MAX_IMAGEN:
        raise ValueError("La imagen es muy pesada (máx. ~300 KB)")
    return v


# ---------- Auth ----------
class LoginIn(BaseModel):
    correo: EmailStr
    password: str
    empresa: str | None = None  # slug; vacío = plataforma


class CambiarPasswordIn(BaseModel):
    actual: str
    nueva: str = Field(min_length=8, max_length=72)


# ---------- Marca ----------
class MarcaIn(BaseModel):
    nombre_app: str = Field(default="", max_length=100)
    color_primario: str = "#19185A"
    color_secundario: str = "#F59E0B"
    logo_claro: str | None = None
    logo_oscuro: str | None = None
    icono: str | None = None
    pie: str = Field(default="", max_length=300)

    @field_validator("color_primario", "color_secundario")
    @classmethod
    def _color(cls, v: str) -> str:
        if not COLOR_RE.match(v):
            raise ValueError("Color inválido, use #RRGGBB")
        return v

    @field_validator("logo_claro", "logo_oscuro", "icono")
    @classmethod
    def _img(cls, v: str | None) -> str | None:
        return _validar_imagen(v)


class MarcaOut(BaseModel):
    slug: str
    nombre: str
    nombre_app: str
    color_primario: str
    color_secundario: str
    logo_claro: str | None
    logo_oscuro: str | None
    icono: str | None
    pie: str


# ---------- Empresas (plataforma) ----------
class AdminIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=200)
    correo: EmailStr


class LicenciaIn(BaseModel):
    plan: str | None = None  # clave del plan
    tipo: Literal["prueba", "periodo", "hasta_fecha", "ilimitada"]
    inicio: date | None = None
    meses: int | None = Field(default=None, ge=1, le=120)  # para tipo=periodo
    fin: date | None = None  # para tipo=hasta_fecha
    dias_prueba: int | None = Field(default=None, ge=1, le=365)
    gracia_dias: int = Field(default=7, ge=0, le=90)
    limites: dict = Field(default_factory=dict)
    notas: str = ""


class EmpresaCrearIn(BaseModel):
    slug: str
    nombre: str = Field(min_length=2, max_length=200)
    nit: str = ""
    pais: str = Field(default="CO", min_length=2, max_length=2)
    moneda: str = Field(default="COP", min_length=3, max_length=3)
    zona_horaria: str = "America/Bogota"
    marca: MarcaIn = Field(default_factory=MarcaIn)
    admin: AdminIn
    licencia: LicenciaIn

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str) -> str:
        v = v.strip().lower()
        if not SLUG_RE.match(v) or v in SLUGS_RESERVADOS:
            raise ValueError("Subdominio inválido o reservado (3-63 caracteres: letras, números y guiones)")
        return v


class EmpresaEditarIn(BaseModel):
    nombre: str | None = None
    nit: str | None = None
    pais: str | None = None
    moneda: str | None = None
    zona_horaria: str | None = None
    notas_internas: str | None = None


class ModuloControlIn(BaseModel):
    activo: bool = True
    inicio: date | None = None
    fin: date | None = None


class ModulosIn(BaseModel):
    # clave -> control (None = quitar el control y volver a lo que dice el plan)
    modulos: dict[str, ModuloControlIn | None]


class LicenciaPatchIn(BaseModel):
    suspendida: bool | None = None
    cancelada: bool | None = None
    fin: date | None = None
    gracia_dias: int | None = Field(default=None, ge=0, le=90)
    limites: dict | None = None
    notas: str | None = None


class PlanIn(BaseModel):
    clave: str = Field(pattern=r"^[a-z0-9_-]{2,40}$")
    nombre: str
    descripcion: str = ""
    modulos: list[str] = []
    limites: dict = {}
    precio_ref: float = 0
    activo: bool = True


class ImpersonarIn(BaseModel):
    motivo: str = Field(min_length=5, max_length=300)
    solo_lectura: bool = True


class ParametroIn(BaseModel):
    pais: str = Field(min_length=2, max_length=2)
    anio: int = Field(ge=2000, le=2100)
    salario_minimo: float = Field(gt=0)
    aux_transporte: float = Field(default=0, ge=0)
    extra: dict = {}


# ---------- Empresa (admin) ----------
class UsuarioIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=200)
    correo: EmailStr
    rol: Literal["admin", "gerencia", "rrhh", "sst", "residente"]
    modulos: list[str] | None = None  # None = los de su rol
    alertas: list[str] | None = None  # None = alertas de los módulos que ve


class UsuarioPatchIn(BaseModel):
    nombre: str | None = None
    rol: Literal["admin", "gerencia", "rrhh", "sst", "residente"] | None = None
    activo: bool | None = None
    modulos: list[str] | None = None  # enviar null = volver a los de su rol
    alertas: list[str] | None = None


# ---------- Negocio ----------
class ObraIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=200)
    ciudad: str = ""
    estado: Literal["activa", "pausada", "terminada"] = "activa"


class TrabajadorIn(BaseModel):
    nombre: str = Field(min_length=2, max_length=200)
    cedula: str = ""
    cargo: str = ""
    telefono: str = ""
    examen_fecha: date | None = None
    examen_vence: date | None = None
    alturas_fecha: date | None = None
    alturas_vence: date | None = None
    jornal: float | None = Field(default=None, ge=0)
    notas: list[str] = []


class AsignacionIn(BaseModel):
    trabajador_id: str
    obra_id: str
    fecha_ingreso: date


class RetiroIn(BaseModel):
    fecha_retiro: date


class MarcaAsistenciaIn(BaseModel):
    trabajador_id: str
    asistio: bool
    motivo: str | None = None
    detalle: str = Field(default="", max_length=300)


class AsistenciaIn(BaseModel):
    obra_id: str
    fecha: date
    marcas: list[MarcaAsistenciaIn]


class ActividadIn(BaseModel):
    id: str | None = None
    item: str = ""
    grupo: int = 0
    capitulo: str = ""
    nombre: str = Field(min_length=1, max_length=300)
    ubicacion: str = ""
    um: str = Field(default="UND", max_length=12)
    cantidad: float = Field(gt=0)
    valor_unitario: float = Field(default=0, ge=0)
    tarifa: float | None = Field(default=None, ge=0)
    advertencias: list[str] = []


class PagoProgramadoIn(BaseModel):
    fecha: date
    valor: float
    concepto: str = ""


class ContratoIn(BaseModel):
    numero: str = ""
    contratante: str = ""
    nit_contratante: str = ""
    contratista: str = ""
    proyecto: str = ""
    descripcion: str = ""
    tipo: str = ""
    fecha_inicio: date
    fecha_fin: date
    valor_total: float = Field(default=0, ge=0)
    valor_antes_aiu: float = 0
    aiu: float = 0
    pct_administracion: float = 0
    pct_imprevistos: float = 0
    pct_utilidad: float = 0
    iva_utilidad: float = 0
    pct_rete_garantia: float = 0
    rete_garantia: float = 0
    valor_neto: float = 0
    forma_pago: str = ""
    pagos: list[PagoProgramadoIn] = []
    advertencias: list[str] = []
    archivo: str = ""
    actividades: list[ActividadIn] = Field(min_length=1)
    ingresos_proyectados: float | None = Field(default=None, ge=0)

    @field_validator("fecha_fin")
    @classmethod
    def _fechas(cls, v, info):
        if info.data.get("fecha_inicio") and v <= info.data["fecha_inicio"]:
            raise ValueError("La fecha de terminación debe ser posterior al inicio")
        return v


class RegistroBitacoraIn(BaseModel):
    obra_id: str
    fecha: date
    actividad_id: str
    cantidad: float = Field(ge=0)
    personas: int = Field(default=0, ge=0)
    trabajadores: list[str] = []
    por_trabajador: dict[str, float] = {}
    horas_atraso: float = Field(default=0, ge=0, le=24)
    motivo_atraso: str | None = None
    acciones: str = ""
    observaciones: str = ""
    fotos: list[str] = []


class AsignarTareasIn(BaseModel):
    obra_id: str
    trabajador_ids: list[str] = Field(min_length=1)
    actividad_id: str = ""
    desde: date
    hasta: date | None = None
    instruccion: str = Field(default="", max_length=300)


class FotoIn(BaseModel):
    obra_id: str
    datos: str


ELEMENTOS_EPP = ("Casco", "Botas de seguridad", "Guantes", "Gafas", "Protector auditivo", "Tapabocas / respirador", "Arnés", "Eslinga",
                 "Chaleco reflectivo", "Overol / dotación")
MOTIVOS_EPP = ("Ingreso a obra", "Reposición", "Desgaste", "Pérdida", "Daño")
CATEGORIAS_GASTO = ("Cortes de obra", "Compra de insumos", "Herramientas y equipos", "Transporte", "Otros")


class ConfigIn(BaseModel):
    semana: Literal["LS", "LV"] = "LS"
    regla_dominical: Literal["legal", "literal"] = "legal"
    modalidad_pago: Literal["jornal", "destajo"] = "jornal"
    jornal_base: float = Field(default=58364, gt=0)
    factor_prestacional: float = Field(default=1.0, ge=1, le=3)
    festivos_extra: list[date] = []


class SolicitudEPPIn(BaseModel):
    trabajador_id: str
    obra_id: str
    elemento: str = Field(min_length=1, max_length=80)
    talla: str = Field(default="", max_length=20)
    cantidad: int = Field(default=1, ge=1, le=100)
    motivo: str = "Ingreso a obra"
    observaciones: str = Field(default="", max_length=300)


class EstadoEPPIn(BaseModel):
    estado: Literal["Aprobado", "Entregado", "Rechazado"]


class FacturaIn(BaseModel):
    numero: str = Field(default="", max_length=40)
    fecha: date
    concepto: str = Field(default="", max_length=200)
    valor: float = Field(gt=0)


class GastoIn(BaseModel):
    fecha: date
    categoria: str = "Otros"
    descripcion: str = Field(default="", max_length=200)
    valor: float = Field(gt=0)


class IngresosIn(BaseModel):
    ingresos_proyectados: float | None = Field(default=None, ge=0)


class TarifaIn(BaseModel):
    tarifa: float | None = Field(default=None, ge=0)
