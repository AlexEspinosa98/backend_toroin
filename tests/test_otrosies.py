"""Otrosíes del contrato: adiciones de valor, prórrogas de plazo, ítems nuevos y el PDF del documento."""
import base64
from datetime import date, timedelta

from .conftest import crear_empresa, login

INICIO = date(2026, 3, 2)
FIN = INICIO + timedelta(days=90)
PDF = base64.b64encode(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF\n").decode()


def _base(client, root):
    out = crear_empresa(client, root, plan="completo")
    h = login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")
    obra = client.post("/obras", json={"nombre": "Torre"}, headers=h).json()
    contrato = {"numero": "C-001", "fecha_inicio": str(INICIO), "fecha_fin": str(FIN),
                "actividades": [{"grupo": 1, "nombre": "MAMPOSTERÍA", "um": "M2", "cantidad": 100, "valor_unitario": 50000}]}
    d = client.put(f"/obras/{obra['id']}/contrato", json=contrato, headers=h).json()
    return h, obra, d


def _crear(client, h, obra, **campos):
    cuerpo = {"numero": "1", "fecha": str(INICIO + timedelta(days=30)), **campos}
    return client.post(f"/obras/{obra['id']}/otrosies", json=cuerpo, headers=h)


def test_el_contrato_arranca_sin_otrosies_y_con_el_valor_base(client, root):
    h, obra, d = _base(client, root)
    assert d["contrato"]["valor_base"] == 5000000 and d["contrato"]["valor_total"] == 5000000
    assert d["contrato"]["fecha_fin_base"] == str(FIN)
    r = client.get(f"/obras/{obra['id']}/otrosies", headers=h).json()
    assert r["otrosies"] == [] and r["adiciones"] == 0 and r["valor_vigente"] == 5000000 and r["dias_prorroga"] == 0


def test_el_otrosi_suma_valor_items_y_prorroga_el_plazo(client, root):
    h, obra, _ = _base(client, root)
    nueva_fin = FIN + timedelta(days=45)
    r = _crear(client, h, obra, descripcion="Adición de pañetes", valor_adicion=3000000,
               fecha_fin_nueva=str(nueva_fin), archivo_nombre="otrosi1.pdf", archivo=PDF,
               actividades=[{"grupo": 2, "nombre": "PAÑETE", "um": "M2", "cantidad": 200, "valor_unitario": 15000}])
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["adiciones"] == 3000000 and d["valor_vigente"] == 8000000
    assert d["fecha_fin_base"] == str(FIN) and d["fecha_fin_vigente"] == str(nueva_fin) and d["dias_prorroga"] == 45
    o = d["otrosies"][0]
    assert o["items"] == 1 and o["valor_items"] == 3000000 and o["cuadra"] is True
    assert o["tiene_pdf"] is True and o["archivo_nombre"] == "otrosi1.pdf"

    # el contrato de la obra ya muestra el valor y el plazo vigentes, y el ítem queda marcado
    obra_d = client.get(f"/obras/{obra['id']}/contrato", headers=h).json()
    assert obra_d["contrato"]["valor_total"] == 8000000 and obra_d["contrato"]["fecha_fin"] == str(nueva_fin)
    assert obra_d["contrato"]["valor_base"] == 5000000 and obra_d["contrato"]["fecha_fin_base"] == str(FIN)
    panete = [a for a in obra_d["actividades"] if a["nombre"] == "PAÑETE"][0]
    assert panete["otrosi"] == "Otrosí 1" and panete["otrosi_id"] == o["id"]
    assert [a for a in obra_d["actividades"] if a["nombre"] == "MAMPOSTERÍA"][0]["otrosi"] == ""


def test_el_pdf_se_descarga_y_solo_se_aceptan_pdf(client, root):
    h, obra, _ = _base(client, root)
    o = _crear(client, h, obra, archivo_nombre="otrosi1.pdf", archivo=PDF).json()["otrosies"][0]
    r = client.get(f"/obras/{obra['id']}/otrosies/{o['id']}/archivo", headers=h)
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF-")

    mal = _crear(client, h, obra, numero="2", archivo_nombre="foto.png", archivo=base64.b64encode(b"\x89PNG\r\n").decode())
    assert mal.status_code == 422 and "PDF" in mal.json()["detail"]


def test_manda_la_prorroga_del_otrosi_mas_reciente_aunque_recorte(client, root):
    h, obra, _ = _base(client, root)
    _crear(client, h, obra, numero="1", fecha=str(INICIO + timedelta(days=10)), fecha_fin_nueva=str(FIN + timedelta(days=60)))
    d = _crear(client, h, obra, numero="2", fecha=str(INICIO + timedelta(days=40)), fecha_fin_nueva=str(FIN + timedelta(days=20))).json()
    assert d["fecha_fin_vigente"] == str(FIN + timedelta(days=20)) and d["dias_prorroga"] == 20


def test_se_puede_corregir_el_otrosi_y_sus_items(client, root):
    h, obra, _ = _base(client, root)
    d = _crear(client, h, obra, valor_adicion=3000000,
               actividades=[{"grupo": 2, "nombre": "PAÑETE", "um": "M2", "cantidad": 200, "valor_unitario": 15000}]).json()
    o = d["otrosies"][0]
    item = o["actividades"][0]
    r = client.put(f"/obras/{obra['id']}/otrosies/{o['id']}", headers=h, json={
        "numero": "1-A", "fecha": str(INICIO + timedelta(days=31)), "valor_adicion": 4500000, "archivo": "",
        "actividades": [{"id": item["id"], "grupo": 2, "nombre": "PAÑETE LISO", "um": "M2", "cantidad": 300, "valor_unitario": 15000}]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["valor_vigente"] == 9500000
    o = d["otrosies"][0]
    assert o["numero"] == "1-A" and o["valor_items"] == 4500000 and o["cuadra"] is True and o["tiene_pdf"] is False
    assert o["actividades"][0]["nombre"] == "PAÑETE LISO" and o["actividades"][0]["id"] == item["id"]

    # quitar la fila del otrosí borra el ítem de la obra
    d = client.put(f"/obras/{obra['id']}/otrosies/{o['id']}", headers=h,
                   json={"numero": "1-A", "fecha": str(INICIO + timedelta(days=31)), "valor_adicion": 0, "actividades": []}).json()
    assert d["otrosies"][0]["items"] == 0 and d["valor_vigente"] == 5000000
    assert not [a for a in client.get(f"/obras/{obra['id']}/contrato", headers=h).json()["actividades"] if a["nombre"] == "PAÑETE LISO"]


def test_eliminar_el_otrosi_devuelve_el_contrato_a_como_estaba(client, root):
    h, obra, _ = _base(client, root)
    d = _crear(client, h, obra, valor_adicion=3000000, fecha_fin_nueva=str(FIN + timedelta(days=45)),
               actividades=[{"grupo": 2, "nombre": "PAÑETE", "um": "M2", "cantidad": 200, "valor_unitario": 15000}]).json()
    oid = d["otrosies"][0]["id"]
    r = client.delete(f"/obras/{obra['id']}/otrosies/{oid}", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["otrosies"] == [] and d["valor_vigente"] == 5000000 and d["fecha_fin_vigente"] == str(FIN) and d["dias_prorroga"] == 0
    acts = client.get(f"/obras/{obra['id']}/contrato", headers=h).json()["actividades"]
    assert [a["nombre"] for a in acts] == ["MAMPOSTERÍA"]


def test_no_se_borra_un_otrosi_cuyo_item_ya_tiene_avance(client, root):
    h, obra, _ = _base(client, root)
    d = _crear(client, h, obra, valor_adicion=3000000,
               actividades=[{"grupo": 2, "nombre": "PAÑETE", "um": "M2", "cantidad": 200, "valor_unitario": 15000}]).json()
    o = d["otrosies"][0]
    t = client.post("/personal", json={"nombre": "Ana", "jornal": 60000, "examen_fecha": "2026-01-01", "examen_vence": "2027-12-31",
                                       "alturas_fecha": "2026-01-01", "alturas_vence": "2027-12-31"}, headers=h).json()
    client.post("/asignaciones", json={"trabajador_id": t["id"], "obra_id": obra["id"], "fecha_ingreso": str(INICIO)}, headers=h)
    reg = client.put("/bitacora", json={"obra_id": obra["id"], "fecha": str(INICIO + timedelta(days=35)),
                                        "actividad_id": o["actividades"][0]["id"], "cantidad": 20, "trabajadores": [t["id"]]}, headers=h)
    assert reg.status_code in (200, 201), reg.text

    r = client.delete(f"/obras/{obra['id']}/otrosies/{o['id']}", headers=h)
    assert r.status_code == 409 and "avance" in r.json()["detail"]
    quitar = client.put(f"/obras/{obra['id']}/otrosies/{o['id']}", headers=h,
                        json={"numero": "1", "fecha": str(INICIO + timedelta(days=30)), "valor_adicion": 0, "actividades": []})
    assert quitar.status_code == 409 and "avance" in quitar.json()["detail"]


def test_guardar_el_contrato_no_borra_ni_duplica_los_items_del_otrosi(client, root):
    h, obra, d0 = _base(client, root)
    d = _crear(client, h, obra, valor_adicion=3000000,
               actividades=[{"grupo": 2, "nombre": "PAÑETE", "um": "M2", "cantidad": 200, "valor_unitario": 15000}]).json()
    base = [a for a in d0["actividades"] if a["nombre"] == "MAMPOSTERÍA"][0]
    contrato = {"numero": "C-001", "fecha_inicio": str(INICIO), "fecha_fin": str(FIN),
                "actividades": [{"id": base["id"], "grupo": 1, "nombre": "MAMPOSTERÍA", "um": "M2", "cantidad": 120, "valor_unitario": 50000}]}
    r = client.put(f"/obras/{obra['id']}/contrato", json=contrato, headers=h)
    assert r.status_code == 200, r.text
    acts = r.json()["actividades"]
    assert sorted(a["nombre"] for a in acts) == ["MAMPOSTERÍA", "PAÑETE"]
    # 6.000.000 de base + 3.000.000 del otrosí
    assert r.json()["contrato"]["valor_base"] == 6000000 and r.json()["contrato"]["valor_total"] == 9000000

    # y el ítem del otrosí no se puede editar desde el contrato
    panete = [a for a in acts if a["nombre"] == "PAÑETE"][0]
    contrato["actividades"].append({"id": panete["id"], "grupo": 2, "nombre": "PAÑETE", "um": "M2", "cantidad": 999, "valor_unitario": 15000})
    mal = client.put(f"/obras/{obra['id']}/contrato", json=contrato, headers=h)
    assert mal.status_code == 422 and "Otrosí 1" in mal.json()["detail"]


def test_el_otrosi_exige_contrato_y_respeta_las_fechas(client, root):
    h, obra, _ = _base(client, root)
    sin_contrato = client.post("/obras", json={"nombre": "Sin contrato"}, headers=h).json()
    r = _crear(client, h, sin_contrato)
    assert r.status_code == 422 and "contrato" in r.json()["detail"]

    temprano = client.post(f"/obras/{obra['id']}/otrosies", headers=h,
                           json={"numero": "1", "fecha": str(INICIO - timedelta(days=5))})
    assert temprano.status_code == 422 and "anterior al inicio" in temprano.json()["detail"]


def test_el_residente_ve_los_otrosies_pero_no_los_cambia(client, root):
    out = crear_empresa(client, root, plan="completo")
    h = login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")
    obra = client.post("/obras", json={"nombre": "Torre"}, headers=h).json()
    client.put(f"/obras/{obra['id']}/contrato", headers=h, json={
        "numero": "C-001", "fecha_inicio": str(INICIO), "fecha_fin": str(FIN),
        "actividades": [{"grupo": 1, "nombre": "MAMPOSTERÍA", "um": "M2", "cantidad": 100, "valor_unitario": 50000}]})
    u = client.post("/empresa/usuarios", json={"nombre": "Residente", "correo": "res@acme.com", "rol": "residente"}, headers=h).json()
    hr = login(client, "res@acme.com", u["password_temporal"], "acme")
    assert client.get(f"/obras/{obra['id']}/otrosies", headers=hr).status_code == 200
    assert _crear(client, hr, obra).status_code == 403
