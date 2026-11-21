"""Catálogo de módulos de la plataforma y planes por defecto.

El catálogo vive en código (los módulos son funcionalidad); qué empresa tiene cuáles
se decide con planes + control por módulo en la licencia.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Modulo:
    clave: str
    nombre: str
    descripcion: str
    requiere: tuple[str, ...] = ()
    nucleo: bool = False  # siempre activo, no se vende por separado
    disponible: bool = True  # False = en el catálogo pero aún no construido
    subfunciones: tuple[str, ...] = field(default_factory=tuple)


MODULOS: dict[str, Modulo] = {m.clave: m for m in [
    Modulo("usuarios", "Usuarios y roles", "Administración de usuarios de la empresa", nucleo=True),
    Modulo("obras", "Obras", "Datos básicos de las obras", nucleo=True),
    Modulo("contrato", "Contrato y tablero de obra", "Contrato, actividades, avance físico vs. tiempo",
           requiere=("obras",), subfunciones=("lector_pdf", "curva_s", "pareto_atrasos")),
    Modulo("personal", "Personal", "Base de datos de trabajadores, vigencias e importación",
           subfunciones=("importar_excel",)),
    Modulo("asistencia", "Asistencia", "Asistencia diaria con habilitación SST",
           requiere=("personal", "obras")),
    Modulo("bitacora", "Bitácora y avance", "Tareas del día y avance por trabajador",
           requiere=("contrato", "personal"), subfunciones=("fotos", "tareas_por_trabajador")),
    Modulo("control_personal", "Control de personal", "Días asistidos, dominicales y festivos",
           requiere=("asistencia", "personal")),
    Modulo("pagos", "Pagos al personal", "Prenómina por jornal o destajo",
           requiere=("control_personal", "bitacora"), subfunciones=("destajo", "prenomina")),
    Modulo("finanzas", "Finanzas", "Ingresos, facturas, gastos y margen", requiere=("obras",)),
    Modulo("epp", "EPP / SST", "Solicitud, aprobación y entrega de dotación", requiere=("personal",)),
    Modulo("inventario", "Inventario y bodega", "Materiales por obra, kardex, traslados y consumo por actividad", requiere=("obras",), subfunciones=("kardex", "traslados", "stock_minimo")),
    Modulo("compras", "Compras y proveedores", "Requisiciones, órdenes de compra y recepción", requiere=("inventario",), subfunciones=("requisiciones", "ordenes", "recepcion")),
    Modulo("presupuesto", "Presupuesto vs. real", "Presupuesto de costos y desviación frente al costo real", requiere=("contrato",), subfunciones=("desviacion", "indice_costo")),
    Modulo("cronograma", "Cronograma / Gantt", "Programación por actividad, ruta crítica y atrasos", requiere=("contrato",), subfunciones=("gantt", "ruta_critica")),
]}

NUCLEO: frozenset[str] = frozenset(k for k, m in MODULOS.items() if m.nucleo)


def activables() -> list[str]:
    return [k for k, m in MODULOS.items() if m.disponible]


def dependientes_de(clave: str, activos: set[str]) -> list[str]:
    """Módulos activos que dependen (directa o indirectamente) de `clave`."""
    res: list[str] = []
    cambio = True
    objetivo = {clave}
    while cambio:
        cambio = False
        for k in activos:
            if k not in objetivo and any(r in objetivo for r in MODULOS[k].requiere):
                objetivo.add(k)
                res.append(k)
                cambio = True
    return res


def podar(activos: set[str]) -> set[str]:
    """Quita los módulos cuyas dependencias no están activas."""
    activos = set(activos)
    cambio = True
    while cambio:
        cambio = False
        for k in list(activos):
            if not all(r in activos for r in MODULOS[k].requiere):
                activos.discard(k)
                cambio = True
    return activos


PLANES_DEFECTO = [
    {"clave": "esencial", "nombre": "Esencial", "descripcion": "Obras, personal y asistencia",
     "modulos": ["personal", "asistencia", "epp"],
     "limites": {"max_usuarios": 5, "max_obras": 3, "max_trabajadores": 50}, "precio_ref": 0},
    {"clave": "profesional", "nombre": "Profesional", "descripcion": "Control de obra y de personal",
     "modulos": ["contrato", "personal", "asistencia", "bitacora", "control_personal", "epp"],
     "limites": {"max_usuarios": 15, "max_obras": 10, "max_trabajadores": 200}, "precio_ref": 0},
    {"clave": "completo", "nombre": "Completo", "descripcion": "Todos los módulos disponibles",
     "modulos": ["contrato", "personal", "asistencia", "bitacora", "control_personal", "pagos", "finanzas", "epp", "inventario", "compras", "presupuesto", "cronograma"],
     "limites": {"max_usuarios": None, "max_obras": None, "max_trabajadores": None}, "precio_ref": 0},
]

ROLES_EMPRESA = ("admin", "gerencia", "rrhh", "sst", "residente")
ROLES_PLATAFORMA = ("super_admin", "soporte")

# Qué roles ven cada módulo (el admin de empresa ve todo lo contratado)
ROLES_POR_MODULO: dict[str, tuple[str, ...]] = {
    "obras": ("admin", "gerencia", "residente"),
    "contrato": ("admin", "gerencia", "residente"),
    "personal": ("admin", "gerencia", "rrhh", "sst"),
    "asistencia": ("admin", "gerencia", "sst"),
    "bitacora": ("admin", "gerencia", "residente"),
    "control_personal": ("admin", "gerencia", "rrhh"),
    "pagos": ("admin", "gerencia", "rrhh"),
    "finanzas": ("admin", "gerencia"),
    "epp": ("admin", "gerencia", "sst"),
    "inventario": ("admin", "gerencia", "residente"),
    "compras": ("admin", "gerencia", "residente"),
    "presupuesto": ("admin", "gerencia"),
    "cronograma": ("admin", "gerencia", "residente"),
    "usuarios": ("admin",),
}
