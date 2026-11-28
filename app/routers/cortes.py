"""Cortes de pago: la liquidación de un periodo congelada tal como se pagó, con los préstamos descontados."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from ..audit import registrar
from ..deps import Ctx, contexto, exigir_modulo
from ..models import AbonoPrestamo, Corte, CorteFila, Obra, Prestamo, Trabajador
from ..schemas import CorteEditIn, CorteIn, PrestamoEditIn, PrestamoIn
from .contrato import acceso, obra_de
from .nomina import liquidar_periodo

router = APIRouter(tags=["cortes"])
ROLES_ESCRIBE = ("admin", "gerencia")


# ---------------- Préstamos ----------------
def _prestamo(p: Prestamo, nombre: str = "") -> dict:
    return {"id": p.id, "trabajador_id": p.trabajador_id, "trabajador": nombre, "fecha": p.fecha, "concepto": p.concepto,
            "valor": p.valor, "cuota": p.cuota, "saldo": p.saldo, "activo": p.activo, "notas": p.notas,
            "pagado": p.saldo <= 0.5, "creado": p.creado, "creado_por": p.creado_por}


def _trabajador(ctx: Ctx, tid: str) -> Trabajador:
    t = ctx.db.get(Trabajador, tid)
    if not t or t.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "Trabajador no encontrado")
    return t


def _prestamos_de(ctx: Ctx, trabajador_id: str | None = None, solo_con_saldo: bool = False) -> list[Prestamo]:
    q = [Prestamo.empresa_id == ctx.empresa_id]
    if trabajador_id:
        q.append(Prestamo.trabajador_id == trabajador_id)
    if solo_con_saldo:
        q += [Prestamo.activo.is_(True), Prestamo.saldo > 0]
    return list(ctx.db.scalars(select(Prestamo).where(*q).order_by(Prestamo.fecha, Prestamo.creado)))


@router.get("/prestamos")
def listar_prestamos(trabajador_id: str | None = None, ctx: Ctx = Depends(contexto)):
    acceso(ctx, "pagos")
    ps = _prestamos_de(ctx, trabajador_id)
    nombres = {t.id: t.nombre for t in ctx.db.scalars(select(Trabajador).where(Trabajador.empresa_id == ctx.empresa_id))}
    filas = [_prestamo(p, nombres.get(p.trabajador_id, "")) for p in ps]
    return {"prestamos": filas, "saldo_total": round(sum(p["saldo"] for p in filas), 2)}


@router.post("/prestamos", status_code=201)
def crear_prestamo(datos: PrestamoIn, request: Request, ctx: Ctx = Depends(exigir_modulo("pagos", *ROLES_ESCRIBE))):
    t = _trabajador(ctx, datos.trabajador_id)
    p = Prestamo(empresa_id=ctx.empresa_id, trabajador_id=t.id, fecha=datos.fecha, concepto=datos.concepto.strip(),
                 valor=round(datos.valor, 2), cuota=round(datos.cuota, 2), saldo=round(datos.valor, 2),
                 activo=datos.activo, notas=datos.notas.strip(), creado_por=ctx.usuario.nombre)
    ctx.db.add(p)
    registrar(ctx.db, accion="prestamo.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="trabajador", entidad_id=t.id,
              detalle={"valor": p.valor, "concepto": p.concepto}, request=request)
    ctx.db.commit()
    return _prestamo(p, t.nombre)


@router.put("/prestamos/{pid}")
def editar_prestamo(pid: str, datos: PrestamoEditIn, request: Request, ctx: Ctx = Depends(exigir_modulo("pagos", *ROLES_ESCRIBE))):
    p = ctx.db.get(Prestamo, pid)
    if not p or p.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "Préstamo no encontrado")
    p.concepto, p.cuota, p.activo, p.notas = datos.concepto.strip(), round(datos.cuota, 2), datos.activo, datos.notas.strip()
    registrar(ctx.db, accion="prestamo.editar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="trabajador", entidad_id=p.trabajador_id,
              detalle={"cuota": p.cuota, "activo": p.activo}, request=request)
    ctx.db.commit()
    return _prestamo(p, _trabajador(ctx, p.trabajador_id).nombre)


@router.delete("/prestamos/{pid}", status_code=204)
def borrar_prestamo(pid: str, request: Request, ctx: Ctx = Depends(exigir_modulo("pagos", *ROLES_ESCRIBE))):
    p = ctx.db.get(Prestamo, pid)
    if not p or p.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "Préstamo no encontrado")
    if ctx.db.scalar(select(AbonoPrestamo.id).where(AbonoPrestamo.prestamo_id == p.id).limit(1)):
        raise HTTPException(409, "No se puede borrar: ya tiene abonos de un corte cerrado. Desactívelo si no quiere seguir descontándolo.")
    registrar(ctx.db, accion="prestamo.borrar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="trabajador", entidad_id=p.trabajador_id,
              detalle={"valor": p.valor}, request=request)
    ctx.db.delete(p)
    ctx.db.commit()


# ---------------- Cortes ----------------
def _de(ctx: Ctx, cid: str) -> Corte:
    c = ctx.db.get(Corte, cid)
    if not c or c.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "Corte no encontrado")
    return c


def _filas(ctx: Ctx, corte_id: str) -> list[CorteFila]:
    return list(ctx.db.scalars(select(CorteFila).where(CorteFila.corte_id == corte_id, CorteFila.empresa_id == ctx.empresa_id)
                               .order_by(CorteFila.nombre)))


def _neto(f: CorteFila) -> float:
    return round(f.devengado + f.auxilio - f.salud - f.pension - f.prestamo - f.otras, 2)


def _totales(filas: list[CorteFila]) -> dict:
    return {k: round(sum(getattr(f, k) for f in filas), 2)
            for k in ("devengado", "auxilio", "salud", "pension", "prestamo", "otras", "neto")} | {"personas": len(filas)}


def _fila_json(f: CorteFila) -> dict:
    return {"id": f.id, "trabajador_id": f.trabajador_id, "nombre": f.nombre, "cedula": f.cedula, "cargo": f.cargo,
            "modalidad": f.modalidad, "dias_asistidos": f.dias_asistidos, "dias_descanso": f.dias_descanso,
            "devengado": f.devengado, "auxilio": f.auxilio, "salud": f.salud, "pension": f.pension,
            "prestamo": f.prestamo, "otras": f.otras, "neto": f.neto, "nota": f.nota, "detalle": f.detalle}


def _avisos(c: Corte, filas: list[CorteFila]) -> list[str]:
    """Lo que conviene que el pagador vea antes de cerrar."""
    av = []
    # El domingo se paga en el corte que lo contiene: si el periodo corta en sábado, queda para el siguiente.
    if (c.hasta.weekday() != 6) and any(f.dias_asistidos for f in filas):
        av.append("El periodo no termina en domingo: el descanso de la última semana se paga en el corte siguiente.")
    if any(f.detalle.get("sin_tarifa") for f in filas):
        av.append("Hay avance pagado por ejecutado sin tarifa fijada: esas cantidades no suman al pago.")
    if any(f.detalle.get("ajuste_minimo") for f in filas):
        av.append("A alguien se le completó el mínimo legal por día (art. 132 CST).")
    return av


def _corte_json(ctx: Ctx, c: Corte, con_filas: bool = True) -> dict:
    obra = ctx.db.get(Obra, c.obra_id) if c.obra_id else None
    d = {"id": c.id, "obra_id": c.obra_id, "obra": obra.nombre if obra else "Todas las obras",
         "desde": c.desde, "hasta": c.hasta, "dias": (c.hasta - c.desde).days + 1, "estado": c.estado, "notas": c.notas,
         "totales": c.totales, "config": c.config, "creado": c.creado, "creado_por": c.creado_por,
         "cerrado_en": c.cerrado_en, "cerrado_por": c.cerrado_por}
    if con_filas:
        filas = _filas(ctx, c.id)
        d["filas"] = [_fila_json(f) for f in filas]
        d["avisos"] = _avisos(c, filas)
    return d


def _sugerir_abono(saldo: float, cuota: float, disponible: float) -> float:
    """Lo que se le alcanza a descontar en este corte, sin dejar el neto en negativo."""
    tope = cuota if cuota > 0 else saldo
    return round(max(0.0, min(saldo, tope, disponible)), 2)


def _liquidar_en(ctx: Ctx, c: Corte) -> list[dict]:
    datos = liquidar_periodo(ctx, c.desde, c.hasta, c.obra_id or None, c.hasta)
    c.config = datos["config"]
    return datos["filas"]


def _volcar(ctx: Ctx, c: Corte, conservar: dict[str, tuple[float, float, str]] | None = None) -> None:
    """Reemplaza las filas del corte con la liquidación de hoy, conservando los descuentos ya ajustados a mano."""
    conservar = conservar or {}
    for vieja in _filas(ctx, c.id):
        ctx.db.delete(vieja)
    ctx.db.flush()
    pendientes = {}
    for p in _prestamos_de(ctx, solo_con_saldo=True):
        pendientes.setdefault(p.trabajador_id, []).append(p)
    nuevas = []
    for r in _liquidar_en(ctx, c):
        disponible = r["devengado"] + r["auxilio"] - r["salud"] - r["pension"]
        prestamo_ant, otras_ant, nota_ant = conservar.get(r["trabajador_id"], (None, 0.0, ""))
        if prestamo_ant is None:
            restante = disponible
            prestamo_ant = 0.0
            for p in pendientes.get(r["trabajador_id"], []):
                abono = _sugerir_abono(p.saldo, p.cuota, restante)
                prestamo_ant += abono
                restante -= abono
        f = CorteFila(empresa_id=ctx.empresa_id, corte_id=c.id, trabajador_id=r["trabajador_id"], nombre=r["nombre"],
                      cedula=r["cedula"] or "", cargo=r["cargo"] or "", modalidad=r["modalidad"],
                      dias_asistidos=r["dias"]["dias_asistidos"], dias_descanso=r["dias_descanso"],
                      devengado=round(r["devengado"], 2), auxilio=round(r["auxilio"], 2),
                      salud=round(r["salud"], 2), pension=round(r["pension"], 2),
                      prestamo=round(min(prestamo_ant, max(0.0, disponible)), 2), otras=round(min(otras_ant, max(0.0, disponible)), 2),
                      nota=nota_ant, detalle=_detalle(r))
        f.neto = _neto(f)
        ctx.db.add(f)
        nuevas.append(f)
    ctx.db.flush()
    c.totales = _totales(nuevas)


def _detalle(r: dict) -> dict:
    """Lo que sustenta la cifra, para poder mostrar la planilla aunque después cambie la asistencia."""
    return {"jornal": r["jornal"], "dias_jornal": r["dias_jornal"], "dias_destajo": r["dias_destajo"],
            "basico": round(r["basico"], 2), "basico_jornal": round(r["basico_jornal"], 2),
            "descansos": round(r["descansos"], 2), "promedio_diario": round(r["promedio_diario"], 2),
            "ajuste_minimo": round(r["ajuste_minimo"], 2), "sin_tarifa": r["sin_tarifa"],
            "semanas": [{"domingo": str(s["domingo"]), "laborables": s["laborables"], "asistidos": s["asistidos"],
                         "reconocido": s["reconocido"], "razon": s["razon"]} for s in r["dias"]["semanas"]],
            "ausencias_justificadas": r["dias"]["ausencias_justificadas"],
            "ausencias_injustificadas": r["dias"]["ausencias_injustificadas"],
            "trabajos": [{"fecha": str(x["fecha"]), "actividad": x["actividad"], "ubicacion": x["ubicacion"], "um": x["um"],
                          "parte": x["parte"], "tarifa": x["tarifa"], "valor": round(x["valor"], 2), "pagado": x["pagado"]}
                         for x in r["detalle"]]}


@router.get("/cortes")
def listar(obra_id: str | None = None, ctx: Ctx = Depends(contexto)):
    acceso(ctx, "pagos")
    q = [Corte.empresa_id == ctx.empresa_id]
    if obra_id:
        q.append(Corte.obra_id == obra_id)
    cs = list(ctx.db.scalars(select(Corte).where(*q).order_by(Corte.desde.desc(), Corte.creado.desc())))
    return {"cortes": [_corte_json(ctx, c, con_filas=False) for c in cs]}


@router.get("/cortes/{cid}")
def ver(cid: str, ctx: Ctx = Depends(contexto)):
    acceso(ctx, "pagos")
    return _corte_json(ctx, _de(ctx, cid))


@router.post("/cortes", status_code=201)
def crear(datos: CorteIn, request: Request, ctx: Ctx = Depends(exigir_modulo("pagos", *ROLES_ESCRIBE))):
    """Liquida el periodo y lo guarda abierto, con los préstamos propuestos. Nada se descuenta hasta cerrarlo."""
    if datos.obra_id:
        obra_de(ctx, datos.obra_id)
    if datos.hasta > date.today():
        raise HTTPException(422, "El corte no puede terminar en una fecha futura")
    chocan = ctx.db.scalar(select(Corte).where(
        Corte.empresa_id == ctx.empresa_id, Corte.obra_id == datos.obra_id,
        Corte.desde <= datos.hasta, Corte.hasta >= datos.desde))
    if chocan:
        raise HTTPException(409, f"Ya hay un corte de {chocan.desde} a {chocan.hasta} que se cruza con este periodo")
    c = Corte(empresa_id=ctx.empresa_id, obra_id=datos.obra_id, desde=datos.desde, hasta=datos.hasta,
              notas=datos.notas.strip(), creado_por=ctx.usuario.nombre, totales={}, config={})
    ctx.db.add(c)
    ctx.db.flush()
    _volcar(ctx, c)
    registrar(ctx.db, accion="corte.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="corte", entidad_id=c.id,
              detalle={"desde": str(c.desde), "hasta": str(c.hasta), "neto": c.totales.get("neto")}, request=request)
    ctx.db.commit()
    return _corte_json(ctx, c)


@router.put("/cortes/{cid}")
def editar(cid: str, datos: CorteEditIn, request: Request, ctx: Ctx = Depends(exigir_modulo("pagos", *ROLES_ESCRIBE))):
    """Ajusta los descuentos del corte, o lo vuelve a liquidar si la asistencia cambió. Solo mientras está abierto."""
    c = _de(ctx, cid)
    if c.estado != "abierto":
        raise HTTPException(409, "El corte está cerrado. Reábralo para cambiarlo.")
    c.notas = datos.notas.strip()
    ajustes = {f.id: f for f in datos.filas}
    filas = _filas(ctx, c.id)
    for f in filas:
        a = ajustes.get(f.id)
        if not a:
            continue
        disponible = f.devengado + f.auxilio - f.salud - f.pension
        if a.prestamo + a.otras > disponible + 0.5:
            raise HTTPException(422, f"Los descuentos de {f.nombre} superan lo que se le debe pagar")
        f.prestamo, f.otras, f.nota = round(a.prestamo, 2), round(a.otras, 2), a.nota.strip()
        f.neto = _neto(f)
    if datos.recalcular:
        _volcar(ctx, c, {f.trabajador_id: (f.prestamo, f.otras, f.nota) for f in filas})
    else:
        ctx.db.flush()
        c.totales = _totales(_filas(ctx, c.id))
    registrar(ctx.db, accion="corte.editar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="corte", entidad_id=c.id,
              detalle={"recalculado": datos.recalcular, "neto": c.totales.get("neto")}, request=request)
    ctx.db.commit()
    return _corte_json(ctx, c)


@router.post("/cortes/{cid}/cerrar")
def cerrar(cid: str, request: Request, ctx: Ctx = Depends(exigir_modulo("pagos", *ROLES_ESCRIBE))):
    """Congela el corte y abona a los préstamos lo que se descontó. Desde aquí las cifras ya no cambian."""
    c = _de(ctx, cid)
    if c.estado != "abierto":
        raise HTTPException(409, "Este corte ya está cerrado")
    filas = _filas(ctx, c.id)
    if not filas:
        raise HTTPException(422, "El corte no tiene a nadie: no hay asistencia registrada en ese periodo")
    pendientes: dict[str, list[Prestamo]] = {}
    for p in _prestamos_de(ctx, solo_con_saldo=True):
        pendientes.setdefault(p.trabajador_id, []).append(p)
    for f in filas:
        restante = f.prestamo
        for p in pendientes.get(f.trabajador_id, []):
            if restante <= 0.004:
                break
            abono = round(min(p.saldo, restante), 2)
            p.saldo = round(p.saldo - abono, 2)
            restante = round(restante - abono, 2)
            ctx.db.add(AbonoPrestamo(empresa_id=ctx.empresa_id, prestamo_id=p.id, corte_id=c.id, fecha=c.hasta, valor=abono))
        if restante > 0.5:
            raise HTTPException(422, f"A {f.nombre} se le descuentan {restante:,.0f} de más: no tiene saldo de préstamos por ese valor")
    c.estado, c.cerrado_en, c.cerrado_por = "cerrado", datetime.now(timezone.utc), ctx.usuario.nombre
    c.totales = _totales(filas)
    registrar(ctx.db, accion="corte.cerrar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="corte", entidad_id=c.id,
              detalle={"personas": len(filas), "neto": c.totales.get("neto")}, request=request)
    ctx.db.commit()
    return _corte_json(ctx, c)


@router.post("/cortes/{cid}/reabrir")
def reabrir(cid: str, request: Request, ctx: Ctx = Depends(exigir_modulo("pagos", *ROLES_ESCRIBE))):
    """Deshace los abonos a préstamos y vuelve a dejar el corte editable."""
    c = _de(ctx, cid)
    if c.estado != "cerrado":
        raise HTTPException(409, "Este corte ya está abierto")
    for ab in ctx.db.scalars(select(AbonoPrestamo).where(AbonoPrestamo.corte_id == c.id, AbonoPrestamo.empresa_id == ctx.empresa_id)):
        p = ctx.db.get(Prestamo, ab.prestamo_id)
        if p:
            p.saldo = round(p.saldo + ab.valor, 2)
        ctx.db.delete(ab)
    c.estado, c.cerrado_en, c.cerrado_por = "abierto", None, ""
    registrar(ctx.db, accion="corte.reabrir", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="corte", entidad_id=c.id,
              detalle={"desde": str(c.desde), "hasta": str(c.hasta)}, request=request)
    ctx.db.commit()
    return _corte_json(ctx, c)


@router.delete("/cortes/{cid}", status_code=204)
def borrar(cid: str, request: Request, ctx: Ctx = Depends(exigir_modulo("pagos", *ROLES_ESCRIBE))):
    c = _de(ctx, cid)
    if c.estado != "abierto":
        raise HTTPException(409, "Un corte cerrado no se borra: reábralo primero si de verdad lo quiere eliminar")
    for f in _filas(ctx, c.id):
        ctx.db.delete(f)
    registrar(ctx.db, accion="corte.borrar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="corte", entidad_id=c.id,
              detalle={"desde": str(c.desde), "hasta": str(c.hasta)}, request=request)
    ctx.db.delete(c)
    ctx.db.commit()
