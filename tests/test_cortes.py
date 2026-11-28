"""Cortes de pago congelados, préstamos del trabajador y finanzas por rango de fechas."""
from datetime import date, timedelta

from .conftest import crear_empresa, login

# Lunes de una semana sin festivos. El corte va de lunes a domingo: el descanso se paga en el corte que lo contiene.
LUN = date(2026, 3, 2)
SAB = LUN + timedelta(days=5)
DOM = LUN + timedelta(days=6)


def _base(client, root):
    out = crear_empresa(client, root, plan="completo")
    h = login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")
    obra = client.post("/obras", json={"nombre": "Torre"}, headers=h).json()
    client.put(f"/obras/{obra['id']}/contrato", headers=h, json={
        "fecha_inicio": str(LUN - timedelta(days=30)), "fecha_fin": str(LUN + timedelta(days=90)),
        "actividades": [{"grupo": 1, "nombre": "MAMPOSTERÍA", "um": "M2", "cantidad": 500, "valor_unitario": 45000}]})
    return h, obra


def _trabajador(client, h, obra, nombre, jornal=60000):
    t = client.post("/personal", json={"nombre": nombre, "cedula": f"100{len(nombre)}", "jornal": jornal,
                                       "examen_fecha": "2026-01-01", "examen_vence": "2027-12-31",
                                       "alturas_fecha": "2026-01-01", "alturas_vence": "2027-12-31"}, headers=h).json()
    client.post("/asignaciones", json={"trabajador_id": t["id"], "obra_id": obra["id"],
                                       "fecha_ingreso": str(LUN - timedelta(days=30))}, headers=h)
    return t


def _asistir(client, h, obra, t, dias):
    for i in dias:
        r = client.put("/asistencia", json={"obra_id": obra["id"], "fecha": str(LUN + timedelta(days=i)),
                                            "marcas": [{"trabajador_id": t["id"], "asistio": True}]}, headers=h)
        assert r.status_code == 200, r.text


def _corte(client, h, obra=None, desde=LUN, hasta=DOM, **extra):
    cuerpo = {"desde": str(desde), "hasta": str(hasta), "obra_id": obra["id"] if obra else "", **extra}
    return client.post("/cortes", json=cuerpo, headers=h)


def test_el_corte_congela_la_liquidacion_del_periodo(client, root):
    h, obra = _base(client, root)
    ana = _trabajador(client, h, obra, "Ana")
    _asistir(client, h, obra, ana, range(6))          # los 6 días laborables

    r = _corte(client, h, obra)
    assert r.status_code == 201, r.text
    c = r.json()
    assert c["estado"] == "abierto" and c["dias"] == 7 and len(c["filas"]) == 1 and c["avisos"] == []
    f = c["filas"][0]
    # 6 jornales + el domingo reconocido = 7 días pagados
    assert f["dias_asistidos"] == 6 and f["dias_descanso"] == 1 and f["devengado"] == 420000
    assert f["neto"] == round(420000 * 0.92 + f["auxilio"], 2)
    assert [s["reconocido"] for s in f["detalle"]["semanas"]] == [True]

    # al cerrarlo queda congelado aunque después cambie la asistencia
    cerrado = client.post(f"/cortes/{c['id']}/cerrar", headers=h)
    assert cerrado.status_code == 200, cerrado.text
    assert cerrado.json()["estado"] == "cerrado" and cerrado.json()["cerrado_por"]
    client.put("/asistencia", json={"obra_id": obra["id"], "fecha": str(LUN),
                                    "marcas": [{"trabajador_id": ana["id"], "asistio": False, "motivo": "Sin justificar"}]}, headers=h)
    assert client.get(f"/cortes/{c['id']}", headers=h).json()["filas"][0]["devengado"] == 420000


def test_si_falta_un_dia_pierde_el_domingo(client, root):
    h, obra = _base(client, root)
    beto = _trabajador(client, h, obra, "Beto")
    _asistir(client, h, obra, beto, [0, 1, 2, 4, 5])  # falta el jueves
    client.put("/asistencia", json={"obra_id": obra["id"], "fecha": str(LUN + timedelta(days=3)),
                                    "marcas": [{"trabajador_id": beto["id"], "asistio": False, "motivo": "Sin justificar"}]}, headers=h)
    c = _corte(client, h, obra).json()
    f = c["filas"][0]
    assert f["dias_asistidos"] == 5 and f["dias_descanso"] == 0 and f["devengado"] == 300000
    assert "Faltó" in f["detalle"]["semanas"][0]["razon"]

    # cortar en sábado deja el domingo para el corte siguiente, y el sistema lo avisa
    _asistir(client, h, obra, beto, range(7, 13))
    c2 = _corte(client, h, obra, desde=DOM + timedelta(days=1), hasta=DOM + timedelta(days=6)).json()
    assert c2["filas"][0]["dias_asistidos"] == 6 and c2["filas"][0]["dias_descanso"] == 0
    assert any("no termina en domingo" in a for a in c2["avisos"])


def test_el_prestamo_se_descuenta_al_cerrar_y_vuelve_al_reabrir(client, root):
    h, obra = _base(client, root)
    ana = _trabajador(client, h, obra, "Ana")
    _asistir(client, h, obra, ana, range(6))
    p = client.post("/prestamos", headers=h, json={"trabajador_id": ana["id"], "fecha": str(LUN - timedelta(days=10)),
                                                   "concepto": "Préstamo para herramienta", "valor": 300000, "cuota": 100000})
    assert p.status_code == 201, p.text
    p = p.json()
    assert p["saldo"] == 300000 and p["pagado"] is False

    c = _corte(client, h, obra).json()
    f = c["filas"][0]
    assert f["prestamo"] == 100000                      # propone la cuota
    assert f["neto"] == round(f["devengado"] + f["auxilio"] - f["salud"] - f["pension"] - 100000, 2)

    # mientras está abierto el saldo no se mueve
    assert client.get(f"/prestamos?trabajador_id={ana['id']}", headers=h).json()["prestamos"][0]["saldo"] == 300000
    client.post(f"/cortes/{c['id']}/cerrar", headers=h)
    assert client.get(f"/prestamos?trabajador_id={ana['id']}", headers=h).json()["prestamos"][0]["saldo"] == 200000

    r = client.post(f"/cortes/{c['id']}/reabrir", headers=h)
    assert r.status_code == 200 and r.json()["estado"] == "abierto"
    assert client.get(f"/prestamos?trabajador_id={ana['id']}", headers=h).json()["prestamos"][0]["saldo"] == 300000


def test_se_ajustan_los_descuentos_a_mano_y_se_recalcula(client, root):
    h, obra = _base(client, root)
    ana = _trabajador(client, h, obra, "Ana")
    _asistir(client, h, obra, ana, range(6))
    client.post("/prestamos", headers=h, json={"trabajador_id": ana["id"], "fecha": str(LUN), "concepto": "Avance", "valor": 500000, "cuota": 100000})
    c = _corte(client, h, obra).json()
    fid = c["filas"][0]["id"]

    r = client.put(f"/cortes/{c['id']}", headers=h, json={
        "notas": "Corte semana 1", "filas": [{"id": fid, "prestamo": 50000, "otras": 20000, "nota": "Botas"}]})
    assert r.status_code == 200, r.text
    f = r.json()["filas"][0]
    assert f["prestamo"] == 50000 and f["otras"] == 20000 and f["nota"] == "Botas"
    assert r.json()["totales"]["neto"] == f["neto"] and r.json()["notas"] == "Corte semana 1"

    # no se puede descontar más de lo que se le debe
    mal = client.put(f"/cortes/{c['id']}", headers=h, json={"filas": [{"id": fid, "prestamo": 9000000, "otras": 0}]})
    assert mal.status_code == 422 and "descuentos" in mal.json()["detail"]

    # recalcular trae la asistencia nueva y conserva los descuentos ajustados
    _asistir(client, h, obra, ana, [6])
    r = client.put(f"/cortes/{c['id']}", headers=h, json={"recalcular": True})
    assert r.status_code == 200
    f = r.json()["filas"][0]
    assert f["prestamo"] == 50000 and f["otras"] == 20000 and f["nota"] == "Botas"

    # al cerrar solo se abona lo que de verdad se descontó
    client.post(f"/cortes/{c['id']}/cerrar", headers=h)
    assert client.get("/prestamos", headers=h).json()["prestamos"][0]["saldo"] == 450000


def test_un_corte_cerrado_no_se_edita_ni_se_borra(client, root):
    h, obra = _base(client, root)
    ana = _trabajador(client, h, obra, "Ana")
    _asistir(client, h, obra, ana, range(6))
    c = _corte(client, h, obra).json()
    client.post(f"/cortes/{c['id']}/cerrar", headers=h)
    assert client.put(f"/cortes/{c['id']}", headers=h, json={"filas": []}).status_code == 409
    assert client.delete(f"/cortes/{c['id']}", headers=h).status_code == 409
    assert client.post(f"/cortes/{c['id']}/cerrar", headers=h).status_code == 409
    # reabierto sí se borra
    client.post(f"/cortes/{c['id']}/reabrir", headers=h)
    assert client.delete(f"/cortes/{c['id']}", headers=h).status_code == 204
    assert client.get("/cortes", headers=h).json()["cortes"] == []


def test_los_cortes_de_una_obra_no_se_cruzan(client, root):
    h, obra = _base(client, root)
    ana = _trabajador(client, h, obra, "Ana")
    _asistir(client, h, obra, ana, range(6))
    assert _corte(client, h, obra).status_code == 201
    choque = _corte(client, h, obra, desde=LUN + timedelta(days=3), hasta=DOM + timedelta(days=3))
    assert choque.status_code == 409 and "cruza" in choque.json()["detail"]
    # el periodo siguiente sí entra, y uno de "todas las obras" es otra serie
    assert _corte(client, h, obra, desde=DOM + timedelta(days=1), hasta=DOM + timedelta(days=7)).status_code == 201
    assert _corte(client, h, None).status_code == 201
    futuro = _corte(client, h, obra, desde=date.today(), hasta=date.today() + timedelta(days=3))
    assert futuro.status_code == 422 and "futura" in futuro.json()["detail"]


def test_el_prestamo_con_abonos_no_se_borra_pero_se_desactiva(client, root):
    h, obra = _base(client, root)
    ana = _trabajador(client, h, obra, "Ana")
    _asistir(client, h, obra, ana, range(6))
    p = client.post("/prestamos", headers=h, json={"trabajador_id": ana["id"], "fecha": str(LUN), "concepto": "Avance", "valor": 200000, "cuota": 50000}).json()
    c = _corte(client, h, obra).json()
    client.post(f"/cortes/{c['id']}/cerrar", headers=h)
    assert client.delete(f"/prestamos/{p['id']}", headers=h).status_code == 409

    r = client.put(f"/prestamos/{p['id']}", headers=h, json={"concepto": "Avance", "cuota": 50000, "activo": False})
    assert r.status_code == 200 and r.json()["activo"] is False
    # desactivado ya no se propone en el corte siguiente
    c2 = _corte(client, h, obra, desde=DOM + timedelta(days=1), hasta=DOM + timedelta(days=7)).json()
    assert c2["filas"] == [] or all(f["prestamo"] == 0 for f in c2["filas"])


def test_finanzas_acepta_un_rango_de_fechas(client, root):
    h, obra = _base(client, root)
    oid = obra["id"]
    client.post(f"/obras/{oid}/facturas", headers=h, json={"numero": "1", "fecha": "2026-03-05", "concepto": "Acta 1", "valor": 5000000})
    client.post(f"/obras/{oid}/facturas", headers=h, json={"numero": "2", "fecha": "2026-04-05", "concepto": "Acta 2", "valor": 3000000})
    client.post(f"/obras/{oid}/gastos", headers=h, json={"fecha": "2026-03-10", "categoria": "Transporte", "descripcion": "Fletes", "valor": 400000})

    todo = client.get(f"/obras/{oid}/finanzas", headers=h).json()
    assert todo["periodo"] is None and len(todo["facturas"]) == 2

    marzo = client.get(f"/obras/{oid}/finanzas?desde=2026-03-01&hasta=2026-03-31", headers=h).json()
    p = marzo["periodo"]
    assert p["facturado"] == 5000000 and p["gastos"] == 400000 and p["neto"] == 4600000
    assert p["n_facturas"] == 1 and p["n_gastos"] == 1 and len(marzo["facturas"]) == 1
    # el resumen de la obra sigue siendo el acumulado
    assert marzo["resumen"]["facturado"] == 8000000

    mal = client.get(f"/obras/{oid}/finanzas?desde=2026-04-01&hasta=2026-03-01", headers=h)
    assert mal.status_code == 422


def test_solo_admin_y_gerencia_manejan_cortes(client, root):
    h, obra = _base(client, root)
    ana = _trabajador(client, h, obra, "Ana")
    _asistir(client, h, obra, ana, range(6))
    u = client.post("/empresa/usuarios", json={"nombre": "Resi", "correo": "resi@acme.com", "rol": "residente"}, headers=h).json()
    hr = login(client, "resi@acme.com", u["password_temporal"], "acme")
    assert _corte(client, hr, obra).status_code == 403
    assert client.post("/prestamos", headers=hr, json={"trabajador_id": ana["id"], "fecha": str(LUN), "valor": 1000}).status_code == 403


def test_los_costos_de_un_item_suman_los_de_sus_subitems(client, root):
    h, obra = _base(client, root)
    oid = obra["id"]
    item = client.get(f"/obras/{oid}/contrato", headers=h).json()["actividades"][0]
    client.put(f"/obras/{oid}/actividades/{item['id']}/subitems", headers=h,
               json=[{"nombre": "Apto 101", "um": "APTO", "cantidad": 1, "equivale": 200}])
    sub = client.get(f"/obras/{oid}/actividades/{item['id']}/subitems", headers=h).json()["subitems"][0]

    # un gasto al ítem y otro a su subítem
    client.post(f"/obras/{oid}/gastos", headers=h, json={"fecha": str(LUN), "categoria": "Herramientas y equipos",
                                                         "descripcion": "Rodillo y brochas", "valor": 185000, "actividad_id": item["id"]})
    client.post(f"/obras/{oid}/gastos", headers=h, json={"fecha": str(LUN), "categoria": "Compra de insumos",
                                                         "descripcion": "Pintura apto 101", "valor": 420000, "actividad_id": sub["id"]})
    # y uno que no es de este ítem
    client.post(f"/obras/{oid}/gastos", headers=h, json={"fecha": str(LUN), "categoria": "Transporte", "descripcion": "Fletes", "valor": 90000})

    r = client.get(f"/obras/{oid}/actividades/{item['id']}/costos", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["total_gastos"] == 605000 and len(d["gastos"]) == 2
    por_nombre = {g["descripcion"]: g for g in d["gastos"]}
    assert por_nombre["Rodillo y brochas"]["subitem"] == "" and por_nombre["Pintura apto 101"]["subitem"] == "Apto 101"
    assert d["costo"] == d["mano_obra"] + d["total_gastos"]
    assert d["margen"] == round(d["ingreso_ejecutado"] - d["costo"], 2)

    # el mismo total aparece en el costo por ítem de finanzas
    fila = [x for x in client.get(f"/obras/{oid}/rubros", headers=h).json()["rubros"] if x["actividad_id"] == item["id"]][0]
    assert fila["gastos"] == 605000

    # un subítem no tiene su propia hoja de costos
    assert client.get(f"/obras/{oid}/actividades/{sub['id']}/costos", headers=h).status_code == 422
