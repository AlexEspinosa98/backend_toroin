from datetime import date, timedelta

from tests.conftest import crear_empresa, login

HOY = date.today()


def _setup(client, root, slug="acme", correo="admin@acme.com", plan="profesional"):
    out = crear_empresa(client, root, slug=slug, correo=correo, plan=plan)
    h = login(client, correo, out["admin"]["password_temporal"], slug)
    obra = client.post("/obras", json={"nombre": "Torre"}, headers=h).json()
    ok = {"nombre": "Ana", "examen_fecha": str(HOY - timedelta(days=100)), "examen_vence": str(HOY + timedelta(days=200)),
          "alturas_fecha": str(HOY - timedelta(days=100)), "alturas_vence": str(HOY + timedelta(days=200))}
    vencido = {**ok, "nombre": "Beto", "examen_vence": str(HOY - timedelta(days=3))}
    ana = client.post("/personal", json=ok, headers=h).json()
    beto = client.post("/personal", json=vencido, headers=h).json()
    for t in (ana, beto):
        r = client.post("/asignaciones", json={"trabajador_id": t["id"], "obra_id": obra["id"], "fecha_ingreso": str(HOY - timedelta(days=30))}, headers=h)
        assert r.status_code == 201, r.text
    return h, obra, ana, beto


def test_lista_con_habilitacion_y_guardado(client, root):
    h, obra, ana, beto = _setup(client, root)
    r = client.get("/asistencia", params={"obra_id": obra["id"], "fecha": str(HOY)}, headers=h).json()
    est = {p["nombre"]: p["habilitacion"]["estado"] for p in r["personal"]}
    assert est == {"Ana": "habilitado", "Beto": "vencido"} and not r["registrada"]
    body = {"obra_id": obra["id"], "fecha": str(HOY), "marcas": [{"trabajador_id": ana["id"], "asistio": True}]}
    assert client.put("/asistencia", json=body, headers=h).status_code == 200
    r = client.get("/asistencia", params={"obra_id": obra["id"], "fecha": str(HOY)}, headers=h).json()
    assert r["registrada"] and [p for p in r["personal"] if p["nombre"] == "Ana"][0]["marca"]["asistio"] is True
    # corregir el mismo día no duplica
    body["marcas"][0] = {"trabajador_id": ana["id"], "asistio": False, "motivo": "Permiso"}
    assert client.put("/asistencia", json=body, headers=h).status_code == 200
    r = client.get("/asistencia", params={"obra_id": obra["id"], "fecha": str(HOY)}, headers=h).json()
    assert [p for p in r["personal"] if p["nombre"] == "Ana"][0]["marca"]["motivo"] == "Permiso"


def test_reglas_de_validacion(client, root):
    h, obra, ana, beto = _setup(client, root)
    base = {"obra_id": obra["id"], "fecha": str(HOY)}
    # no habilitado no puede marcarse
    assert client.put("/asistencia", json={**base, "marcas": [{"trabajador_id": beto["id"], "asistio": True}]}, headers=h).status_code == 422
    # ausente exige motivo
    assert client.put("/asistencia", json={**base, "marcas": [{"trabajador_id": ana["id"], "asistio": False}]}, headers=h).status_code == 422
    # fecha futura
    fut = {"obra_id": obra["id"], "fecha": str(HOY + timedelta(days=1)), "marcas": []}
    assert client.put("/asistencia", json=fut, headers=h).status_code == 422
    # un trabajador retirado ya no aparece
    asig = client.get("/asignaciones", params={"trabajador_id": ana["id"]}, headers=h).json()[0]
    client.patch(f"/asignaciones/{asig['id']}/retiro", json={"fecha_retiro": str(HOY - timedelta(days=1))}, headers=h)
    r = client.get("/asistencia", params=base, headers=h).json()
    assert [p["nombre"] for p in r["personal"]] == ["Beto"]
    assert client.post("/asignaciones", json={"trabajador_id": beto["id"], "obra_id": obra["id"], "fecha_ingreso": str(HOY)}, headers=h).status_code == 409


def test_aislamiento_y_licencia(client, root):
    h, obra, ana, _ = _setup(client, root)
    hb, obra_b, _, _ = _setup(client, root, slug="beta", correo="admin@beta.com")
    # otra empresa no ve ni toca esta obra / personal
    assert client.get("/asistencia", params={"obra_id": obra["id"], "fecha": str(HOY)}, headers=hb).status_code == 404
    assert client.post("/asignaciones", json={"trabajador_id": ana["id"], "obra_id": obra_b["id"], "fecha_ingreso": str(HOY)}, headers=hb).status_code == 404
    assert client.get("/asignaciones", headers=hb).json() and all(a["obra_id"] == obra_b["id"] for a in client.get("/asignaciones", headers=hb).json())
    # plan sin el módulo asistencia: el plan esencial lo incluye, así que se desactiva a mano
    eid = client.get("/platform/empresas", headers=root).json()[0]["id"]
    client.put(f"/platform/empresas/{eid}/modulos", json={"modulos": {"asistencia": {"activo": False}}}, headers=root)
