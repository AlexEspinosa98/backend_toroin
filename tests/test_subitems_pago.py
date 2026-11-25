"""Subítems del contrato, forma de pago por asignación y gastos imputados al ítem."""
from datetime import date, timedelta

from .conftest import crear_empresa, login

LUN = date(2026, 3, 2)  # lunes, semana sin festivos


def _base(client, root):
    out = crear_empresa(client, root, plan="completo")
    h = login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")
    obra = client.post("/obras", json={"nombre": "Torre"}, headers=h).json()
    contrato = {"fecha_inicio": str(LUN - timedelta(days=10)), "fecha_fin": str(LUN + timedelta(days=60)),
                "actividades": [{"grupo": 1, "nombre": "MAMPOSTERÍA", "um": "M2", "cantidad": 500, "valor_unitario": 45000}]}
    d = client.put(f"/obras/{obra['id']}/contrato", json=contrato, headers=h).json()
    return h, obra, d["actividades"][0]


def _trabajador(client, h, obra, nombre, **asig):
    t = client.post("/personal", json={"nombre": nombre, "jornal": 60000, "examen_fecha": "2026-01-01", "examen_vence": "2027-12-31",
                                       "alturas_fecha": "2026-01-01", "alturas_vence": "2027-12-31"}, headers=h).json()
    r = client.post("/asignaciones", json={"trabajador_id": t["id"], "obra_id": obra["id"],
                                           "fecha_ingreso": str(LUN - timedelta(days=10)), **asig}, headers=h)
    assert r.status_code == 201, r.text
    return t


def _asistir(client, h, obra, t, dias):
    for i in dias:
        client.put("/asistencia", json={"obra_id": obra["id"], "fecha": str(LUN + timedelta(days=i)),
                                        "marcas": [{"trabajador_id": t["id"], "asistio": True}]}, headers=h)


def _subitems(client, h, obra, act, filas):
    return client.put(f"/obras/{obra['id']}/actividades/{act['id']}/subitems", json=filas, headers=h)


def test_subitems_reparten_el_item_y_aportan_avance(client, root):
    h, obra, act = _base(client, root)
    r = _subitems(client, h, obra, act, [
        {"nombre": "Apartamento 101", "um": "APTO", "cantidad": 1, "equivale": 76, "tarifa": 900000},
        {"nombre": "Apartamento 102", "um": "APTO", "cantidad": 1, "equivale": 40},
        {"nombre": "Acarreo de material", "um": "VIAJE", "cantidad": 10, "equivale": 0, "aporta_avance": False, "tarifa": 30000},
    ])
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["repartido"] == 116 and d["sin_repartir"] == 384
    assert d["item"]["registrable"] is False and d["item"]["subitems"] == 3

    # no se puede repartir más de lo contratado
    mal = _subitems(client, h, obra, act, [{"nombre": "Todo", "um": "APTO", "cantidad": 1, "equivale": 900}])
    assert mal.status_code == 422 and "500" in mal.json()["detail"]

    # el avance va en el subítem, no en el ítem
    sub = {s["nombre"]: s for s in d["subitems"]}
    t = _trabajador(client, h, obra, "Ana")
    directo = client.put("/bitacora", json={"obra_id": obra["id"], "fecha": str(LUN), "actividad_id": act["id"], "cantidad": 10,
                                            "trabajadores": [t["id"]]}, headers=h)
    assert directo.status_code == 422 and "subítems" in directo.json()["detail"]

    # 80 % del apartamento 101 = 0,8 APTO → 60,8 M2 del ítem
    ok = client.put("/bitacora", json={"obra_id": obra["id"], "fecha": str(LUN), "actividad_id": sub["Apartamento 101"]["id"],
                                       "cantidad": 0.8, "trabajadores": [t["id"]]}, headers=h)
    assert ok.status_code == 200, ok.text
    filas = {a["nombre"]: a for a in client.get(f"/obras/{obra['id']}/contrato", headers=h).json()["actividades"]}
    assert round(filas["MAMPOSTERÍA"]["ejecutado"], 2) == 60.8
    assert round(filas["Apartamento 101"]["aporte"], 2) == 60.8 and filas["Apartamento 101"]["ejecutado"] == 0.8

    # no deja pasarse de lo que falta del subítem
    pasado = client.put("/bitacora", json={"obra_id": obra["id"], "fecha": str(LUN + timedelta(days=1)),
                                           "actividad_id": sub["Apartamento 101"]["id"], "cantidad": 0.5, "trabajadores": [t["id"]]}, headers=h)
    assert pasado.status_code == 422 and "subítem" in pasado.json()["detail"]

    # tampoco se puede borrar un subítem con avance
    quitar = _subitems(client, h, obra, act, [{"id": sub["Apartamento 102"]["id"], "nombre": "Apartamento 102", "um": "APTO", "cantidad": 1, "equivale": 40}])
    assert quitar.status_code == 409


def test_subitem_no_se_divide_y_avance_valoriza_el_contrato(client, root):
    h, obra, act = _base(client, root)
    d = _subitems(client, h, obra, act, [{"nombre": "Apto 101", "um": "APTO", "cantidad": 1, "equivale": 100}]).json()
    hijo = d["subitems"][0]
    assert client.put(f"/obras/{obra['id']}/actividades/{hijo['id']}/subitems", json=[], headers=h).status_code == 422
    # el cliente paga 100 M2 × 45.000 por ese subítem completo
    assert hijo["ingreso"] == 4_500_000
    t = _trabajador(client, h, obra, "Ana")
    client.put("/bitacora", json={"obra_id": obra["id"], "fecha": str(LUN), "actividad_id": hijo["id"], "cantidad": 1,
                                  "trabajadores": [t["id"]]}, headers=h)
    res = client.get(f"/obras/{obra['id']}/contrato", headers=h).json()["resumen"]
    # 100 de 500 M2 ejecutados: el contrato vale 22,5 M y lo ejecutado 4,5 M
    assert res["valor_contrato"] == 22_500_000 and res["valor_ejecutado"] == 4_500_000


def test_forma_de_pago_por_asignacion(client, root):
    h, obra, act = _base(client, root)
    hijo = _subitems(client, h, obra, act, [{"nombre": "Apto 101", "um": "APTO", "cantidad": 2, "equivale": 100, "tarifa": 500000}]).json()["subitems"][0]
    jor = _trabajador(client, h, obra, "Ana", modalidad="jornal")
    des = _trabajador(client, h, obra, "Beto", modalidad="destajo", jornal=70000)
    for t in (jor, des):
        _asistir(client, h, obra, t, range(0, 6))
    r = client.put("/bitacora", json={"obra_id": obra["id"], "fecha": str(LUN), "actividad_id": hijo["id"], "cantidad": 1,
                                      "trabajadores": [jor["id"], des["id"]], "por_trabajador": {jor["id"]: 0.4, des["id"]: 0.6}}, headers=h)
    assert r.status_code == 200, r.text
    q = f"/pagos?desde={LUN}&hasta={LUN + timedelta(days=6)}&obra_id={obra['id']}&corte={LUN + timedelta(days=30)}"
    filas = {f["nombre"]: f for f in client.get(q, headers=h).json()["filas"]}
    # Ana es por jornal: 6 días × 60.000 + el domingo, aunque haya ejecutado cantidad
    a = filas["Ana"]
    assert a["modalidad"] == "jornal" and a["basico"] == 6 * 60000 and a["devengado"] == 7 * 60000
    assert a["detalle"][0]["pagado"] is False
    # Beto es por ejecutado: 0,6 APTO × 500.000 = 300.000, con el mínimo garantizado
    b = filas["Beto"]
    assert b["modalidad"] == "destajo" and b["basico"] == 300000 and b["dias_destajo"] == 6
    assert b["ajuste_minimo"] > 0 and b["devengado"] >= 7 * 58364

    # cambiar la forma de pago de una asignación ya creada
    asig = client.get(f"/asignaciones?trabajador_id={des['id']}", headers=h).json()[0]
    assert client.patch(f"/asignaciones/{asig['id']}/pago", json={"modalidad": "jornal"}, headers=h).json()["modalidad"] == "jornal"
    b2 = {f["nombre"]: f for f in client.get(q, headers=h).json()["filas"]}["Beto"]
    assert b2["modalidad"] == "jornal" and b2["basico"] == 6 * 70000  # usa el jornal pactado en la obra


def test_gastos_y_facturas_imputados_al_item(client, root):
    h, obra, act = _base(client, root)
    oid = obra["id"]
    hijo = _subitems(client, h, obra, act, [{"nombre": "Apto 101", "um": "APTO", "cantidad": 1, "equivale": 100}]).json()["subitems"][0]
    assert client.post(f"/obras/{oid}/gastos", json={"fecha": str(LUN), "categoria": "Transporte", "valor": 200000,
                                                     "actividad_id": hijo["id"]}, headers=h).status_code == 201
    assert client.post(f"/obras/{oid}/gastos", json={"fecha": str(LUN), "categoria": "Otros", "valor": 50000}, headers=h).status_code == 201
    assert client.post(f"/obras/{oid}/facturas", json={"fecha": str(LUN), "valor": 1_000_000, "numero": "F1",
                                                       "actividad_id": act["id"]}, headers=h).status_code == 201
    ajeno = client.post(f"/obras/{oid}/gastos", json={"fecha": str(LUN), "categoria": "Otros", "valor": 1, "actividad_id": "no-existe"}, headers=h)
    assert ajeno.status_code == 422
    r = client.get(f"/obras/{oid}/rubros", headers=h).json()
    fila = r["rubros"][0]
    # el gasto del subítem cuenta en el rubro del ítem
    assert fila["gastos"] == 200000 and fila["facturado"] == 1_000_000 and fila["contratado"] == 22_500_000
    assert r["sin_imputar"]["gastos"] == 50000


def test_presupuesto_suma_la_mano_de_obra_de_los_subitems_al_item(client, root):
    h, obra, act = _base(client, root)
    oid = obra["id"]
    subs = {s["nombre"]: s for s in _subitems(client, h, obra, act, [
        {"nombre": "Apto 101", "um": "APTO", "cantidad": 1, "equivale": 100, "tarifa": 500000},
        {"nombre": "Acarreo", "um": "VIAJE", "cantidad": 10, "equivale": 0, "aporta_avance": False, "tarifa": 30000},
    ]).json()["subitems"]}
    t = _trabajador(client, h, obra, "Ana", modalidad="destajo")
    _asistir(client, h, obra, t, range(0, 6))
    for nombre, cant in (("Apto 101", 1), ("Acarreo", 2)):
        r = client.put("/bitacora", json={"obra_id": oid, "fecha": str(LUN), "actividad_id": subs[nombre]["id"],
                                          "cantidad": cant, "trabajadores": [t["id"]]}, headers=h)
        assert r.status_code == 200, r.text
    assert client.put(f"/obras/{oid}/presupuesto", json={"items": [
        {"categoria": "Mano de obra", "concepto": "Cuadrilla", "valor": 1_000_000, "actividad_id": act["id"]}]}, headers=h).status_code == 200
    d = client.get(f"/obras/{oid}/presupuesto", headers=h).json()
    # 1 APTO × 500.000 + 2 VIAJE × 30.000: el renglón del ítem padre trae lo pagado en sus subítems
    fila = d["por_actividad"][0]
    assert fila["actividad_id"] == act["id"] and fila["real"] == 560000 and fila["desviacion"] == -440000
    # y la categoría no lo cuenta dos veces
    mo = {c["categoria"]: c for c in d["categorias"]}["Mano de obra"]
    assert mo["real"] == 560000
    # los subítems salen debajo de su padre en el selector
    acts = {a["nombre"]: a for a in d["actividades"]}
    assert acts["Apto 101"]["padre"] == "MAMPOSTERÍA" and acts["MAMPOSTERÍA"]["subitems"] == 2
