from datetime import date, timedelta

from app.main import app

from .conftest import crear_empresa, login

HOY = date.today()


def _empresa(client, root, plan="completo"):
    out = crear_empresa(client, root, plan=plan)
    return login(client, "admin@acme.com", out["admin"]["password_temporal"], "acme")


def test_alertas_habilitacion_y_epp(client, root):
    h = _empresa(client, root)
    obra = client.post("/obras", json={"nombre": "Torre"}, headers=h).json()
    venc = {"nombre": "Vencido", "examen_fecha": "2024-01-01", "examen_vence": str(HOY - timedelta(days=5)), "alturas_fecha": "2024-01-01", "alturas_vence": str(HOY + timedelta(days=300))}
    prox = {"nombre": "Por vencer", "examen_fecha": "2025-01-01", "examen_vence": str(HOY + timedelta(days=10)), "alturas_fecha": "2025-01-01", "alturas_vence": str(HOY + timedelta(days=300))}
    ids = []
    for t in (venc, prox):
        r = client.post("/personal", json=t, headers=h).json()
        ids.append(r["id"])
        client.post("/asignaciones", json={"trabajador_id": r["id"], "obra_id": obra["id"], "fecha_ingreso": str(HOY - timedelta(days=20))}, headers=h)
    a = client.get("/alertas", headers=h).json()
    tipos = [(x["tipo"], x["severidad"]) for x in a["alertas"]]
    assert ("sst", "alta") in tipos and ("sst", "media") in tipos
    assert a["altas"] >= 1 and a["alertas"][0]["severidad"] == "alta"  # ordenadas por gravedad
    # EPP pendiente aparece
    client.post("/epp", json={"trabajador_id": ids[1], "obra_id": obra["id"], "elemento": "Casco"}, headers=h)
    assert any(x["tipo"] == "epp" for x in client.get("/alertas", headers=h).json()["alertas"])


def test_alertas_respetan_licencia_y_aislamiento(client, root):
    h = _empresa(client, root, plan="esencial")
    tipos = {x["tipo"] for x in client.get("/alertas", headers=h).json()["alertas"]}
    assert "finanzas" not in tipos and "pagos" not in tipos  # módulos no licenciados no generan alertas
    assert client.get("/alertas").status_code == 401


def test_error_500_queda_registrado(client, root):
    h = _empresa(client, root)

    @app.get("/_boom_test")
    def boom():
        raise RuntimeError("fallo de prueba")

    r = client.get("/_boom_test", headers=h)
    assert r.status_code == 500 and r.json()["ref"] and r.headers["x-ref"] == r.json()["ref"]
    errores = client.get("/empresa/actividad?solo_errores=true", headers=h).json()
    assert errores and errores[0]["detalle"]["tipo"] == "RuntimeError" and "traza" not in errores[0]["detalle"]
    assert errores[0]["entidad_id"] == r.json()["ref"]
    # peticiones normales llevan referencia
    assert client.get("/health").headers["x-ref"]
