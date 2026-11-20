from datetime import date, timedelta

from .conftest import crear_empresa, login

HOY = date.today()
FOTO = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="


def _setup(client, root, slug="acme", correo="admin@acme.com"):
    out = crear_empresa(client, root, slug=slug, correo=correo)
    h = login(client, correo, out["admin"]["password_temporal"], slug)
    obra = client.post("/obras", json={"nombre": "Torre"}, headers=h).json()
    ok = {"nombre": "Ana", "examen_fecha": str(HOY - timedelta(days=90)), "examen_vence": str(HOY + timedelta(days=200)),
          "alturas_fecha": str(HOY - timedelta(days=90)), "alturas_vence": str(HOY + timedelta(days=200))}
    ana = client.post("/personal", json=ok, headers=h).json()
    luis = client.post("/personal", json={**ok, "nombre": "Luis"}, headers=h).json()
    for t in (ana, luis):
        client.post("/asignaciones", json={"trabajador_id": t["id"], "obra_id": obra["id"], "fecha_ingreso": str(HOY - timedelta(days=30))}, headers=h)
    contrato = {
        "numero": "3010185", "fecha_inicio": str(HOY - timedelta(days=30)), "fecha_fin": str(HOY + timedelta(days=30)),
        "actividades": [{"grupo": 1, "nombre": "BUITRONES", "um": "M2", "cantidad": 100, "valor_unitario": 1000},
                        {"grupo": 2, "nombre": "PAÑETES", "um": "M2", "cantidad": 50, "valor_unitario": 2000}],
    }
    r = client.put(f"/obras/{obra['id']}/contrato", json=contrato, headers=h)
    assert r.status_code == 200, r.text
    return h, obra, ana, luis, r.json()


def test_contrato_resumen_y_actividades(client, root):
    h, obra, *_ , d = _setup(client, root)
    assert d["contrato"]["valor_total"] == 200000 and len(d["actividades"]) == 2
    assert d["resumen"]["valor_contrato"] == 200000 and d["resumen"]["avance_fisico"] == 0
    # fechas inválidas
    malo = {"fecha_inicio": str(HOY), "fecha_fin": str(HOY - timedelta(days=1)), "actividades": [{"nombre": "X", "cantidad": 1}]}
    assert client.put(f"/obras/{obra['id']}/contrato", json=malo, headers=h).status_code == 422


def test_bitacora_avance_y_reglas(client, root):
    h, obra, ana, luis, d = _setup(client, root)
    a1 = d["actividades"][0]
    base = {"obra_id": obra["id"], "fecha": str(HOY), "actividad_id": a1["id"], "cantidad": 40, "trabajadores": [ana["id"], luis["id"]]}
    r = client.put("/bitacora", json={**base, "por_trabajador": {ana["id"]: 30, luis["id"]: 10}}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["personas"] == 2
    # repartir mal la cantidad
    assert client.put("/bitacora", json={**base, "por_trabajador": {ana["id"]: 5}}, headers=h).status_code == 422
    # atraso sin motivo
    assert client.put("/bitacora", json={**base, "horas_atraso": 2}, headers=h).status_code == 422
    assert client.put("/bitacora", json={**base, "horas_atraso": 2, "motivo_atraso": "Clima", "acciones": "Doble turno"}, headers=h).status_code == 200
    # mismo día y actividad: se corrige, no se duplica
    assert len(client.get("/bitacora", params={"obra_id": obra["id"], "fecha": str(HOY)}, headers=h).json()) == 1
    # sin acciones no se acepta el atraso
    assert client.put("/bitacora", json={**base, "horas_atraso": 2, "motivo_atraso": "Clima"}, headers=h).status_code == 422
    # no se puede superar el saldo del contrato (100 M2)
    assert client.put("/bitacora", json={**base, "cantidad": 101}, headers=h).status_code == 422
    # el avance entra al tablero
    res = client.get(f"/obras/{obra['id']}/contrato", headers=h).json()
    assert res["resumen"]["valor_ejecutado"] == 40000 and abs(res["resumen"]["avance_fisico"] - 0.2) < 1e-9
    # fecha futura y actividad ajena
    assert client.put("/bitacora", json={**base, "fecha": str(HOY + timedelta(days=1))}, headers=h).status_code == 422
    assert client.put("/bitacora", json={**base, "actividad_id": "no-existe"}, headers=h).status_code == 404
    # no se puede quitar una actividad con avance
    contrato = {"fecha_inicio": str(HOY - timedelta(days=30)), "fecha_fin": str(HOY + timedelta(days=30)),
                "actividades": [{"id": d["actividades"][1]["id"], "nombre": "PAÑETES", "cantidad": 50, "valor_unitario": 2000}]}
    assert client.put(f"/obras/{obra['id']}/contrato", json=contrato, headers=h).status_code == 409


def test_tareas_y_fotos(client, root):
    h, obra, ana, luis, d = _setup(client, root)
    a1, a2 = d["actividades"]
    r = client.post("/tareas/asignar", json={"obra_id": obra["id"], "trabajador_ids": [ana["id"], luis["id"]], "actividad_id": a1["id"], "desde": str(HOY - timedelta(days=2))}, headers=h)
    assert r.status_code == 201
    client.post("/tareas/asignar", json={"obra_id": obra["id"], "trabajador_ids": [luis["id"]], "actividad_id": a2["id"], "desde": str(HOY), "hasta": str(HOY)}, headers=h)
    hoy = client.get("/tareas", params={"obra_id": obra["id"], "fecha": str(HOY)}, headers=h).json()
    assert hoy[ana["id"]]["actividad_id"] == a1["id"] and hoy[luis["id"]]["actividad_id"] == a2["id"]
    manana = client.get("/tareas", params={"obra_id": obra["id"], "fecha": str(HOY + timedelta(days=1))}, headers=h).json()
    assert manana[luis["id"]]["actividad_id"] == a1["id"]  # la de solo hoy ya no manda
    # quitar tarea
    client.post("/tareas/asignar", json={"obra_id": obra["id"], "trabajador_ids": [ana["id"]], "actividad_id": "", "desde": str(HOY)}, headers=h)
    assert ana["id"] not in client.get("/tareas", params={"obra_id": obra["id"], "fecha": str(HOY)}, headers=h).json()
    # fotos
    assert client.post("/fotos", json={"obra_id": obra["id"], "datos": "no-es-imagen"}, headers=h).status_code == 422
    f = client.post("/fotos", json={"obra_id": obra["id"], "datos": FOTO}, headers=h)
    assert f.status_code == 201
    r = client.put("/bitacora", json={"obra_id": obra["id"], "fecha": str(HOY), "actividad_id": a1["id"], "cantidad": 5, "fotos": [f.json()["id"]]}, headers=h)
    assert r.status_code == 200


def test_aislamiento_contrato_bitacora(client, root):
    h, obra, ana, _, d = _setup(client, root)
    hb, *_ = _setup(client, root, slug="beta", correo="admin@beta.com")
    assert client.get(f"/obras/{obra['id']}/contrato", headers=hb).status_code == 404
    assert client.get("/bitacora", params={"obra_id": obra["id"]}, headers=hb).status_code == 404
    f = client.post("/fotos", json={"obra_id": obra["id"], "datos": FOTO}, headers=h).json()
    assert client.get(f"/fotos/{f['id']}", headers=hb).status_code == 404
    assert client.post("/tareas/asignar", json={"obra_id": obra["id"], "trabajador_ids": [ana["id"]], "desde": str(HOY)}, headers=hb).status_code == 404
