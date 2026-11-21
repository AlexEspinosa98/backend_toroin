"""Compras y proveedores: requisiciones de obra, órdenes de compra y recepción (entra al inventario y a los gastos de la obra)."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select

from ..audit import registrar
from ..deps import Ctx, exigir_modulo
from ..models import Gasto, Material, Obra, OrdenCompra, Proveedor, Requisicion
from ..schemas import EstadoOrdenIn, OrdenIn, ProveedorIn, RecepcionIn, RequisicionIn, ResolverRequisicionIn
from .contrato import obra_de
from .inventario import anotar_entrada

router = APIRouter(prefix="/compras", tags=["compras"])
ROLES_GESTION = ("admin", "gerencia")
ROLES_SOLICITA = ("admin", "gerencia", "residente")


def _fila(x) -> dict:
    return {c.name: getattr(x, c.name) for c in x.__table__.columns if c.name != "empresa_id"}


def _siguiente(ctx: Ctx, modelo) -> int:
    return int(ctx.db.scalar(select(func.coalesce(func.max(modelo.numero), 0)).where(modelo.empresa_id == ctx.empresa_id)) or 0) + 1


def _totales(o: OrdenCompra) -> dict:
    sub = sum(i["cantidad"] * i["precio_unitario"] for i in o.items)
    iva = sub * o.iva_pct / 100
    pend = sum(max(0.0, i["cantidad"] - i.get("recibido", 0)) * i["precio_unitario"] for i in o.items)
    return {"subtotal": sub, "iva": iva, "total": sub + iva, "pendiente": pend * (1 + o.iva_pct / 100)}


def _orden_out(ctx: Ctx, o: OrdenCompra) -> dict:
    p, ob = ctx.db.get(Proveedor, o.proveedor_id), ctx.db.get(Obra, o.obra_id)
    return {**_fila(o), **_totales(o), "proveedor": p.nombre if p else "", "proveedor_nit": p.nit if p else "", "obra": ob.nombre if ob else "",
            "etiqueta": f"OC-{o.numero:04d}", "vencida": bool(o.fecha_entrega and o.fecha_entrega < date.today() and o.estado in ("Emitida", "Parcial"))}


def _items_limpios(ctx: Ctx, items, con_precio: bool) -> list[dict]:
    mats = {m.id: m for m in ctx.db.scalars(select(Material).where(Material.empresa_id == ctx.empresa_id))}
    out = []
    for it in items:
        d = it.model_dump()
        if d["material_id"]:
            m = mats.get(d["material_id"])
            if not m:
                raise HTTPException(422, "Un material no existe en el inventario de la empresa")
            d["descripcion"], d["unidad"] = m.nombre, m.unidad
        elif not d["descripcion"].strip():
            raise HTTPException(422, "Cada ítem necesita un material o una descripción")
        if con_precio:
            d["recibido"] = 0.0
        out.append(d)
    return out


# ---------- Proveedores ----------
@router.get("/proveedores")
def proveedores(ctx: Ctx = Depends(exigir_modulo("compras"))):
    return [_fila(p) for p in ctx.db.scalars(select(Proveedor).where(Proveedor.empresa_id == ctx.empresa_id).order_by(Proveedor.nombre))]


@router.post("/proveedores", status_code=201)
def crear_proveedor(datos: ProveedorIn, request: Request, ctx: Ctx = Depends(exigir_modulo("compras", *ROLES_GESTION))):
    if datos.nit and ctx.db.scalar(select(Proveedor.id).where(Proveedor.empresa_id == ctx.empresa_id, Proveedor.nit == datos.nit)):
        raise HTTPException(409, "Ya existe un proveedor con ese NIT")
    p = Proveedor(empresa_id=ctx.empresa_id, **datos.model_dump())
    ctx.db.add(p)
    ctx.db.flush()
    registrar(ctx.db, accion="proveedor.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="proveedor", entidad_id=p.id, request=request)
    ctx.db.commit()
    return _fila(p)


@router.patch("/proveedores/{pid}")
def editar_proveedor(pid: str, datos: ProveedorIn, request: Request, ctx: Ctx = Depends(exigir_modulo("compras", *ROLES_GESTION))):
    p = ctx.db.get(Proveedor, pid)
    if not p or p.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "No encontrado")
    if datos.nit and ctx.db.scalar(select(Proveedor.id).where(Proveedor.empresa_id == ctx.empresa_id, Proveedor.nit == datos.nit, Proveedor.id != p.id)):
        raise HTTPException(409, "Ya existe un proveedor con ese NIT")
    for k, v in datos.model_dump().items():
        setattr(p, k, v)
    registrar(ctx.db, accion="proveedor.editar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="proveedor", entidad_id=p.id, request=request)
    ctx.db.commit()
    return _fila(p)


# ---------- Requisiciones ----------
def _req_out(ctx: Ctx, r: Requisicion) -> dict:
    ob = ctx.db.get(Obra, r.obra_id)
    return {**_fila(r), "obra": ob.nombre if ob else "", "etiqueta": f"REQ-{r.numero:04d}"}


@router.get("/requisiciones")
def requisiciones(obra_id: str | None = None, estado: str | None = None, ctx: Ctx = Depends(exigir_modulo("compras"))):
    q = select(Requisicion).where(Requisicion.empresa_id == ctx.empresa_id)
    if obra_id:
        q = q.where(Requisicion.obra_id == obra_id)
    if estado:
        q = q.where(Requisicion.estado == estado)
    return [_req_out(ctx, r) for r in ctx.db.scalars(q.order_by(Requisicion.numero.desc()))]


@router.post("/requisiciones", status_code=201)
def crear_requisicion(datos: RequisicionIn, request: Request, ctx: Ctx = Depends(exigir_modulo("compras", *ROLES_SOLICITA))):
    obra_de(ctx, datos.obra_id)
    items = _items_limpios(ctx, datos.items, con_precio=False)
    r = Requisicion(empresa_id=ctx.empresa_id, obra_id=datos.obra_id, numero=_siguiente(ctx, Requisicion), fecha=date.today(), solicitante=ctx.usuario.correo,
                    items=items, observaciones=datos.observaciones)
    ctx.db.add(r)
    ctx.db.flush()
    registrar(ctx.db, accion="requisicion.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="requisicion", entidad_id=r.id, detalle={"items": len(items)}, request=request)
    ctx.db.commit()
    return _req_out(ctx, r)


@router.patch("/requisiciones/{rid}")
def resolver_requisicion(rid: str, datos: ResolverRequisicionIn, request: Request, ctx: Ctx = Depends(exigir_modulo("compras", *ROLES_GESTION))):
    r = ctx.db.get(Requisicion, rid)
    if not r or r.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "No encontrado")
    if r.estado != "Solicitada":
        raise HTTPException(409, f"La requisición ya está {r.estado.lower()}")
    r.estado, r.resuelto_por, r.fecha_resolucion = datos.estado, ctx.usuario.correo, date.today()
    registrar(ctx.db, accion=f"requisicion.{datos.estado.lower()}", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="requisicion", entidad_id=r.id, request=request)
    ctx.db.commit()
    return _req_out(ctx, r)


@router.post("/requisiciones/{rid}/orden", status_code=201)
def orden_desde_requisicion(rid: str, proveedor_id: str, request: Request, ctx: Ctx = Depends(exigir_modulo("compras", *ROLES_GESTION))):
    r = ctx.db.get(Requisicion, rid)
    if not r or r.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "No encontrado")
    if r.estado != "Aprobada":
        raise HTTPException(409, "Solo una requisición aprobada se convierte en orden de compra")
    p = ctx.db.get(Proveedor, proveedor_id)
    if not p or p.empresa_id != ctx.empresa_id or not p.activo:
        raise HTTPException(404, "Proveedor no encontrado")
    items = [{"material_id": i.get("material_id", ""), "descripcion": i["descripcion"], "unidad": i["unidad"], "cantidad": i["cantidad"], "precio_unitario": 0.0, "recibido": 0.0} for i in r.items]
    o = OrdenCompra(empresa_id=ctx.empresa_id, obra_id=r.obra_id, proveedor_id=p.id, requisicion_id=r.id, numero=_siguiente(ctx, OrdenCompra), fecha=date.today(), items=items, creado_por=ctx.usuario.correo)
    r.estado = "Ordenada"
    ctx.db.add(o)
    ctx.db.flush()
    registrar(ctx.db, accion="orden.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="orden_compra", entidad_id=o.id, detalle={"requisicion": r.id}, request=request)
    ctx.db.commit()
    return _orden_out(ctx, o)


# ---------- Órdenes de compra ----------
def _orden(ctx: Ctx, oid: str) -> OrdenCompra:
    o = ctx.db.get(OrdenCompra, oid)
    if not o or o.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "Orden no encontrada")
    return o


def _validar_orden(ctx: Ctx, datos: OrdenIn) -> list[dict]:
    obra_de(ctx, datos.obra_id)
    p = ctx.db.get(Proveedor, datos.proveedor_id)
    if not p or p.empresa_id != ctx.empresa_id or not p.activo:
        raise HTTPException(404, "Proveedor no encontrado o inactivo")
    if datos.fecha_entrega and datos.fecha_entrega < datos.fecha:
        raise HTTPException(422, "La entrega no puede ser anterior a la fecha de la orden")
    return _items_limpios(ctx, datos.items, con_precio=True)


@router.get("/ordenes")
def ordenes(obra_id: str | None = None, estado: str | None = None, proveedor_id: str | None = None, ctx: Ctx = Depends(exigir_modulo("compras"))):
    q = select(OrdenCompra).where(OrdenCompra.empresa_id == ctx.empresa_id)
    if obra_id:
        q = q.where(OrdenCompra.obra_id == obra_id)
    if estado:
        q = q.where(OrdenCompra.estado == estado)
    if proveedor_id:
        q = q.where(OrdenCompra.proveedor_id == proveedor_id)
    return [_orden_out(ctx, o) for o in ctx.db.scalars(q.order_by(OrdenCompra.numero.desc()))]


@router.get("/ordenes/{oid}")
def ver_orden(oid: str, ctx: Ctx = Depends(exigir_modulo("compras"))):
    return _orden_out(ctx, _orden(ctx, oid))


@router.post("/ordenes", status_code=201)
def crear_orden(datos: OrdenIn, request: Request, ctx: Ctx = Depends(exigir_modulo("compras", *ROLES_GESTION))):
    items = _validar_orden(ctx, datos)
    o = OrdenCompra(empresa_id=ctx.empresa_id, numero=_siguiente(ctx, OrdenCompra), creado_por=ctx.usuario.correo, **{**datos.model_dump(exclude={"items"}), "items": items})
    ctx.db.add(o)
    ctx.db.flush()
    registrar(ctx.db, accion="orden.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="orden_compra", entidad_id=o.id, request=request)
    ctx.db.commit()
    return _orden_out(ctx, o)


@router.put("/ordenes/{oid}")
def editar_orden(oid: str, datos: OrdenIn, request: Request, ctx: Ctx = Depends(exigir_modulo("compras", *ROLES_GESTION))):
    o = _orden(ctx, oid)
    if o.estado != "Borrador":
        raise HTTPException(409, "Solo se edita una orden en borrador")
    items = _validar_orden(ctx, datos)
    for k, v in datos.model_dump(exclude={"items"}).items():
        setattr(o, k, v)
    o.items = items
    registrar(ctx.db, accion="orden.editar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="orden_compra", entidad_id=o.id, request=request)
    ctx.db.commit()
    return _orden_out(ctx, o)


@router.patch("/ordenes/{oid}")
def cambiar_estado_orden(oid: str, datos: EstadoOrdenIn, request: Request, ctx: Ctx = Depends(exigir_modulo("compras", *ROLES_GESTION))):
    o = _orden(ctx, oid)
    if datos.estado == "Emitida":
        if o.estado != "Borrador":
            raise HTTPException(409, "Solo se emite una orden en borrador")
        if any(i["precio_unitario"] <= 0 for i in o.items):
            raise HTTPException(422, "Todos los ítems deben tener precio para emitir la orden")
    else:
        if o.estado in ("Recibida", "Cancelada") or any(i.get("recibido", 0) > 0 for i in o.items):
            raise HTTPException(409, "No se puede cancelar una orden con mercancía recibida o ya cerrada")
    o.estado = datos.estado
    registrar(ctx.db, accion=f"orden.{datos.estado.lower()}", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="orden_compra", entidad_id=o.id, request=request)
    ctx.db.commit()
    return _orden_out(ctx, o)


@router.post("/ordenes/{oid}/recibir")
def recibir(oid: str, datos: RecepcionIn, request: Request, ctx: Ctx = Depends(exigir_modulo("compras", "admin", "gerencia", "residente"))):
    o = _orden(ctx, oid)
    if o.estado not in ("Emitida", "Parcial"):
        raise HTTPException(409, "Solo se recibe una orden emitida")
    if datos.fecha > date.today():
        raise HTTPException(422, "La fecha de recepción no puede ser futura")
    items = [dict(i) for i in o.items]
    valor = 0.0
    for r in datos.items:
        if r.indice >= len(items):
            raise HTTPException(422, "Ítem inexistente en la orden")
        it = items[r.indice]
        pendiente = it["cantidad"] - it.get("recibido", 0)
        if r.cantidad > pendiente + 1e-9:
            raise HTTPException(422, f"«{it['descripcion']}»: solo faltan {pendiente:g} {it['unidad']} por recibir")
        it["recibido"] = it.get("recibido", 0) + r.cantidad
        valor += r.cantidad * it["precio_unitario"]
        if it.get("material_id"):
            anotar_entrada(ctx, it["material_id"], o.obra_id, datos.fecha, r.cantidad, it["precio_unitario"], f"OC-{o.numero:04d}", f"Recepción de OC-{o.numero:04d}")
    o.items = items
    o.estado = "Recibida" if all(i.get("recibido", 0) >= i["cantidad"] - 1e-9 for i in items) else "Parcial"
    p = ctx.db.get(Proveedor, o.proveedor_id)
    # la compra recibida es un gasto de la obra (con IVA), para que Finanzas y Presupuesto la vean
    ctx.db.add(Gasto(empresa_id=ctx.empresa_id, obra_id=o.obra_id, fecha=datos.fecha, categoria="Compra de insumos", valor=valor * (1 + o.iva_pct / 100),
                     descripcion=f"OC-{o.numero:04d} · {p.nombre if p else ''}"[:200], origen=f"oc:{o.id}"))
    registrar(ctx.db, accion="orden.recibir", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="orden_compra", entidad_id=o.id, detalle={"valor": valor}, request=request)
    ctx.db.commit()
    return _orden_out(ctx, o)


@router.get("/resumen")
def resumen(ctx: Ctx = Depends(exigir_modulo("compras"))):
    e = ctx.empresa_id
    reqs = list(ctx.db.scalars(select(Requisicion).where(Requisicion.empresa_id == e, Requisicion.estado.in_(("Solicitada", "Aprobada")))))
    abiertas = list(ctx.db.scalars(select(OrdenCompra).where(OrdenCompra.empresa_id == e, OrdenCompra.estado.in_(("Emitida", "Parcial")))))
    hoy = date.today()
    return {"requisiciones_pendientes": sum(1 for r in reqs if r.estado == "Solicitada"), "requisiciones_por_ordenar": sum(1 for r in reqs if r.estado == "Aprobada"),
            "ordenes_abiertas": len(abiertas), "por_recibir": sum(_totales(o)["pendiente"] for o in abiertas),
            "ordenes_vencidas": sum(1 for o in abiertas if o.fecha_entrega and o.fecha_entrega < hoy)}
