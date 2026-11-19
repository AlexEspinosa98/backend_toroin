"""Datos iniciales: planes, parámetros legales y el super admin.

    python -m app.seed            # catálogo + super admin (idempotente)
    python -m app.seed --demo     # además crea una empresa de demostración con datos genéricos
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import catalog
from .config import get_settings
from .db import SessionLocal
from .models import Empresa, Licencia, Obra, ParametroLegal, Plan, Trabajador, Usuario
from .security import hash_password


def sembrar_base(db: Session) -> None:
    for p in catalog.PLANES_DEFECTO:
        if not db.scalars(select(Plan).where(Plan.clave == p["clave"])).first():
            db.add(Plan(**p))
    if not db.scalars(select(ParametroLegal).where(ParametroLegal.pais == "CO", ParametroLegal.anio == 2026)).first():
        db.add(ParametroLegal(pais="CO", anio=2026, salario_minimo=1_750_905, aux_transporte=249_095,
                              extra={"fuente": "Decreto 1469 de 2025", "pct_salud": 0.04, "pct_pension": 0.04}))
    s = get_settings()
    correo = s.superadmin_email.lower()
    if not db.scalars(select(Usuario).where(Usuario.empresa_id.is_(None), Usuario.correo == correo)).first():
        db.add(Usuario(empresa_id=None, correo=correo, nombre="Super Admin", rol="super_admin",
                       password_hash=hash_password(s.superadmin_password)))
    db.commit()


def sembrar_demo(db: Session) -> None:
    if db.scalars(select(Empresa).where(Empresa.slug == "demo")).first():
        return
    e = Empresa(slug="demo", nombre="Constructora Demo S.A.S.", nit="900.000.000-0", nombre_app="Demo Obras")
    db.add(e)
    db.flush()
    plan = db.scalars(select(Plan).where(Plan.clave == "profesional")).one()
    db.add(Licencia(empresa_id=e.id, numero=1, plan_id=plan.id, tipo="periodo", inicio=date.today(),
                    fin=date.today() + timedelta(days=364)))
    db.add(Usuario(empresa_id=e.id, correo="admin@demo.com", nombre="Admin Demo", rol="admin",
                   password_hash=hash_password("Demo12345")))
    db.add_all([Obra(empresa_id=e.id, nombre="Torres del Parque", ciudad="Bogotá"),
                Obra(empresa_id=e.id, nombre="Conjunto Las Palmas", ciudad="Medellín")])
    db.add_all([
        Trabajador(empresa_id=e.id, nombre="Trabajador Ejemplo 1", cedula="1000000001", cargo="Oficial",
                   examen_vence=date.today() + timedelta(days=200), alturas_vence=date.today() + timedelta(days=120)),
        Trabajador(empresa_id=e.id, nombre="Trabajador Ejemplo 2", cedula="1000000002", cargo="Ayudante",
                   examen_vence=date.today() + timedelta(days=20), alturas_vence=date.today() + timedelta(days=300)),
    ])
    db.commit()


if __name__ == "__main__":
    with SessionLocal() as db:
        sembrar_base(db)
        if "--demo" in sys.argv:
            sembrar_demo(db)
    print("Listo. Super admin:", get_settings().superadmin_email)
