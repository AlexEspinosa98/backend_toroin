"""Carga inicial de TOROIN como primera empresa de la plataforma (vía API, sin tocar la base directamente).

Crea la empresa con su marca y licencia, un administrador, las obras y el personal. Es idempotente:
si se repite, no duplica obras ni trabajadores (compara por nombre).

Los datos reales NO están en este repositorio: se leen de un directorio local que usted indica.

    python scripts/cargar_toroin.py \
        --api https://TU-BACKEND.vercel.app \
        --datos /ruta/a/toroin-app \
        --admin-correo gerencia@toroin.com --admin-nombre "Nombre Apellido"

Pide por consola el correo y la clave del super admin (o use SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD).
Requiere solo la librería estándar de Python.
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


class Cliente:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.token: str | None = None

    def llamar(self, metodo: str, ruta: str, cuerpo=None):
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        req = urllib.request.Request(f"{self.base}{ruta}", data=datos, method=metodo)
        req.add_header("content-type", "application/json")
        if self.token:
            req.add_header("authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                texto = r.read().decode()
                return json.loads(texto) if texto else None
        except urllib.error.HTTPError as e:
            detalle = e.read().decode()[:400]
            raise SystemExit(f"Error {e.code} en {metodo} {ruta}: {detalle}")


def imagen(ruta: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(ruta.read_bytes()).decode()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", required=True, help="URL del backend, sin barra final")
    ap.add_argument("--datos", required=True, help="Carpeta del prototipo toroin-app (contiene src/lib/data y public)")
    ap.add_argument("--slug", default="toroin", help="Código de acceso de la empresa")
    ap.add_argument("--admin-correo", required=True)
    ap.add_argument("--admin-nombre", required=True)
    ap.add_argument("--plan", default="completo")
    ap.add_argument("--licencia", default="ilimitada", choices=["ilimitada", "periodo", "hasta_fecha", "prueba"])
    ap.add_argument("--meses", type=int, default=12, help="Si --licencia periodo")
    ap.add_argument("--fin", help="AAAA-MM-DD si --licencia hasta_fecha")
    a = ap.parse_args()

    raiz = Path(a.datos).expanduser()
    personal_json = raiz / "src/lib/data/personal.json"
    if not personal_json.exists():
        sys.exit(f"No encuentro {personal_json}")
    personal = json.loads(personal_json.read_text())

    c = Cliente(a.api)
    c.llamar("GET", "/health")
    correo = os.environ.get("SUPERADMIN_EMAIL") or input("Correo del super admin: ").strip()
    clave = os.environ.get("SUPERADMIN_PASSWORD") or getpass.getpass("Clave del super admin: ")
    c.token = c.llamar("POST", "/auth/login", {"correo": correo, "password": clave, "empresa": None})["token"]
    token_super = c.token

    # 1) Empresa
    existentes = {e["slug"]: e for e in c.llamar("GET", "/platform/empresas")}
    if a.slug in existentes:
        empresa_id = existentes[a.slug]["id"]
        print(f"La empresa «{a.slug}» ya existe: se completan los datos que falten.")
    else:
        marca = {
            "nombre_app": "TOROIN Obras", "color_primario": "#19185A", "color_secundario": "#22C3D3",
            "logo_claro": imagen(raiz / "public/logo-blanco.png"),
            "logo_oscuro": imagen(raiz / "public/logo.png"),
            "icono": imagen(raiz / "public/icono.png"),
            "pie": "TORRES RODRIGUEZ INGENIEROS S.A.S.",
        }
        lic = {"plan": a.plan, "tipo": a.licencia, "gracia_dias": 7}
        if a.licencia == "periodo":
            lic["meses"] = a.meses
        if a.licencia == "hasta_fecha":
            lic["fin"] = a.fin
        r = c.llamar("POST", "/platform/empresas", {
            "slug": a.slug, "nombre": "TORRES RODRIGUEZ INGENIEROS S.A.S.", "pais": "CO", "moneda": "COP",
            "marca": marca, "admin": {"nombre": a.admin_nombre, "correo": a.admin_correo}, "licencia": lic,
        })
        empresa_id = r["empresa"]["id"]
        print("\n=== Empresa creada. Credenciales del administrador (se muestran UNA sola vez) ===")
        print(f"  Código de empresa: {a.slug}\n  Correo: {r['admin']['correo']}\n  Contraseña temporal: {r['admin']['password_temporal']}\n")

    # 2) Sesión de la empresa con permiso de escritura (queda en auditoría con su motivo)
    t = c.llamar("POST", f"/platform/empresas/{empresa_id}/impersonar",
                 {"motivo": "Carga inicial de datos de TOROIN", "solo_lectura": False})
    c.token = t["token"]

    # 3) Obras (un nombre por obra; la gente puede estar en varias)
    nombres = sorted({o for f in personal for o in f.get("obras", [])})
    pretty = {"Venecia": "Venecia del Sol", "Ika": "IKA"}
    ya = {o["nombre"] for o in c.llamar("GET", "/obras")}
    nuevas = 0
    for n in nombres:
        nombre = pretty.get(n, n)
        if nombre not in ya:
            c.llamar("POST", "/obras", {"nombre": nombre, "ciudad": "Santa Marta", "estado": "activa"})
            nuevas += 1
    print(f"Obras: {nuevas} creadas, {len(nombres) - nuevas} ya existían.")

    # 4) Personal
    ya = {t["nombre"] for t in c.llamar("GET", "/personal")}
    creados = 0
    for f in personal:
        if f["nombre"] in ya:
            continue
        notas = list(f.get("notas", []))
        if f.get("obras"):  # la asignación a obra aún no es un módulo propio: se conserva como nota
            notas.append("Obras: " + ", ".join(pretty.get(o, o) for o in f["obras"]))
        c.llamar("POST", "/personal", {
            "nombre": f["nombre"], "examen_fecha": f.get("examenFecha"), "examen_vence": f.get("examenVence"),
            "alturas_fecha": f.get("alturasFecha"), "alturas_vence": f.get("alturasVence"), "notas": notas,
        })
        creados += 1
    print(f"Personal: {creados} cargados, {len(personal) - creados} ya existían.")

    # 5) Verificación
    c.token = token_super
    uso = c.llamar("GET", f"/platform/empresas/{empresa_id}/uso")["uso"]
    print(f"\nVerificación en la plataforma → obras: {uso['obras']}, trabajadores: {uso['trabajadores']}, usuarios: {uso['usuarios']}")
    print("Pendiente de portar (aún no existen como módulos): contrato 3010185, asistencia, bitácora, pagos y finanzas.")


if __name__ == "__main__":
    main()
