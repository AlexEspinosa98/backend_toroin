from datetime import date, timedelta

from .conftest import crear_empresa, login


def test_login_y_roles(client, root):
    assert client.get("/platform/empresas").status_code == 401
    r = client.get("/platform/empresas", headers=root)
    assert r.status_code == 200 and r.json() == []
    assert client.post("/auth/login", json={"correo": "root@plataforma.com", "password": "mala"}).status_code == 401


def test_crear_empresa_marca_y_admin(client, root):
    out = crear_empresa(client, root)
    assert out["empresa"]["licencia_estado"] == "activa"
    clave = out["admin"]["password_temporal"]
    h = login(client, "admin@acme.com", clave, "acme")
    me = client.get("/auth/me", headers=h).json()
    assert me["rol"] == "admin" and me["debe_cambiar_password"] is True
    assert "bitacora" in me["modulos"] and "finanzas" not in me["modulos"]
    marca = client.get("/public/empresas/acme/marca").json()
    assert marca["nombre"] == "Empresa acme"
    # subdominio duplicado y reservado
    r = client.post("/platform/empresas", json={"slug": "acme", "nombre": "Empresa X", "admin": {"nombre": "Admin", "correo": "a@a.com"},
                                                 "licencia": {"plan": "esencial", "tipo": "ilimitada"}}, headers=root)
    assert r.status_code == 409
    r = client.post("/platform/empresas", json={"slug": "admin", "nombre": "Empresa X", "admin": {"nombre": "Admin", "correo": "a@a.com"},
                                                 "licencia": {"plan": "esencial", "tipo": "ilimitada"}}, headers=root)
    assert r.status_code == 422


def test_modulo_no_licenciado_bloqueado_en_servidor(client, root):
    crear_empresa(client, root)
    emp = client.get("/platform/empresas", headers=root).json()[0]
    clave = client.post(f"/platform/empresas/{emp['id']}/admin", json={"nombre": "Admin", "correo": "admin@acme.com"}, headers=root).json()["password_temporal"]
    h = login(client, "admin@acme.com", clave, "acme")
    assert client.get("/personal", headers=h).status_code == 200
    # finanzas no existe como ruta aún, pero personal sí: lo apagamos con control por módulo
    r = client.put(f"/platform/empresas/{emp['id']}/modulos", json={"modulos": {"personal": {"activo": False}}}, headers=root)
    assert r.status_code == 409  # asistencia/bitácora/etc. dependen de personal
    r = client.put(f"/platform/empresas/{emp['id']}/modulos",
                   json={"modulos": {k: {"activo": False} for k in ("personal", "asistencia", "bitacora", "control_personal", "epp")}}, headers=root)
    assert r.status_code == 200, r.text
    r = client.get("/personal", headers=h)
    assert r.status_code == 403 and "licencia" in r.json()["detail"]
    # volver a lo que dice el plan reactiva (los datos no se pierden)
    client.put(f"/platform/empresas/{emp['id']}/modulos",
               json={"modulos": {k: None for k in ("personal", "asistencia", "bitacora", "control_personal", "epp")}}, headers=root)
    assert client.get("/personal", headers=h).status_code == 200


def test_modulo_con_vigencia(client, root):
    out = crear_empresa(client, root, plan="esencial")
    eid = out["empresa"]["id"]
    ayer = (date.today() - timedelta(days=1)).isoformat()
    r = client.put(f"/platform/empresas/{eid}/modulos", json={"modulos": {"contrato": {"activo": True, "fin": ayer}}}, headers=root)
    assert r.status_code == 200 and "contrato" not in r.json()["modulos"]
    manana = (date.today() + timedelta(days=30)).isoformat()
    r = client.put(f"/platform/empresas/{eid}/modulos", json={"modulos": {"contrato": {"activo": True, "fin": manana}}}, headers=root)
    assert "contrato" in r.json()["modulos"]
    # no se puede activar un módulo no construido ni uno núcleo
    assert client.put(f"/platform/empresas/{eid}/modulos", json={"modulos": {"inventario": {"activo": True}}}, headers=root).status_code == 422
    assert client.put(f"/platform/empresas/{eid}/modulos", json={"modulos": {"obras": {"activo": False}}}, headers=root).status_code == 422


def test_licencia_vencida_solo_lectura_y_gracia(client, root):
    hace = (date.today() - timedelta(days=60)).isoformat()
    fin = (date.today() - timedelta(days=20)).isoformat()
    out = crear_empresa(client, root, licencia={"plan": "profesional", "tipo": "hasta_fecha", "inicio": hace, "fin": fin, "gracia_dias": 7})
    assert out["empresa"]["licencia_estado"] == "vencida"
    h = login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")
    assert client.get("/obras", headers=h).status_code == 200  # puede consultar
    r = client.post("/obras", json={"nombre": "Nueva"}, headers=h)
    assert r.status_code == 402  # no puede registrar
    # renovar restablece la escritura
    eid = out["empresa"]["id"]
    r = client.post(f"/platform/empresas/{eid}/licencias", json={"plan": "profesional", "tipo": "ilimitada"}, headers=root)
    assert r.status_code == 201
    assert client.post("/obras", json={"nombre": "Nueva"}, headers=h).status_code == 201
    assert len(client.get(f"/platform/empresas/{eid}", headers=root).json()["historial"]) == 2


def test_suspension_bloquea_y_reactiva(client, root):
    out = crear_empresa(client, root)
    eid = out["empresa"]["id"]
    h = login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")
    client.post(f"/platform/empresas/{eid}/suspender", headers=root)
    assert client.get("/obras", headers=h).status_code == 403
    assert client.post("/auth/login", json={"correo": "admin@acme.com", "password": out["admin"]["password_temporal"], "empresa": "acme"}).status_code == 403
    client.post(f"/platform/empresas/{eid}/reactivar", headers=root)
    assert client.get("/obras", headers=h).status_code == 200


def test_aislamiento_entre_empresas(client, root):
    a = crear_empresa(client, root, slug="alfa", correo="admin@alfa.com")
    b = crear_empresa(client, root, slug="beta", correo="admin@beta.com")
    ha = login(client, "admin@alfa.com", a["admin"]["password_temporal"], "alfa")
    hb = login(client, "admin@beta.com", b["admin"]["password_temporal"], "beta")
    obra = client.post("/obras", json={"nombre": "Solo de alfa"}, headers=ha).json()
    assert client.get("/obras", headers=hb).json() == []
    assert client.put(f"/obras/{obra['id']}", json={"nombre": "Robada"}, headers=hb).status_code == 404
    assert client.delete(f"/obras/{obra['id']}", headers=hb).status_code == 404
    # el correo no sirve en otra empresa
    r = client.post("/auth/login", json={"correo": "admin@alfa.com", "password": a["admin"]["password_temporal"], "empresa": "beta"})
    assert r.status_code == 401
    # un admin de empresa no accede al panel de plataforma
    assert client.get("/platform/empresas", headers=ha).status_code == 403


def test_limites_del_plan(client, root):
    out = crear_empresa(client, root, plan="esencial")  # max_obras = 3
    h = login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")
    for i in range(3):
        assert client.post("/obras", json={"nombre": f"O{i}"}, headers=h).status_code == 201
    r = client.post("/obras", json={"nombre": "O4"}, headers=h)
    assert r.status_code == 402 and "límite" in r.json()["detail"]
    # el super admin amplía el límite en la licencia
    eid = out["empresa"]["id"]
    client.patch(f"/platform/empresas/{eid}/licencia", json={"limites": {"max_obras": 10}}, headers=root)
    assert client.post("/obras", json={"nombre": "O4"}, headers=h).status_code == 201


def test_roles_dentro_de_la_empresa(client, root):
    out = crear_empresa(client, root)
    h = login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")
    u = client.post("/empresa/usuarios", json={"nombre": "Sara", "correo": "sara@acme.com", "rol": "sst"}, headers=h).json()
    hs = login(client, "sara@acme.com", u["password_temporal"], "acme")
    assert client.get("/obras", headers=hs).status_code == 200  # las obras son de consulta para todos (asistencia/EPP las necesitan)
    assert client.post("/obras", json={"nombre": "x"}, headers=hs).status_code == 403
    assert client.get("/personal", headers=hs).status_code == 200
    assert client.get("/empresa/usuarios", headers=hs).status_code == 403
    me = client.get("/auth/me", headers=hs).json()
    assert "obras" not in me["modulos"] and "personal" in me["modulos"]


def test_suplantacion_solo_lectura_y_auditada(client, root):
    out = crear_empresa(client, root)
    eid = out["empresa"]["id"]
    assert client.post(f"/platform/empresas/{eid}/impersonar", json={"motivo": "x"}, headers=root).status_code == 422
    t = client.post(f"/platform/empresas/{eid}/impersonar", json={"motivo": "Soporte ticket 123"}, headers=root).json()["token"]
    h = {"Authorization": f"Bearer {t}"}
    assert client.get("/obras", headers=h).status_code == 200
    assert client.post("/obras", json={"nombre": "no"}, headers=h).status_code == 403
    assert client.get("/platform/empresas", headers=h).status_code == 403
    me = client.get("/auth/me", headers=h).json()
    assert me["suplantando"] is True and me["empresa"]["slug"] == "acme"
    acciones = [a["accion"] for a in client.get("/platform/auditoria", params={"empresa_id": eid}, headers=root).json()]
    assert "suplantacion.iniciar" in acciones


def test_marca_validaciones(client, root):
    out = crear_empresa(client, root)
    h = login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")
    pixel = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    r = client.put("/empresa/marca", json={"nombre_app": "Acme Obras", "color_primario": "#112233", "logo_claro": pixel}, headers=h)
    assert r.status_code == 200 and r.json()["logo_claro"] == pixel
    assert client.put("/empresa/marca", json={"color_primario": "rojo"}, headers=h).status_code == 422
    assert client.put("/empresa/marca", json={"logo_claro": "http://evil.com/x.png"}, headers=h).status_code == 422
    assert client.put("/empresa/marca", json={"logo_claro": "data:text/html;base64,PHNjcmlwdD4="}, headers=h).status_code == 422


def test_soporte_solo_lee(client, root):
    from app.db import SessionLocal
    from app.models import Usuario
    from app.security import hash_password
    with SessionLocal() as db:
        db.add(Usuario(empresa_id=None, correo="soporte@plataforma.com", nombre="Soporte", rol="soporte", password_hash=hash_password("Soporte1234")))
        db.commit()
    hs = login(client, "soporte@plataforma.com", "Soporte1234")
    assert client.get("/platform/empresas", headers=hs).status_code == 200
    assert client.post("/platform/planes", json={"clave": "zz", "nombre": "Z"}, headers=hs).status_code == 403


def test_planes_y_parametros(client, root):
    planes = client.get("/platform/planes", headers=root).json()
    assert {p["clave"] for p in planes} >= {"esencial", "profesional", "completo"}
    # un plan no puede incluir un módulo sin sus requisitos
    r = client.post("/platform/planes", json={"clave": "roto", "nombre": "Roto", "modulos": ["pagos"]}, headers=root)
    assert r.status_code == 409
    r = client.post("/platform/planes", json={"clave": "fin", "nombre": "Fin", "modulos": ["finanzas"]}, headers=root)
    assert r.status_code == 201
    assert client.get("/platform/parametros-legales", headers=root).json()[0]["salario_minimo"] == 1_750_905
    dash = client.get("/platform/dashboard", headers=root).json()
    assert dash["empresas"] == 0
