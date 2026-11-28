"""Contrato de la obra, actividades contratadas y tablero de avance."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from .. import catalog
from ..audit import registrar
from ..deps import Ctx, contexto, exigir_modulo, modulos_de_usuario
from ..models import Actividad, Asistencia, Contrato, Obra, Otrosi, RegistroBitacora
from ..reglas import aporte_al_padre, ejecutado_de_items, ejecutado_por_actividad, resumen_obra, subitems_por_padre
from ..schemas import ContratoIn, SubitemIn

router = APIRouter(tags=["contrato"])
ROLES_ESCRIBE = ("admin", "gerencia")


def acceso(ctx: Ctx, *modulos: str) -> None:
    """Permite el acceso si la empresa tiene licenciado alguno de los módulos y el rol puede verlo."""
    propios = modulos_de_usuario(ctx.usuario, ctx.est)
    for m in modulos:
        if m in propios:
            return
    raise HTTPException(403, "Su licencia o su rol no permiten ver esta información")


def obra_de(ctx: Ctx, obra_id: str) -> Obra:
    o = ctx.db.get(Obra, obra_id)
    if not o or o.empresa_id != ctx.empresa_id:
        raise HTTPException(404, "No encontrado")
    return o


def _act(a: Actividad, ejec: float = 0.0, padre: Actividad | None = None, hijos: int = 0, mide_por_subitems: bool = False,
         otrosies: dict[str, str] | None = None) -> dict:
    """Una fila de actividad: ítem del contrato (padre_id vacío) o subítem de la empresa."""
    d = {"id": a.id, "item": a.item, "grupo": a.grupo, "capitulo": a.capitulo, "nombre": a.nombre, "ubicacion": a.ubicacion, "um": a.um,
         "cantidad": a.cantidad, "valor_unitario": a.valor_unitario, "valor_total": a.valor_total, "tarifa": a.tarifa,
         "advertencias": a.advertencias, "ejecutado": ejec, "pendiente": max(0.0, a.cantidad - ejec),
         "padre_id": a.padre_id, "equivale": a.equivale, "aporta_avance": a.aporta_avance,
         "otrosi_id": a.otrosi_id, "otrosi": (otrosies or {}).get(a.otrosi_id, ""),
         "subitems": hijos, "registrable": not mide_por_subitems}
    if padre is not None:
        d["padre"] = padre.nombre
        d["um_item"] = padre.um
        d["aporte"] = aporte_al_padre(a, ejec)          # lo ejecutado, en la unidad del ítem del contrato
        d["ingreso"] = (a.equivale if a.aporta_avance else 0.0) * padre.valor_unitario  # lo que el cliente paga por este subítem completo
    return d


def actividades_de(ctx: Ctx, obra_id: str) -> list[Actividad]:
    return list(ctx.db.scalars(select(Actividad).where(Actividad.obra_id == obra_id, Actividad.empresa_id == ctx.empresa_id)
                               .order_by(Actividad.grupo, Actividad.padre_id, Actividad.nombre)))


def filas_actividades(acts: list[Actividad], regs, otrosies: dict[str, str] | None = None) -> list[dict]:
    """Ítems del contrato y, enseguida de cada uno, sus subítems."""
    bruto = ejecutado_por_actividad(regs)
    de_item = ejecutado_de_items(acts, regs)
    hijos = subitems_por_padre(acts)
    filas = []
    for a in acts:
        if a.padre_id:
            continue
        sub = hijos.get(a.id, [])
        filas.append(_act(a, de_item.get(a.id, 0.0), hijos=len(sub), mide_por_subitems=any(h.aporta_avance for h in sub), otrosies=otrosies))
        filas.extend(_act(h, bruto.get(h.id, 0.0), padre=a, otrosies=otrosies) for h in sub)
    return filas


def numeros_de_otrosi(ctx: Ctx, obra_id: str) -> dict[str, str]:
    """id del otrosí → cómo se le nombra en pantalla («Otrosí 2»)."""
    return {o.id: f"Otrosí {o.numero}" if o.numero else "Otrosí s/n"
            for o in ctx.db.scalars(select(Otrosi).where(Otrosi.obra_id == obra_id, Otrosi.empresa_id == ctx.empresa_id))}


def recalcular_vigente(ctx: Ctx, obra: Obra) -> Contrato | None:
    """El valor y el plazo del contrato son siempre los vigentes: los del original más lo que cambien los otrosíes."""
    c = ctx.db.scalar(select(Contrato).where(Contrato.obra_id == obra.id, Contrato.empresa_id == ctx.empresa_id))
    if not c:
        return None
    otros = list(ctx.db.scalars(select(Otrosi).where(Otrosi.obra_id == obra.id, Otrosi.empresa_id == ctx.empresa_id)))
    c.fecha_fin_base = c.fecha_fin_base or c.fecha_fin
    prorrogas = [o for o in otros if o.fecha_fin_nueva]
    # Manda la prórroga del otrosí más reciente, no la fecha más lejana: un otrosí posterior puede recortar el plazo.
    c.fecha_fin = max(prorrogas, key=lambda o: (o.fecha, o.creado)).fecha_fin_nueva if prorrogas else c.fecha_fin_base
    c.valor_total = round(c.valor_base + sum(o.valor_adicion for o in otros), 2)
    return c


def _contrato(c: Contrato) -> dict:
    d = {k.name: getattr(c, k.name) for k in c.__table__.columns if k.name not in ("empresa_id",)}
    return d


def datos_obra(ctx: Ctx, obra: Obra, fecha: date | None = None) -> dict:
    c = ctx.db.scalar(select(Contrato).where(Contrato.obra_id == obra.id, Contrato.empresa_id == ctx.empresa_id))
    acts = actividades_de(ctx, obra.id)
    regs = list(ctx.db.scalars(select(RegistroBitacora).where(RegistroBitacora.obra_id == obra.id, RegistroBitacora.empresa_id == ctx.empresa_id)))
    return {
        "obra": {"id": obra.id, "nombre": obra.nombre, "ciudad": obra.ciudad, "ingresos_proyectados": obra.ingresos_proyectados},
        "contrato": _contrato(c) if c else None,
        "actividades": filas_actividades(acts, regs, numeros_de_otrosi(ctx, obra.id)),
        "resumen": resumen_obra(c, acts, regs, fecha or date.today()),
    }


@router.get("/contratos")
def listar_contratos(ctx: Ctx = Depends(contexto)):
    """Obras de la empresa con el estado de su contrato (para el listado y el tablero general)."""
    acceso(ctx, "contrato", "finanzas", "pagos")
    obras = ctx.db.scalars(select(Obra).where(Obra.empresa_id == ctx.empresa_id).order_by(Obra.nombre))
    out = []
    for o in obras:
        d = datos_obra(ctx, o)
        c = d["contrato"]
        out.append({"obra": d["obra"], "resumen": d["resumen"],
                    "contrato": {k: c[k] for k in ("numero", "contratante", "fecha_inicio", "fecha_fin", "valor_total")} if c else None,
                    "actividades": len(d["actividades"])})
    return out


@router.get("/obras/{obra_id}/presentes")
def presentes_por_dia(obra_id: str, desde: date, hasta: date, ctx: Ctx = Depends(contexto)):
    acceso(ctx, "contrato", "asistencia")
    obra_de(ctx, obra_id)
    filas = ctx.db.scalars(select(Asistencia).where(Asistencia.empresa_id == ctx.empresa_id, Asistencia.obra_id == obra_id,
                                                    Asistencia.fecha >= desde, Asistencia.fecha <= hasta, Asistencia.asistio.is_(True)))
    cuenta: dict[str, int] = {}
    for a in filas:
        cuenta[str(a.fecha)] = cuenta.get(str(a.fecha), 0) + 1
    return cuenta


@router.get("/obras/{obra_id}/contrato")
def ver_contrato(obra_id: str, ctx: Ctx = Depends(contexto)):
    acceso(ctx, "contrato", "bitacora", "pagos", "finanzas", "control_personal")
    return datos_obra(ctx, obra_de(ctx, obra_id))


@router.put("/obras/{obra_id}/contrato")
def guardar_contrato(obra_id: str, datos: ContratoIn, request: Request, ctx: Ctx = Depends(exigir_modulo("contrato", *ROLES_ESCRIBE))):
    obra = obra_de(ctx, obra_id)
    c = ctx.db.scalar(select(Contrato).where(Contrato.obra_id == obra.id))
    cols = datos.model_dump(exclude={"actividades", "ingresos_proyectados", "pagos"})
    pagos = [p.model_dump(mode="json") for p in datos.pagos]
    suma = sum(round(a.cantidad * a.valor_unitario, 2) for a in datos.actividades)
    # Aquí se edita el contrato original. Los otrosíes se suman después, en recalcular_vigente.
    cols["valor_base"] = cols["valor_total"] = datos.valor_total or suma
    cols["fecha_fin_base"] = datos.fecha_fin
    if c:
        for k, v in cols.items():
            setattr(c, k, v)
        c.pagos = pagos
    else:
        ctx.db.add(Contrato(empresa_id=ctx.empresa_id, obra_id=obra.id, pagos=pagos, **cols))

    # Solo los ítems del contrato original: los que trajo un otrosí se editan en su propio otrosí.
    existentes = {a.id: a for a in ctx.db.scalars(select(Actividad).where(Actividad.obra_id == obra.id, Actividad.empresa_id == ctx.empresa_id,
                                                                          Actividad.padre_id == "", Actividad.otrosi_id == ""))}
    de_otrosi = numeros_de_otrosi(ctx, obra.id)
    usados: set[str] = set()
    for a in datos.actividades:
        vals = a.model_dump(exclude={"id"})
        vals["valor_total"] = round(a.cantidad * a.valor_unitario, 2)
        fila = existentes.get(a.id) if a.id else None
        if a.id and not fila:
            ajeno = ctx.db.get(Actividad, a.id)
            if ajeno is not None and ajeno.otrosi_id:
                raise HTTPException(422, f"«{a.nombre}» vino del {de_otrosi.get(ajeno.otrosi_id, 'otrosí')}: edítelo desde allí")
        if fila:
            for k, v in vals.items():
                setattr(fila, k, v)
            usados.add(fila.id)
        else:
            ctx.db.add(Actividad(empresa_id=ctx.empresa_id, obra_id=obra.id, **vals))
    for id_, fila in existentes.items():
        if id_ in usados:
            continue
        if ctx.db.scalar(select(RegistroBitacora.id).where(RegistroBitacora.actividad_id == id_).limit(1)):
            raise HTTPException(409, f"No se puede quitar «{fila.nombre}»: ya tiene avance registrado en la bitácora")
        hijos = list(ctx.db.scalars(select(Actividad).where(Actividad.padre_id == id_, Actividad.empresa_id == ctx.empresa_id)))
        if any(ctx.db.scalar(select(RegistroBitacora.id).where(RegistroBitacora.actividad_id == h.id).limit(1)) for h in hijos):
            raise HTTPException(409, f"No se puede quitar «{fila.nombre}»: sus subítems ya tienen avance registrado")
        for h in hijos:
            ctx.db.delete(h)
        ctx.db.delete(fila)
    obra.ingresos_proyectados = datos.ingresos_proyectados
    ctx.db.flush()
    recalcular_vigente(ctx, obra)
    registrar(ctx.db, accion="contrato.guardar", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="obra", entidad_id=obra.id,
              detalle={"actividades": len(datos.actividades), "valor": cols["valor_total"]}, request=request)
    ctx.db.commit()
    return datos_obra(ctx, obra)


# ---------- Subítems: el ítem del contrato en las unidades con que la empresa paga ----------
def _item_de(ctx: Ctx, obra_id: str, aid: str) -> Actividad:
    a = ctx.db.get(Actividad, aid)
    if not a or a.empresa_id != ctx.empresa_id or a.obra_id != obra_id:
        raise HTTPException(404, "Actividad no encontrada")
    if a.padre_id:
        raise HTTPException(422, "Un subítem no se puede volver a dividir")
    return a


def _salida_subitems(ctx: Ctx, item: Actividad) -> dict:
    hijos = list(ctx.db.scalars(select(Actividad).where(Actividad.padre_id == item.id, Actividad.empresa_id == ctx.empresa_id).order_by(Actividad.nombre)))
    regs = list(ctx.db.scalars(select(RegistroBitacora).where(RegistroBitacora.empresa_id == ctx.empresa_id,
                                                              RegistroBitacora.actividad_id.in_([item.id, *[h.id for h in hijos]])))) if hijos else []
    bruto = ejecutado_por_actividad(regs)
    repartido = sum(h.equivale for h in hijos if h.aporta_avance)
    return {"item": _act(item, ejecutado_de_items([item, *hijos], regs).get(item.id, 0.0), hijos=len(hijos),
                         mide_por_subitems=any(h.aporta_avance for h in hijos)),
            "subitems": [_act(h, bruto.get(h.id, 0.0), padre=item) for h in hijos],
            "repartido": repartido, "sin_repartir": round(item.cantidad - repartido, 6)}


@router.get("/obras/{obra_id}/actividades/{aid}/subitems")
def ver_subitems(obra_id: str, aid: str, ctx: Ctx = Depends(contexto)):
    acceso(ctx, "contrato", "bitacora", "pagos")
    obra_de(ctx, obra_id)
    return _salida_subitems(ctx, _item_de(ctx, obra_id, aid))


@router.put("/obras/{obra_id}/actividades/{aid}/subitems")
def guardar_subitems(obra_id: str, aid: str, datos: list[SubitemIn], request: Request,
                     ctx: Ctx = Depends(exigir_modulo("contrato", *ROLES_ESCRIBE))):
    """Reemplaza los subítems del ítem. No se puede repartir más de lo contratado ni quitar lo que ya tiene avance."""
    obra_de(ctx, obra_id)
    item = _item_de(ctx, obra_id, aid)
    existentes = {h.id: h for h in ctx.db.scalars(select(Actividad).where(Actividad.padre_id == item.id, Actividad.empresa_id == ctx.empresa_id))}

    reparto = sum(d.equivale for d in datos if d.aporta_avance)
    if reparto > item.cantidad + 1e-6:
        raise HTTPException(422, f"Los subítems suman {reparto:g} {item.um} y el ítem solo tiene {item.cantidad:g} {item.um}")
    for d in datos:
        if d.aporta_avance and d.equivale <= 0:
            raise HTTPException(422, f"Indique a cuántos {item.um} del ítem equivale «{d.nombre}»")
        if d.id and d.id not in existentes:
            raise HTTPException(404, f"El subítem «{d.nombre}» ya no existe")

    ejec = ejecutado_por_actividad(list(ctx.db.scalars(select(RegistroBitacora).where(
        RegistroBitacora.empresa_id == ctx.empresa_id, RegistroBitacora.actividad_id.in_(list(existentes)))))) if existentes else {}
    usados: set[str] = set()
    for d in datos:
        hecho = ejec.get(d.id or "", 0.0)
        if hecho > d.cantidad + 1e-6:
            raise HTTPException(422, f"«{d.nombre}» ya tiene {hecho:g} {d.um} ejecutados: no puede quedar en {d.cantidad:g}")
        vals = d.model_dump(exclude={"id"})
        fila = existentes.get(d.id) if d.id else None
        if fila:
            for k, v in vals.items():
                setattr(fila, k, v)
            usados.add(fila.id)
        else:
            ctx.db.add(Actividad(empresa_id=ctx.empresa_id, obra_id=obra_id, padre_id=item.id, item=item.item, grupo=item.grupo,
                                 capitulo=item.capitulo, valor_unitario=0, valor_total=0, **vals))
    for id_, fila in existentes.items():
        if id_ in usados:
            continue
        if ejec.get(id_, 0.0) > 0:
            raise HTTPException(409, f"No se puede quitar «{fila.nombre}»: ya tiene avance registrado en la bitácora")
        ctx.db.delete(fila)

    registrar(ctx.db, accion="contrato.subitems", usuario=ctx.usuario, empresa_id=ctx.empresa_id, entidad="actividad", entidad_id=item.id,
              detalle={"item": item.nombre, "subitems": len(datos), "repartido": reparto}, request=request)
    ctx.db.commit()
    return _salida_subitems(ctx, item)
