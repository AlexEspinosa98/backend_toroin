"""Inventario y bodega: materiales, kardex por obra, traslados y consumo por actividad."""
from datetime import date
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from ..audit import registrar
from ..deps import Ctx, exigir_modulo
from ..models import Actividad, Material, MovimientoInv
from ..schemas import MaterialIn, MovimientoIn, TrasladoIn
from .contrato import obra_de

router = APIRouter(prefix="/inventario", tags=["inventario"])
ROLES = ("admin", "gerencia", "residente")


def _fila(x) -> dict:
    return {c.name: getattr(x, c.name) for c in x.__table__.columns if c.name != "empresa_id"}


def material_de(ctx: Ctx, mid: str) -> Material:
    m = ctx.db.get(Material, mid)
    if not m or m.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "Material no encontrado")
    return m


def saldo(db, empresa_id: str, material_id: str, obra_id: str) -> float:
    return float(db.scalar(select(func.coalesce(func.sum(MovimientoInv.cantidad), 0.0)).where(
        MovimientoInv.empresa_id == empresa_id, MovimientoInv.material_id == material_id, MovimientoInv.obra_id == obra_id)) or 0.0)


def costo_promedio(db, empresa_id: str, material_id: str) -> float:
    """Costo promedio ponderado de todo lo que ha entrado (con costo)."""
    q = select(func.sum(MovimientoInv.cantidad * MovimientoInv.costo_unitario), func.sum(MovimientoInv.cantidad)).where(
        MovimientoInv.empresa_id == empresa_id, MovimientoInv.material_id == material_id, MovimientoInv.cantidad > 0, MovimientoInv.costo_unitario > 0)
    valor, cant = db.execute(q).one()
    return float(valor / cant) if cant else 0.0


def anotar_entrada(ctx: Ctx, material_id: str, obra_id: str, fecha: date, cantidad: float, costo: float, referencia: str, detalle: str = "") -> MovimientoInv:
    mv = MovimientoInv(empresa_id=ctx.empresa_id, material_id=material_id, obra_id=obra_id, fecha=fecha, tipo="entrada", cantidad=cantidad,
                       costo_unitario=costo, referencia=referencia, detalle=detalle, registrado_por=ctx.usuario.correo)
    ctx.db.add(mv)
    return mv


def _materiales(ctx: Ctx, obra_id: str | None) -> list[dict]:
    db, e = ctx.db, ctx.empresa_id
    mats = list(db.scalars(select(Material).where(Material.empresa_id == e).order_by(Material.nombre)))
    q = select(MovimientoInv.material_id, MovimientoInv.obra_id, func.sum(MovimientoInv.cantidad)).where(MovimientoInv.empresa_id == e)
    por_obra: dict[str, dict[str, float]] = {}
    for mid, oid, tot in db.execute(q.group_by(MovimientoInv.material_id, MovimientoInv.obra_id)):
        por_obra.setdefault(mid, {})[oid] = float(tot or 0)
    cq = select(MovimientoInv.material_id, func.sum(MovimientoInv.cantidad * MovimientoInv.costo_unitario), func.sum(MovimientoInv.cantidad)).where(
        MovimientoInv.empresa_id == e, MovimientoInv.cantidad > 0, MovimientoInv.costo_unitario > 0).group_by(MovimientoInv.material_id)
    costos = {mid: (float(v) / float(c) if c else 0.0) for mid, v, c in db.execute(cq)}
    out = []
    for m in mats:
        bodegas = por_obra.get(m.id, {})
        stock = bodegas.get(obra_id, 0.0) if obra_id else sum(bodegas.values())
        bajo = [oid for oid, s in bodegas.items() if m.stock_minimo > 0 and s <= m.stock_minimo and (not obra_id or oid == obra_id)]
        cp = costos.get(m.id, 0.0)
        out.append({**_fila(m), "stock": stock, "por_obra": [{"obra_id": o, "stock": s} for o, s in bodegas.items()], "costo_promedio": cp, "valor": stock * cp, "bajo_minimo": bool(bajo), "obras_bajo_minimo": bajo})
    return out


@router.get("/materiales")
def listar_materiales(obra_id: str | None = None, ctx: Ctx = Depends(exigir_modulo("inventario"))):
    if obra_id:
        obra_de(ctx, obra_id)
    return _materiales(ctx, obra_id)


@router.post("/materiales", status_code=201)
def crear_material(datos: MaterialIn, request: Request, ctx: Ctx = Depends(exigir_modulo("inventario", *ROLES))):
    if ctx.db.scalar(select(Material.id).where(Material.empresa_id == ctx.empresa_id, func.lower(Material.nombre) == datos.nombre.strip().lower())):
        raise HTTPException(409, "Ya existe un material con ese nombre")
    m = Material(empresa_id=ctx.empresa_id, **{**datos.model_dump(), "nombre": datos.nombre.strip()})
    ctx.db.add(m)
    ctx.db.flush()
    registrar(ctx.db, accion="material.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="material", entidad_id=m.id, request=request)
    ctx.db.commit()
    return _fila(m)


@router.patch("/materiales/{mid}")
def editar_material(mid: str, datos: MaterialIn, request: Request, ctx: Ctx = Depends(exigir_modulo("inventario", *ROLES))):
    m = material_de(ctx, mid)
    otro = ctx.db.scalar(select(Material.id).where(Material.empresa_id == ctx.empresa_id, func.lower(Material.nombre) == datos.nombre.strip().lower(), Material.id != m.id))
    if otro:
        raise HTTPException(409, "Ya existe un material con ese nombre")
    for k, v in datos.model_dump().items():
        setattr(m, k, v.strip() if k == "nombre" else v)
    registrar(ctx.db, accion="material.editar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="material", entidad_id=m.id, request=request)
    ctx.db.commit()
    return _fila(m)


@router.delete("/materiales/{mid}", status_code=204)
def borrar_material(mid: str, request: Request, ctx: Ctx = Depends(exigir_modulo("inventario", "admin", "gerencia"))):
    m = material_de(ctx, mid)
    if ctx.db.scalar(select(MovimientoInv.id).where(MovimientoInv.material_id == m.id).limit(1)):
        raise HTTPException(409, "El material tiene movimientos: desactívelo en lugar de eliminarlo")
    ctx.db.delete(m)
    registrar(ctx.db, accion="material.eliminar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="material", entidad_id=mid, request=request)
    ctx.db.commit()


@router.get("/movimientos")
def movimientos(obra_id: str | None = None, material_id: str | None = None, limite: int = 200, ctx: Ctx = Depends(exigir_modulo("inventario"))):
    q = select(MovimientoInv).where(MovimientoInv.empresa_id == ctx.empresa_id)
    if obra_id:
        q = q.where(MovimientoInv.obra_id == obra_id)
    if material_id:
        q = q.where(MovimientoInv.material_id == material_id)
    return [_fila(x) for x in ctx.db.scalars(q.order_by(MovimientoInv.fecha.desc(), MovimientoInv.creado.desc()).limit(min(max(limite, 1), 1000)))]


@router.get("/kardex")
def kardex(material_id: str, obra_id: str, ctx: Ctx = Depends(exigir_modulo("inventario"))):
    m = material_de(ctx, material_id)
    obra_de(ctx, obra_id)
    filas, acumulado = [], 0.0
    for x in ctx.db.scalars(select(MovimientoInv).where(MovimientoInv.empresa_id == ctx.empresa_id, MovimientoInv.material_id == m.id, MovimientoInv.obra_id == obra_id)
                            .order_by(MovimientoInv.fecha, MovimientoInv.creado)):
        acumulado += x.cantidad
        filas.append({**_fila(x), "saldo": acumulado})
    return {"material": _fila(m), "saldo": acumulado, "movimientos": filas}


@router.post("/movimientos", status_code=201)
def registrar_movimiento(datos: MovimientoIn, request: Request, ctx: Ctx = Depends(exigir_modulo("inventario", *ROLES))):
    m = material_de(ctx, datos.material_id)
    obra_de(ctx, datos.obra_id)
    if not m.activo:
        raise HTTPException(409, "El material está desactivado")
    if datos.tipo in ("entrada", "salida") and datos.cantidad <= 0:
        raise HTTPException(422, "La cantidad debe ser mayor que cero")
    if datos.tipo == "ajuste" and datos.cantidad == 0:
        raise HTTPException(422, "El ajuste no puede ser cero")
    if datos.actividad_id:
        a = ctx.db.get(Actividad, datos.actividad_id)
        if datos.tipo != "salida" or not a or a.empresa_id != ctx.empresa_id or a.obra_id != datos.obra_id:
            raise HTTPException(422, "La actividad solo aplica a salidas y debe ser de la misma obra")
    firmada = -datos.cantidad if datos.tipo == "salida" else datos.cantidad
    actual = saldo(ctx.db, ctx.empresa_id, m.id, datos.obra_id)
    if firmada < 0 and actual + firmada < -1e-9:
        raise HTTPException(409, f"Saldo insuficiente de «{m.nombre}»: hay {actual:g} {m.unidad} en esa bodega")
    costo = datos.costo_unitario if datos.tipo == "entrada" else costo_promedio(ctx.db, ctx.empresa_id, m.id)
    mv = MovimientoInv(empresa_id=ctx.empresa_id, material_id=m.id, obra_id=datos.obra_id, fecha=datos.fecha, tipo=datos.tipo, cantidad=firmada, costo_unitario=costo,
                       actividad_id=datos.actividad_id, referencia=datos.referencia, detalle=datos.detalle, registrado_por=ctx.usuario.correo)
    ctx.db.add(mv)
    ctx.db.flush()
    registrar(ctx.db, accion=f"inventario.{datos.tipo}", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="material", entidad_id=m.id,
              detalle={"obra": datos.obra_id, "cantidad": firmada}, request=request)
    ctx.db.commit()
    return {**_fila(mv), "saldo": actual + firmada}


@router.post("/traslados", status_code=201)
def trasladar(datos: TrasladoIn, request: Request, ctx: Ctx = Depends(exigir_modulo("inventario", *ROLES))):
    m = material_de(ctx, datos.material_id)
    obra_de(ctx, datos.obra_origen_id)
    obra_de(ctx, datos.obra_destino_id)
    if datos.obra_origen_id == datos.obra_destino_id:
        raise HTTPException(422, "El origen y el destino deben ser obras distintas")
    actual = saldo(ctx.db, ctx.empresa_id, m.id, datos.obra_origen_id)
    if actual < datos.cantidad - 1e-9:
        raise HTTPException(409, f"Saldo insuficiente de «{m.nombre}» en la obra de origen: hay {actual:g} {m.unidad}")
    costo, tid = costo_promedio(ctx.db, ctx.empresa_id, m.id), str(uuid.uuid4())
    for oid, signo in ((datos.obra_origen_id, -1), (datos.obra_destino_id, 1)):
        ctx.db.add(MovimientoInv(empresa_id=ctx.empresa_id, material_id=m.id, obra_id=oid, fecha=datos.fecha, tipo="traslado", cantidad=signo * datos.cantidad,
                                 costo_unitario=costo, detalle=datos.detalle, traslado_id=tid, registrado_por=ctx.usuario.correo))
    registrar(ctx.db, accion="inventario.traslado", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="material", entidad_id=m.id,
              detalle={"origen": datos.obra_origen_id, "destino": datos.obra_destino_id, "cantidad": datos.cantidad}, request=request)
    ctx.db.commit()
    return {"ok": True, "traslado_id": tid}


@router.get("/consumos")
def consumos(obra_id: str, ctx: Ctx = Depends(exigir_modulo("inventario"))):
    """Material consumido (salidas) por actividad de la obra, valorado a costo promedio."""
    obra_de(ctx, obra_id)
    acts = {a.id: a for a in ctx.db.scalars(select(Actividad).where(Actividad.obra_id == obra_id, Actividad.empresa_id == ctx.empresa_id))}
    mats = {m.id: m for m in ctx.db.scalars(select(Material).where(Material.empresa_id == ctx.empresa_id))}
    filas: dict[tuple[str, str], dict] = {}
    for x in ctx.db.scalars(select(MovimientoInv).where(MovimientoInv.empresa_id == ctx.empresa_id, MovimientoInv.obra_id == obra_id, MovimientoInv.tipo == "salida")):
        a = acts.get(x.actividad_id)
        k = (x.actividad_id or "", x.material_id)
        f = filas.setdefault(k, {"actividad_id": x.actividad_id, "actividad": (a.nombre if a else "Sin actividad"), "material_id": x.material_id,
                                 "material": mats[x.material_id].nombre if x.material_id in mats else "", "unidad": mats[x.material_id].unidad if x.material_id in mats else "", "cantidad": 0.0, "valor": 0.0})
        f["cantidad"] += -x.cantidad
        f["valor"] += -x.cantidad * x.costo_unitario
    return sorted(filas.values(), key=lambda f: (f["actividad"], f["material"]))
