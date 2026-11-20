from datetime import date, timedelta

from app.festivos import festivos_colombia

from .conftest import crear_empresa, login

# Semana fija y pasada: lunes 2 a sábado 7 de marzo de 2026 (sin festivos), domingo 8
LUN = date(2026, 3, 2)


def _setup(client, root, plan="completo"):
    out = crear_empresa(client, root, plan=plan)
    h = login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")
    obra = client.post("/obras", json={"nombre": "Torre"}, headers=h).json()
    t = client.post("/personal", json={"nombre": "Ana", "jornal": 60000, "examen_fecha": "2026-01-01", "examen_vence": "2027-12-31", "alturas_fecha": "2026-01-01", "alturas_vence": "2027-12-31"}, headers=h).json()
    client.post("/asignaciones", json={"trabajador_id": t["id"], "obra_id": obra["id"], "fecha_ingreso": str(LUN - timedelta(days=10))}, headers=h)
    contrato = {"fecha_inicio": str(LUN - timedelta(days=10)), "fecha_fin": str(LUN + timedelta(days=60)),
                "actividades": [{"grupo": 1, "nombre": "PAÑETES", "um": "M2", "cantidad": 100, "valor_unitario": 10000, "tarifa": 4000}]}
    d = client.put(f"/obras/{obra['id']}/contrato", json=contrato, headers=h).json()
    return h, obra, t, d["actividades"][0]


def _asistir(client, h, obra, t, dias, motivo=None):
    for i in dias:
        f = LUN + timedelta(days=i)
        r = client.put("/asistencia", json={"obra_id": obra["id"], "fecha": str(f), "marcas": [
            {"trabajador_id": t["id"], "asistio": motivo is None, "motivo": motivo}]}, headers=h)
        assert r.status_code == 200, r.text


def test_festivos_colombia_2026():
    f = festivos_colombia(2026)
    assert f[date(2026, 4, 3)] == "Viernes Santo" and f[date(2026, 1, 12)] == "Reyes Magos" and f[date(2026, 12, 25)] == "Navidad"


def test_control_domingo_legal(client, root):
    h, obra, t, act = _setup(client, root)
    _asistir(client, h, obra, t, range(0, 6))  # lunes a sábado completos
    q = f"/control-personal?desde={LUN}&hasta={LUN + timedelta(days=6)}&obra_id={obra['id']}&corte={LUN + timedelta(days=30)}"
    f = client.get(q, headers=h).json()["filas"][0]
    assert f["dias_asistidos"] == 6 and f["domingos"] == 1
    # falta un día sin justificar → pierde el domingo
    client.put("/asistencia", json={"obra_id": obra["id"], "fecha": str(LUN + timedelta(days=2)), "marcas": [
        {"trabajador_id": t["id"], "asistio": False, "motivo": "Sin justificar"}]}, headers=h)
    f = client.get(q, headers=h).json()["filas"][0]
    assert f["domingos"] == 0 and f["dias_asistidos"] == 5


def test_control_ausencia_justificada_mantiene_domingo(client, root):
    h, obra, t, _ = _setup(client, root)
    _asistir(client, h, obra, t, [0, 1, 3, 4, 5])
    _asistir(client, h, obra, t, [2], motivo="Incapacidad")
    q = f"/control-personal?desde={LUN}&hasta={LUN + timedelta(days=6)}&obra_id={obra['id']}&corte={LUN + timedelta(days=30)}"
    f = client.get(q, headers=h).json()["filas"][0]
    assert f["domingos"] == 1 and f["ausencias_justificadas"] == 1


def test_pagos_jornal_y_destajo(client, root):
    h, obra, t, act = _setup(client, root)
    _asistir(client, h, obra, t, range(0, 6))
    r = client.put("/bitacora", json={"obra_id": obra["id"], "fecha": str(LUN), "actividad_id": act["id"], "cantidad": 30, "trabajadores": [t["id"]]}, headers=h)
    assert r.status_code == 200, r.text
    q = f"/pagos?desde={LUN}&hasta={LUN + timedelta(days=6)}&obra_id={obra['id']}&corte={LUN + timedelta(days=30)}"
    p = client.get(q, headers=h).json()
    fila = p["filas"][0]
    assert fila["basico"] == 6 * 60000 and fila["descansos"] == 60000 and fila["devengado"] == 7 * 60000
    assert round(fila["salud"]) == round(fila["devengado"] * 0.04)
    # a destajo: 30 m2 × 4000 = 120000; el mínimo proporcional (7 días × jornal mínimo) lo supera → ajuste
    assert client.put("/config", json={"modalidad_pago": "destajo"}, headers=h).status_code == 200
    d = client.get(q, headers=h).json()["filas"][0]
    assert d["basico"] == 120000 and d["ajuste_minimo"] > 0 and d["devengado"] >= 7 * 58364
    assert client.patch(f"/actividades/{act['id']}/tarifa", json={"tarifa": 10000}, headers=h).status_code == 200
    assert client.get(q, headers=h).json()["filas"][0]["basico"] == 300000
    assert len(client.get(f"/tarifas?obra_id={obra['id']}", headers=h).json()) == 1


def test_finanzas(client, root):
    h, obra, t, act = _setup(client, root)
    oid = obra["id"]
    assert client.post(f"/obras/{oid}/facturas", json={"fecha": str(LUN), "valor": 100000, "numero": "F1"}, headers=h).status_code == 201
    r = client.post(f"/obras/{oid}/gastos", json={"fecha": str(LUN), "categoria": "Transporte", "valor": 30000}, headers=h)
    assert r.status_code == 201 and r.json()["resumen"]["facturado"] == 100000 and r.json()["resumen"]["gastos"] == 30000
    assert client.post(f"/obras/{oid}/gastos", json={"fecha": str(LUN), "categoria": "Inventada", "valor": 1}, headers=h).status_code == 422
    assert client.put(f"/obras/{oid}/ingresos", json={"ingresos_proyectados": 500000}, headers=h).json()["resumen"]["ingresos_proyectados"] == 500000
    assert len(client.get("/finanzas", headers=h).json()) == 1
    fid = r.json()["facturas"][0]["id"]
    assert client.delete(f"/facturas/{fid}", headers=h).json()["resumen"]["facturado"] == 0


def test_epp_flujo_y_aislamiento(client, root):
    h, obra, t, _ = _setup(client, root)
    body = {"trabajador_id": t["id"], "obra_id": obra["id"], "elemento": "Casco", "cantidad": 1}
    s = client.post("/epp", json=body, headers=h)
    assert s.status_code == 201, s.text
    sid = s.json()["id"]
    assert client.patch(f"/epp/{sid}", json={"estado": "Entregado"}, headers=h).status_code == 409  # debe aprobarse primero
    assert client.patch(f"/epp/{sid}", json={"estado": "Aprobado"}, headers=h).json()["estado"] == "Aprobado"
    assert client.patch(f"/epp/{sid}", json={"estado": "Entregado"}, headers=h).json()["fecha_entrega"]
    assert client.delete(f"/epp/{sid}", headers=h).status_code == 409
    # otra empresa no ve ni toca nada
    out = crear_empresa(client, root, slug="otra", correo="admin@otra.com", plan="completo")
    h2 = login(client, "admin@otra.com", out["admin"]["password_temporal"], "otra")
    assert client.get("/epp", headers=h2).json() == []
    assert client.patch(f"/epp/{sid}", json={"estado": "Rechazado"}, headers=h2).status_code == 404
    assert client.get(f"/obras/{obra['id']}/finanzas", headers=h2).status_code == 404


def test_modulo_no_licenciado(client, root):
    out = crear_empresa(client, root, plan="esencial")
    h = login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")
    assert client.get("/finanzas", headers=h).status_code == 403
    assert client.get(f"/pagos?desde={LUN}&hasta={LUN}", headers=h).status_code == 403
