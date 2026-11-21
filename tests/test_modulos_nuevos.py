from datetime import date, timedelta

from .conftest import crear_empresa, login

HOY = date.today()
SST_OK = {"examen_fecha": "2026-01-01", "examen_vence": "2030-12-31", "alturas_fecha": "2026-01-01", "alturas_vence": "2030-12-31"}


def _setup(client, root, plan="completo", dias_contrato=(30, 60)):
    out = crear_empresa(client, root, plan=plan)
    h = login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")
    obra = client.post("/obras", json={"nombre": "Torre"}, headers=h).json()
    t = client.post("/personal", json={"nombre": "Ana", "jornal": 60000, **SST_OK}, headers=h).json()
    client.post("/asignaciones", json={"trabajador_id": t["id"], "obra_id": obra["id"], "fecha_ingreso": str(HOY - timedelta(days=60))}, headers=h)
    contrato = {"fecha_inicio": str(HOY - timedelta(days=dias_contrato[0])), "fecha_fin": str(HOY + timedelta(days=dias_contrato[1])),
                "actividades": [{"grupo": 1, "nombre": "PAÑETES", "um": "M2", "cantidad": 100, "valor_unitario": 10000, "tarifa": 4000},
                                {"grupo": 2, "nombre": "PINTURA", "um": "M2", "cantidad": 100, "valor_unitario": 5000}]}
    d = client.put(f"/obras/{obra['id']}/contrato", json=contrato, headers=h).json()
    return h, obra, t, d["actividades"]


# ---------------- Presupuesto ----------------
def test_presupuesto_vs_real(client, root):
    h, obra, t, acts = _setup(client, root)
    oid = obra["id"]
    items = [{"categoria": "Mano de obra", "concepto": "Pañetes", "valor": 100000, "actividad_id": acts[0]["id"]},
             {"categoria": "Materiales e insumos", "concepto": "Cemento", "valor": 200000}]
    r = client.put(f"/obras/{oid}/presupuesto", json={"items": items}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["resumen"]["presupuesto"] == 300000 and r.json()["resumen"]["real"] == 0
    # real: un día de la cuadrilla (jornal 60000) en pañetes + un gasto de insumos
    client.put("/asistencia", json={"obra_id": oid, "fecha": str(HOY - timedelta(days=1)), "marcas": [{"trabajador_id": t["id"], "asistio": True}]}, headers=h)
    client.put("/bitacora", json={"obra_id": oid, "fecha": str(HOY - timedelta(days=1)), "actividad_id": acts[0]["id"], "cantidad": 10, "trabajadores": [t["id"]]}, headers=h)
    client.post(f"/obras/{oid}/gastos", json={"fecha": str(HOY), "categoria": "Compra de insumos", "valor": 50000}, headers=h)
    d = client.get(f"/obras/{oid}/presupuesto", headers=h).json()
    cat = {c["categoria"]: c for c in d["categorias"]}
    assert cat["Mano de obra"]["real"] == 60000 and cat["Materiales e insumos"]["real"] == 50000
    assert d["por_actividad"][0]["real"] == 60000 and d["por_actividad"][0]["desviacion"] == -40000
    assert d["resumen"]["real"] == 110000 and d["resumen"]["valor_ganado"] >= 0
    # validaciones
    assert client.put(f"/obras/{oid}/presupuesto", json={"items": [{"categoria": "Inventada", "valor": 1}]}, headers=h).status_code == 422
    assert client.put(f"/obras/{oid}/presupuesto", json={"items": [{"categoria": "Otros", "valor": 1, "actividad_id": acts[0]["id"]}]}, headers=h).status_code == 422
    assert len(client.get("/presupuesto", headers=h).json()) == 1


# ---------------- Cronograma ----------------
def test_cronograma_ruta_critica_y_atraso(client, root):
    h, obra, t, acts = _setup(client, root)
    oid, a, b = obra["id"], acts[0]["id"], acts[1]["id"]
    prop = client.get(f"/obras/{oid}/cronograma/propuesta", headers=h)
    assert prop.status_code == 200 and len(prop.json()) == 2
    tareas = [{"actividad_id": a, "inicio": str(HOY - timedelta(days=30)), "fin": str(HOY - timedelta(days=10))},
              {"actividad_id": b, "inicio": str(HOY - timedelta(days=9)), "fin": str(HOY + timedelta(days=20)), "predecesoras": [a]}]
    r = client.put(f"/obras/{oid}/cronograma", json={"tareas": tareas}, headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    por = {x["actividad_id"]: x for x in d["tareas"]}
    assert por[a]["critica"] and por[b]["critica"]                         # cadena a → b: sin holgura
    assert por[a]["estado"] == "Atrasada" and por[a]["atraso_dias"] > 0     # debía haber terminado y no hay avance
    assert d["criticas_atrasadas"] >= 1 and d["atraso_proyecto"] > 0 and d["sin_programar"] == []
    # con avance completo pasa a Terminada
    client.put("/asistencia", json={"obra_id": oid, "fecha": str(HOY - timedelta(days=2)), "marcas": [{"trabajador_id": t["id"], "asistio": True}]}, headers=h)
    client.put("/bitacora", json={"obra_id": oid, "fecha": str(HOY - timedelta(days=2)), "actividad_id": a, "cantidad": 100, "trabajadores": [t["id"]]}, headers=h)
    por = {x["actividad_id"]: x for x in client.get(f"/obras/{oid}/cronograma", headers=h).json()["tareas"]}
    assert por[a]["estado"] == "Terminada" and por[a]["fin_real"] == str(HOY - timedelta(days=2))
    # validaciones: ciclo, predecesora propia, actividad repetida, actividad ajena
    ciclo = [{"actividad_id": a, "inicio": str(HOY), "fin": str(HOY), "predecesoras": [b]}, {"actividad_id": b, "inicio": str(HOY), "fin": str(HOY), "predecesoras": [a]}]
    assert client.put(f"/obras/{oid}/cronograma", json={"tareas": ciclo}, headers=h).status_code == 422
    assert client.put(f"/obras/{oid}/cronograma", json={"tareas": [{"actividad_id": a, "inicio": str(HOY), "fin": str(HOY), "predecesoras": [a]}]}, headers=h).status_code == 422
    assert client.put(f"/obras/{oid}/cronograma", json={"tareas": [{"actividad_id": "x", "inicio": str(HOY), "fin": str(HOY)}]}, headers=h).status_code == 422
    assert client.put(f"/obras/{oid}/cronograma", json={"tareas": [{"actividad_id": a, "inicio": str(HOY), "fin": str(HOY - timedelta(days=1))}]}, headers=h).status_code == 422


# ---------------- Inventario ----------------
def test_inventario_kardex_traslado_y_consumo(client, root):
    h, obra, t, acts = _setup(client, root)
    o1 = obra["id"]
    o2 = client.post("/obras", json={"nombre": "Casa"}, headers=h).json()["id"]
    m = client.post("/inventario/materiales", json={"nombre": "Cemento", "unidad": "BTO", "stock_minimo": 5}, headers=h).json()
    assert client.post("/inventario/materiales", json={"nombre": "cemento"}, headers=h).status_code == 409   # nombre repetido sin importar mayúsculas
    base = {"material_id": m["id"], "obra_id": o1, "fecha": str(HOY)}
    assert client.post("/inventario/movimientos", json={**base, "tipo": "entrada", "cantidad": 20, "costo_unitario": 30000}, headers=h).status_code == 201
    r = client.post("/inventario/movimientos", json={**base, "tipo": "salida", "cantidad": 8, "actividad_id": acts[0]["id"]}, headers=h)
    assert r.status_code == 201 and r.json()["saldo"] == 12
    assert client.post("/inventario/movimientos", json={**base, "tipo": "salida", "cantidad": 99}, headers=h).status_code == 409      # saldo insuficiente
    assert client.post("/inventario/movimientos", json={**base, "tipo": "entrada", "cantidad": 0}, headers=h).status_code == 422
    # traslado a otra obra
    assert client.post("/inventario/traslados", json={"material_id": m["id"], "obra_origen_id": o1, "obra_destino_id": o2, "fecha": str(HOY), "cantidad": 10}, headers=h).status_code == 201
    assert client.post("/inventario/traslados", json={"material_id": m["id"], "obra_origen_id": o1, "obra_destino_id": o1, "fecha": str(HOY), "cantidad": 1}, headers=h).status_code == 422
    assert client.post("/inventario/traslados", json={"material_id": m["id"], "obra_origen_id": o1, "obra_destino_id": o2, "fecha": str(HOY), "cantidad": 50}, headers=h).status_code == 409
    mats = client.get("/inventario/materiales", headers=h).json()[0]
    por = {x["obra_id"]: x["stock"] for x in mats["por_obra"]}
    assert por == {o1: 2, o2: 10} and mats["stock"] == 12 and mats["costo_promedio"] == 30000 and mats["bajo_minimo"] and o1 in mats["obras_bajo_minimo"]
    k = client.get(f"/inventario/kardex?material_id={m['id']}&obra_id={o1}", headers=h).json()
    assert [x["saldo"] for x in k["movimientos"]] == [20, 12, 2]
    c = client.get(f"/inventario/consumos?obra_id={o1}", headers=h).json()
    assert c[0]["actividad"] == "PAÑETES" and c[0]["cantidad"] == 8 and c[0]["valor"] == 240000
    assert client.delete(f"/inventario/materiales/{m['id']}", headers=h).status_code == 409   # tiene movimientos
    # desactivado no admite movimientos
    client.patch(f"/inventario/materiales/{m['id']}", json={"nombre": "Cemento", "activo": False}, headers=h)
    assert client.post("/inventario/movimientos", json={**base, "tipo": "entrada", "cantidad": 1}, headers=h).status_code == 409


# ---------------- Compras ----------------
def test_compras_flujo_completo(client, root):
    h, obra, t, acts = _setup(client, root)
    oid = obra["id"]
    m = client.post("/inventario/materiales", json={"nombre": "Arena", "unidad": "M3"}, headers=h).json()
    p = client.post("/compras/proveedores", json={"nombre": "Ferretería Sol", "nit": "900123"}, headers=h).json()
    assert client.post("/compras/proveedores", json={"nombre": "Otra", "nit": "900123"}, headers=h).status_code == 409
    # requisición → aprobación → orden
    rq = client.post("/compras/requisiciones", json={"obra_id": oid, "items": [{"material_id": m["id"], "cantidad": 10}, {"descripcion": "Guantes", "unidad": "PAR", "cantidad": 4}]}, headers=h)
    assert rq.status_code == 201 and rq.json()["etiqueta"] == "REQ-0001" and rq.json()["items"][0]["descripcion"] == "Arena"
    rid = rq.json()["id"]
    assert client.post(f"/compras/requisiciones/{rid}/orden?proveedor_id={p['id']}", headers=h).status_code == 409   # aún no aprobada
    assert client.patch(f"/compras/requisiciones/{rid}", json={"estado": "Aprobada"}, headers=h).json()["estado"] == "Aprobada"
    o = client.post(f"/compras/requisiciones/{rid}/orden?proveedor_id={p['id']}", headers=h).json()
    assert o["estado"] == "Borrador" and o["etiqueta"] == "OC-0001"
    assert client.patch(f"/compras/ordenes/{o['id']}", json={"estado": "Emitida"}, headers=h).status_code == 422     # sin precios
    items = [{"material_id": m["id"], "cantidad": 10, "precio_unitario": 50000}, {"descripcion": "Guantes", "unidad": "PAR", "cantidad": 4, "precio_unitario": 10000}]
    e = client.put(f"/compras/ordenes/{o['id']}", json={"obra_id": oid, "proveedor_id": p["id"], "requisicion_id": rid, "fecha": str(HOY), "items": items, "iva_pct": 19}, headers=h)
    assert e.status_code == 200 and e.json()["subtotal"] == 540000 and round(e.json()["total"]) == 642600
    assert client.patch(f"/compras/ordenes/{o['id']}", json={"estado": "Emitida"}, headers=h).json()["estado"] == "Emitida"
    assert client.put(f"/compras/ordenes/{o['id']}", json={"obra_id": oid, "proveedor_id": p["id"], "fecha": str(HOY), "items": items}, headers=h).status_code == 409   # ya no editable
    # recepción parcial: entra al inventario y genera gasto en la obra
    r1 = client.post(f"/compras/ordenes/{o['id']}/recibir", json={"fecha": str(HOY), "items": [{"indice": 0, "cantidad": 6}]}, headers=h)
    assert r1.status_code == 200 and r1.json()["estado"] == "Parcial"
    assert client.post(f"/compras/ordenes/{o['id']}/recibir", json={"fecha": str(HOY), "items": [{"indice": 0, "cantidad": 5}]}, headers=h).status_code == 422   # excede lo pendiente
    mat = client.get("/inventario/materiales", headers=h).json()[0]
    assert mat["stock"] == 6 and mat["costo_promedio"] == 50000
    g = client.get(f"/obras/{oid}/finanzas", headers=h).json()["gastos"]
    assert len(g) == 1 and round(g[0]["valor"]) == round(6 * 50000 * 1.19) and g[0]["origen"] == f"oc:{o['id']}"
    assert client.patch(f"/compras/ordenes/{o['id']}", json={"estado": "Cancelada"}, headers=h).status_code == 409           # ya recibió mercancía
    r2 = client.post(f"/compras/ordenes/{o['id']}/recibir", json={"fecha": str(HOY), "items": [{"indice": 0, "cantidad": 4}, {"indice": 1, "cantidad": 4}]}, headers=h)
    assert r2.json()["estado"] == "Recibida"
    assert client.get("/compras/resumen", headers=h).json()["ordenes_abiertas"] == 0
    assert len(client.get("/compras/requisiciones", headers=h).json()) == 1


def test_compras_aislamiento_y_alertas(client, root):
    h, obra, t, acts = _setup(client, root)
    m = client.post("/inventario/materiales", json={"nombre": "Clavos", "stock_minimo": 10}, headers=h).json()
    client.post("/inventario/movimientos", json={"material_id": m["id"], "obra_id": obra["id"], "fecha": str(HOY), "tipo": "entrada", "cantidad": 3}, headers=h)
    p = client.post("/compras/proveedores", json={"nombre": "Prov"}, headers=h).json()
    o = client.post("/compras/ordenes", json={"obra_id": obra["id"], "proveedor_id": p["id"], "fecha": str(HOY - timedelta(days=10)), "fecha_entrega": str(HOY - timedelta(days=2)),
                                              "items": [{"descripcion": "Cosa", "cantidad": 1, "precio_unitario": 100}]}, headers=h).json()
    client.patch(f"/compras/ordenes/{o['id']}", json={"estado": "Emitida"}, headers=h)
    tipos = {x["tipo"] for x in client.get("/alertas", headers=h).json()["alertas"]}
    assert {"inventario", "compras"} <= tipos
    # otra empresa no ve ni toca nada
    out = crear_empresa(client, root, slug="otra", correo="admin@otra.com", plan="completo")
    h2 = login(client, "admin@otra.com", out["admin"]["password_temporal"], "otra")
    assert client.get("/inventario/materiales", headers=h2).json() == [] and client.get("/compras/ordenes", headers=h2).json() == []
    assert client.get(f"/compras/ordenes/{o['id']}", headers=h2).status_code == 404
    assert client.get(f"/obras/{obra['id']}/presupuesto", headers=h2).status_code == 404
    assert client.get(f"/obras/{obra['id']}/cronograma", headers=h2).status_code == 404
    assert client.get(f"/inventario/kardex?material_id={m['id']}&obra_id={obra['id']}", headers=h2).status_code in (404,)


def test_modulos_nuevos_respetan_licencia_y_roles(client, root):
    out = crear_empresa(client, root, plan="profesional")
    h = login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")
    for ruta in ("/inventario/materiales", "/compras/ordenes", "/presupuesto", "/cronograma"):
        assert client.get(ruta, headers=h).status_code == 403
    out2 = crear_empresa(client, root, slug="full", correo="admin@full.com", plan="completo")
    h2 = login(client, "admin@full.com", out2["admin"]["password_temporal"], "full")
    u = client.post("/empresa/usuarios", json={"nombre": "Res", "correo": "res@full.com", "rol": "residente"}, headers=h2).json()
    hr = login(client, "res@full.com", u["password_temporal"], "full")
    assert client.get("/inventario/materiales", headers=hr).status_code == 200     # el residente trae inventario por rol
    assert client.get("/presupuesto", headers=hr).status_code == 403               # presupuesto es de gerencia
    assert client.get("/compras/proveedores", headers=hr).status_code == 200
    assert client.post("/compras/proveedores", json={"nombre": "X"}, headers=hr).status_code == 403   # solo gestiona gerencia
    # y el admin puede darle Presupuesto
    client.patch(f"/empresa/usuarios/{u['id']}", json={"modulos": ["contrato", "presupuesto"]}, headers=h2)
    assert client.get("/presupuesto", headers=hr).status_code == 200
