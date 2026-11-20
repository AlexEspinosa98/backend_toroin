"""Registro de peticiones y de errores del servidor.

- Cada petición queda en el log (método, ruta, estado, ms, ref) y en la cabecera x-ref.
- Un error no controlado se guarda en la auditoría (acción «error.500») para que el soporte lo vea
  sin entrar a los logs del hosting, y el usuario recibe un código de referencia.
"""
from __future__ import annotations

import logging
import time
import traceback
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from .db import SessionLocal
from .models import Auditoria
from .security import leer_token

log = logging.getLogger("toroin")
if not log.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)

RUIDO = ("/health",)


def _quien(request: Request) -> tuple[str | None, str, str | None]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            d = leer_token(auth[7:])
            return d.get("sub"), "", d.get("emp")
        except Exception:  # noqa: BLE001
            pass
    return None, "", None


def _guardar_error(request: Request, ref: str, exc: Exception) -> None:
    uid, _, emp = _quien(request)
    try:
        with SessionLocal() as db:
            if uid and not emp:
                from .models import Usuario
                u = db.get(Usuario, uid)
                emp = u.empresa_id if u else None
            db.add(Auditoria(empresa_id=emp, usuario_id=uid, usuario_correo="", accion="error.500", entidad="servidor", entidad_id=ref,
                             detalle={"ruta": request.url.path, "metodo": request.method, "tipo": type(exc).__name__, "mensaje": str(exc)[:300],
                                      "traza": traceback.format_exc()[-1500:]},
                             ip=request.client.host if request.client else ""))
            db.commit()
    except Exception:  # noqa: BLE001 — registrar el error nunca debe romper la respuesta
        log.exception("No se pudo guardar el error en la auditoría")


async def registrar_peticion(request: Request, call_next):
    ref = uuid.uuid4().hex[:10]
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        log.error("%s %s -> 500 ref=%s %s", request.method, request.url.path, ref, type(exc).__name__, exc_info=True)
        _guardar_error(request, ref, exc)
        return JSONResponse({"detail": f"Error del servidor. Código de referencia: {ref}", "ref": ref}, status_code=500, headers={"x-ref": ref})
    ms = (time.perf_counter() - t0) * 1000
    if not request.url.path.startswith(RUIDO):
        nivel = logging.WARNING if resp.status_code >= 400 else logging.INFO
        log.log(nivel, "%s %s -> %s %.0fms ref=%s", request.method, request.url.path, resp.status_code, ms, ref)
    resp.headers["x-ref"] = ref
    return resp
