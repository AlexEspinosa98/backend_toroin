import os

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["SUPERADMIN_EMAIL"] = "root@plataforma.com"
os.environ["SUPERADMIN_PASSWORD"] = "RootPass123"
os.environ["JWT_SECRET"] = "secreto-de-pruebas-largo-y-seguro-123456"

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal, engine
from app.main import app
from app.models import Base
from app.seed import sembrar_base


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        sembrar_base(db)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(engine)


def login(c, correo, password, empresa=None):
    r = c.post("/auth/login", json={"correo": correo, "password": password, "empresa": empresa})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture()
def root(client):
    return login(client, "root@plataforma.com", "RootPass123")


def crear_empresa(c, root, slug="acme", plan="profesional", licencia=None, correo="admin@acme.com"):
    body = {
        "slug": slug, "nombre": f"Empresa {slug}",
        "admin": {"nombre": "Admin", "correo": correo},
        "licencia": licencia or {"plan": plan, "tipo": "periodo", "meses": 12},
    }
    r = c.post("/platform/empresas", json=body, headers=root)
    assert r.status_code == 201, r.text
    return r.json()
