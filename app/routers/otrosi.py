"""Otrosíes del contrato: adiciones de valor, prórrogas de plazo e ítems nuevos, con el PDF del documento."""
import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select

from ..audit import registrar
from ..models import Actividad, Contrato, Obra, Otrosi, RegistroBitacora
from ..deps import Ctx, contexto, exigir_modulo
from ..schemas import OtrosiIn
from .contrato import ROLES_ESCRIBE, acceso, obra_de, recalcular_vigente

router = APIRouter(tags=["contrato"])

MAX_PDF = 3 * 1024 * 1024  # el cuerpo de la petición no puede pasar de ~4,5 MB; el base64 crece un tercio


def _pdf(datos64: str, nombre: str) -> tuple[str, int]:
    """Valida el PDF que llega en base64 y devuelve (base64 limpio, tamaño real en bytes)."""
    limpio = datos64.split(",", 1)[1] if datos64.startswith("data:") else datos64
    try:
        crudo = base64.b64decode(limpio, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "El archivo no se pudo leer. Vuelva a seleccionarlo.") from None
    if not crudo.startswith(b"%PDF-"):
        raise HTTPException(422, "El archivo adjunto debe ser un PDF")
    if len(crudo) > MAX_PDF:
        raise HTTPException(422, f"El PDF pesa {len(crudo) / 1048576:.1f} MB y el máximo son {MAX_PDF // 1048576} MB")
    if not nombre.lower().endswith(".pdf"):
        raise HTTPException(422, "El archivo adjunto debe ser un PDF")
    return limpio, len(crudo)


def _otrosies(ctx: Ctx, obra_id: str) -> list[Otrosi]:
    return list(ctx.db.scalars(select(Otrosi).where(Otrosi.obra_id == obra_id, Otrosi.empresa_id == ctx.empresa_id)
                               .order_by(Otrosi.fecha, Otrosi.creado)))


def _de(ctx: Ctx, obra_id: str, oid: str) -> Otrosi:
    o = ctx.db.get(Otrosi, oid)
    if not o or o.empresa_id != ctx.empresa_id or o.obra_id != obra_id:
        raise HTTPException(404, "Otrosí no encontrado")
    return o


def _items(ctx: Ctx, oid: str) -> list[Actividad]:
    return list(ctx.db.scalars(select(Actividad).where(Actividad.otrosi_id == oid, Actividad.empresa_id == ctx.empresa_id)
                               .order_by(Actividad.grupo, Actividad.nombre)))


def _con_avance(ctx: Ctx, ids: list[str]) -> set[str]:
    """De los ítems dados, los que ya tienen avance en la bitácora (ellos o sus subítems)."""
    if not ids:
        return set()
    hijos = {h.id: h.padre_id for h in ctx.db.scalars(select(Actividad).where(Actividad.padre_id.in_(ids), Actividad.empresa_id == ctx.empresa_id))}
    tocados = set(ctx.db.scalars(select(RegistroBitacora.actividad_id).where(
        RegistroBitacora.empresa_id == ctx.empresa_id, RegistroBitacora.actividad_id.in_([*ids, *hijos]))))
    return {hijos.get(t, t) for t in tocados}


def _fila(o: Otrosi, items: list[Actividad]) -> dict:
    suma = round(sum(a.valor_total for a in items), 2)
    return {"id": o.id, "numero": o.numero, "fecha": o.fecha, "descripcion": o.descripcion,
            "valor_adicion": o.valor_adicion, "fecha_fin_nueva": o.fecha_fin_nueva,
            "archivo_nombre": o.archivo_nombre, "archivo_bytes": o.archivo_bytes, "tiene_pdf": bool(o.archivo),
            "creado": o.creado, "creado_por": o.creado_por,
            "items": len(items), "valor_items": suma, "cuadra": abs(suma - o.valor_adicion) < 1,
            "actividades": [{"id": a.id, "item": a.item, "grupo": a.grupo, "capitulo": a.capitulo, "nombre": a.nombre,
                             "ubicacion": a.ubicacion, "um": a.um, "cantidad": a.cantidad,
                             "valor_unitario": a.valor_unitario, "valor_total": a.valor_total, "tarifa": a.tarifa,
                             "advertencias": a.advertencias} for a in items]}


def _salida(ctx: Ctx, obra: Obra) -> dict:
    c = ctx.db.scalar(select(Contrato).where(Contrato.obra_id == obra.id, Contrato.empresa_id == ctx.empresa_id))
    otros = _otrosies(ctx, obra.id)
    filas = [_fila(o, _items(ctx, o.id)) for o in otros]
    adiciones = round(sum(o.valor_adicion for o in otros), 2)
    base = c.valor_base if c else 0.0
    fin_base = (c.fecha_fin_base or c.fecha_fin) if c else None
    return {"obra": {"id": obra.id, "nombre": obra.nombre},
            "tiene_contrato": c is not None,
            "contrato": {"numero": c.numero, "contratante": c.contratante, "fecha_inicio": c.fecha_inicio} if c else None,
            "valor_base": base, "adiciones": adiciones, "valor_vigente": round(base + adiciones, 2),
            "fecha_fin_base": fin_base, "fecha_fin_vigente": c.fecha_fin if c else None,
            "dias_prorroga": ((c.fecha_fin - fin_base).days if c and fin_base else 0),
            "otrosies": filas}


@router.get("/obras/{obra_id}/otrosies")
def listar(obra_id: str, ctx: Ctx = Depends(contexto)):
    acceso(ctx, "contrato", "finanzas")
    return _salida(ctx, obra_de(ctx, obra_id))


@router.get("/obras/{obra_id}/otrosies/{oid}/archivo")
def descargar(obra_id: str, oid: str, ctx: Ctx = Depends(contexto)):
    acceso(ctx, "contrato", "finanzas")
    obra_de(ctx, obra_id)
    o = _de(ctx, obra_id, oid)
    if not o.archivo:
        raise HTTPException(404, "Este otrosí no tiene PDF adjunto")
    nombre = o.archivo_nombre or f"otrosi-{o.numero or oid[:8]}.pdf"
    return Response(base64.b64decode(o.archivo), media_type="application/pdf",
                    headers={"content-disposition": f'inline; filename="{nombre}"'})


def _guardar_items(ctx: Ctx, obra: Obra, o: Otrosi, datos: OtrosiIn) -> None:
    """Reemplaza los ítems del otrosí. No se puede quitar uno que ya tenga avance en la bitácora."""
    existentes = {a.id: a for a in _items(ctx, o.id)}
    usados: set[str] = set()
    for a in datos.actividades:
        vals = a.model_dump(exclude={"id"})
        vals["valor_total"] = round(a.cantidad * a.valor_unitario, 2)
        fila = existentes.get(a.id) if a.id else None
        if a.id and not fila:
            raise HTTPException(422, f"El ítem «{a.nombre}» no pertenece a este otrosí")
        if fila:
            for k, v in vals.items():
                setattr(fila, k, v)
            usados.add(fila.id)
        else:
            ctx.db.add(Actividad(empresa_id=ctx.empresa_id, obra_id=obra.id, otrosi_id=o.id, **vals))
    sobran = [a for id_, a in existentes.items() if id_ not in usados]
    bloqueados = _con_avance(ctx, [a.id for a in sobran])
    for a in sobran:
        if a.id in bloqueados:
            raise HTTPException(409, f"No se puede quitar «{a.nombre}»: ya tiene avance registrado en la bitácora")
        for h in ctx.db.scalars(select(Actividad).where(Actividad.padre_id == a.id, Actividad.empresa_id == ctx.empresa_id)):
            ctx.db.delete(h)
        ctx.db.delete(a)


def _campos(o: Otrosi, datos: OtrosiIn) -> None:
    o.numero, o.fecha, o.descripcion = datos.numero.strip(), datos.fecha, datos.descripcion.strip()
    o.valor_adicion = round(datos.valor_adicion, 2)
    o.fecha_fin_nueva = datos.fecha_fin_nueva
    if datos.archivo is not None:
        if datos.archivo == "":
            o.archivo, o.archivo_nombre, o.archivo_bytes = "", "", 0
        else:
            o.archivo, o.archivo_bytes = _pdf(datos.archivo, datos.archivo_nombre)
            o.archivo_nombre = datos.archivo_nombre


def _validar(ctx: Ctx, obra: Obra, datos: OtrosiIn) -> Contrato:
    c = ctx.db.scalar(select(Contrato).where(Contrato.obra_id == obra.id, Contrato.empresa_id == ctx.empresa_id))
    if not c:
        raise HTTPException(422, "Cargue primero el contrato de la obra")
    inicio = c.fecha_inicio
    if datos.fecha < inicio:
        raise HTTPException(422, "La fecha del otrosí no puede ser anterior al inicio del contrato")
    if datos.fecha_fin_nueva and datos.fecha_fin_nueva <= inicio:
        raise HTTPException(422, "La nueva fecha de terminación debe ser posterior al inicio del contrato")
    return c


@router.post("/obras/{obra_id}/otrosies", status_code=201)
def crear(obra_id: str, datos: OtrosiIn, request: Request, ctx: Ctx = Depends(exigir_modulo("contrato", *ROLES_ESCRIBE))):
    obra = obra_de(ctx, obra_id)
    _validar(ctx, obra, datos)
    o = Otrosi(empresa_id=ctx.empresa_id, obra_id=obra.id, fecha=datos.fecha, creado_por=ctx.usuario.nombre)
    _campos(o, datos)
    ctx.db.add(o)
    ctx.db.flush()
    _guardar_items(ctx, obra, o, datos)
    recalcular_vigente(ctx, obra)
    registrar(ctx.db, accion="otrosi.crear", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=obra.id,
              detalle={"numero": o.numero, "valor": o.valor_adicion, "items": len(datos.actividades)}, request=request)
    ctx.db.commit()
    return _salida(ctx, obra)


@router.put("/obras/{obra_id}/otrosies/{oid}")
def editar(obra_id: str, oid: str, datos: OtrosiIn, request: Request, ctx: Ctx = Depends(exigir_modulo("contrato", *ROLES_ESCRIBE))):
    obra = obra_de(ctx, obra_id)
    _validar(ctx, obra, datos)
    o = _de(ctx, obra_id, oid)
    _campos(o, datos)
    _guardar_items(ctx, obra, o, datos)
    recalcular_vigente(ctx, obra)
    registrar(ctx.db, accion="otrosi.editar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=obra.id,
              detalle={"numero": o.numero, "valor": o.valor_adicion, "items": len(datos.actividades)}, request=request)
    ctx.db.commit()
    return _salida(ctx, obra)


@router.delete("/obras/{obra_id}/otrosies/{oid}")
def eliminar(obra_id: str, oid: str, request: Request, ctx: Ctx = Depends(exigir_modulo("contrato", *ROLES_ESCRIBE))):
    """Borra el otrosí y los ítems que trajo. Se niega si alguno ya tiene avance en la bitácora."""
    obra = obra_de(ctx, obra_id)
    o = _de(ctx, obra_id, oid)
    items = _items(ctx, o.id)
    bloqueados = _con_avance(ctx, [a.id for a in items])
    if bloqueados:
        nombres = ", ".join(f"«{a.nombre}»" for a in items if a.id in bloqueados)
        raise HTTPException(409, f"No se puede eliminar: {nombres} ya tiene avance registrado en la bitácora")
    for a in items:
        for h in ctx.db.scalars(select(Actividad).where(Actividad.padre_id == a.id, Actividad.empresa_id == ctx.empresa_id)):
            ctx.db.delete(h)
        ctx.db.delete(a)
    numero = o.numero
    ctx.db.delete(o)
    ctx.db.flush()
    recalcular_vigente(ctx, obra)
    registrar(ctx.db, accion="otrosi.eliminar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=obra.id,
              detalle={"numero": numero, "items": len(items)}, request=request)
    ctx.db.commit()
    return _salida(ctx, obra)
