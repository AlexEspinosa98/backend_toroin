from datetime import date, timedelta

from .conftest import crear_empresa, login

HOY = date.today()


def _admin(client, root, plan="completo"):
    out = crear_empresa(client, root, plan=plan)
    return login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")


def _usuario(client, h, correo, rol, **extra):
    u = client.post("/empresa/usuarios", json={"nombre": "Persona", "correo": correo, "rol": rol, **extra}, headers=h)
    assert u.status_code == 201, u.text
    return u.json(), login(client, correo, u.json()["password_temporal"], "acme")


def test_por_defecto_usa_el_rol(client, root):
    h = _admin(client, root)
    u, hs = _usuario(client, h, "sst@acme.com", "sst")
    assert u["modulos"] is None and set(u["modulos_efectivos"]) == {"personal", "asistencia", "epp"}
    assert client.get("/finanzas", headers=hs).status_code == 403


def test_admin_da_y_quita_modulos(client, root):
    h = _admin(client, root)
    u, hs = _usuario(client, h, "sst@acme.com", "sst")
    # le da Finanzas (que su rol no trae) y le quita EPP
    r = client.patch(f"/empresa/usuarios/{u['id']}", json={"modulos": ["finanzas", "personal", "asistencia"]}, headers=h)
    assert r.status_code == 200 and set(r.json()["modulos_efectivos"]) == {"finanzas", "personal", "asistencia"}
    assert client.get("/finanzas", headers=hs).status_code == 200
    assert client.get("/epp", headers=hs).status_code == 403
    me = client.get("/auth/me", headers=hs).json()
    assert "finanzas" in me["modulos"] and "epp" not in me["modulos"]
    # al asignarlo expresamente puede usarlo completo (no solo consultar)
    obra = client.post("/obras", json={"nombre": "Torre"}, headers=h).json()
    assert client.post(f"/obras/{obra['id']}/gastos", json={"fecha": str(HOY), "categoria": "Transporte", "valor": 5000}, headers=hs).status_code == 201
    # volver a lo de su rol
    r = client.patch(f"/empresa/usuarios/{u['id']}", json={"modulos": None}, headers=h)
    assert r.json()["modulos"] is None
    assert client.get("/finanzas", headers=hs).status_code == 403 and client.get("/epp", headers=hs).status_code == 200


def test_dependencias_y_validaciones(client, root):
    h = _admin(client, root)
    u, _ = _usuario(client, h, "x@acme.com", "residente")
    # Asistencia necesita Personal: se agrega solo
    r = client.patch(f"/empresa/usuarios/{u['id']}", json={"modulos": ["asistencia"]}, headers=h).json()
    assert set(r["modulos"]) == {"asistencia", "personal"}
    # módulo inexistente o 'usuarios' no se puede asignar
    assert client.patch(f"/empresa/usuarios/{u['id']}", json={"modulos": ["usuarios"]}, headers=h).status_code == 422
    assert client.patch(f"/empresa/usuarios/{u['id']}", json={"alertas": ["inventado"]}, headers=h).status_code == 422
    # un usuario no administrador no puede cambiar permisos
    _, hr = _usuario(client, h, "y@acme.com", "gerencia")
    assert client.patch(f"/empresa/usuarios/{u['id']}", json={"modulos": []}, headers=hr).status_code == 403


def test_no_se_puede_dar_modulo_no_licenciado(client, root):
    h = _admin(client, root, plan="esencial")
    u, _ = _usuario(client, h, "x@acme.com", "gerencia")
    assert client.patch(f"/empresa/usuarios/{u['id']}", json={"modulos": ["finanzas"]}, headers=h).status_code == 422
    cat = client.get("/empresa/permisos", headers=h).json()
    assert "finanzas" not in {m["clave"] for m in cat["modulos"]} and {"personal", "epp"} <= {m["clave"] for m in cat["modulos"]}


def test_alertas_segun_lo_que_elige_el_admin(client, root):
    h = _admin(client, root)
    obra = client.post("/obras", json={"nombre": "Torre"}, headers=h).json()
    t = client.post("/personal", json={"nombre": "Vencido", "examen_fecha": "2024-01-01", "examen_vence": str(HOY - timedelta(days=5)),
                                       "alturas_fecha": "2024-01-01", "alturas_vence": str(HOY + timedelta(days=300))}, headers=h).json()
    client.post("/asignaciones", json={"trabajador_id": t["id"], "obra_id": obra["id"], "fecha_ingreso": str(HOY - timedelta(days=20))}, headers=h)
    client.post("/epp", json={"trabajador_id": t["id"], "obra_id": obra["id"], "elemento": "Casco"}, headers=h)
    u, hs = _usuario(client, h, "sst@acme.com", "sst")
    tipos = {a["tipo"] for a in client.get("/alertas", headers=hs).json()["alertas"]}
    assert "sst" in tipos and "epp" in tipos
    # solo quiere alertas de EPP
    client.patch(f"/empresa/usuarios/{u['id']}", json={"alertas": ["epp"]}, headers=h)
    tipos = {a["tipo"] for a in client.get("/alertas", headers=hs).json()["alertas"]}
    assert tipos == {"epp"}
    # sin alertas
    client.patch(f"/empresa/usuarios/{u['id']}", json={"alertas": []}, headers=h)
    assert client.get("/alertas", headers=hs).json()["total"] == 0
    # el cambio de permisos queda en la actividad
    acc = [a["accion"] for a in client.get("/empresa/actividad", headers=h).json()]
    assert "usuario.permisos" in acc
