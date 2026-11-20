"""Comprueba que la BD tiene el esquema esperado y los datos base. Sale con código 1 si algo falta."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import inspect, select

from app.db import engine, SessionLocal
from app.models import Base, Plan, Usuario

faltan = set(Base.metadata.tables) - set(inspect(engine).get_table_names())
if faltan:
    sys.exit(f"Faltan tablas: {sorted(faltan)}")
with SessionLocal() as db:
    if not db.scalars(select(Plan)).first():
        sys.exit("No hay planes sembrados")
    if not db.scalars(select(Usuario).where(Usuario.empresa_id.is_(None))).first():
        sys.exit("No hay super admin")
print("BD OK:", len(Base.metadata.tables), "tablas, planes y super admin presentes")
