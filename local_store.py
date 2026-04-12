"""Persistencia local en JSON. Reemplaza Turso + ventas_db + productos_lookup.

Todo vive en `data.json` al lado del proyecto. Es 100% local, sync, sin
dependencias externas. Para esta escala (cientos a miles de ventas) la
escritura completa del JSON es instantánea.

Estructura del JSON:
{
    "checks":             ["order_id1", ...],            # ventas marcadas
    "notas":              {"order_id": "texto"},         # nota libre por venta
    "fob":                {"SKU": {"precio": 12.5, "mult": 1, "markup": 1.4}},
    "etiquetas_catalogo": ["ordenador", "blanco", ...],  # valores permitidos
    "etiquetas_por_sku":  {"SKU": ["ordenador", ...]},   # asignaciones
    "neto_manual":        {"order_id": 12345.67},        # neto MP cargado a mano
    "shipping_manual":    {"order_id": 6500.00},         # costo Flex que paga el seller
    "consolidados":       [{"id": "...", "fecha_creacion": "2026-04-09", ...}],
    "liquidacion_links":  {"2026-05-07": ["https://...", "https://..."]}  # links MP por día
}

Patrón de uso:
- init() una vez al arrancar (sync, instantáneo).
- get/set/has/count para todo lo demás. Cada set escribe el JSON entero a
  disco de forma atómica (tmp + rename). No hace falta thread.
"""
import json
import os
import tempfile
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data.json"

_data: dict = {
    "checks": [],
    "notas": {},
    "fob": {},
    "fob_combo": {},
    "etiquetas_catalogo": [],
    "etiquetas_por_sku": {},
    "neto_manual": {},
    "shipping_manual": {},
    "consolidados": [],
    "liquidacion_links": {},
}
_checks_set: set[str] = set()
_loaded = False


# ────────────────────── Carga / guardado ──────────────────────

def init() -> None:
    """Carga el JSON desde disco. Si no existe, lo crea vacío."""
    global _data, _checks_set, _loaded
    if DATA_PATH.exists():
        try:
            with DATA_PATH.open("r", encoding="utf-8") as f:
                loaded = json.load(f)
            # Mergear con defaults para tolerar JSONs viejos a los que les
            # falten claves nuevas.
            for k, v in _data.items():
                loaded.setdefault(k, v if not isinstance(v, (dict, list)) else type(v)())
            _data = loaded
        except (OSError, json.JSONDecodeError):
            # JSON corrupto: arrancamos vacío. No pisamos el archivo todavía,
            # se sobrescribe en el primer save().
            pass
    _checks_set = set(_data.get("checks") or [])
    _loaded = True


def loaded() -> bool:
    return _loaded


def _save() -> None:
    """Escribe el JSON entero a disco de forma atómica."""
    # Sincronizamos checks (set) → lista para serializar.
    _data["checks"] = sorted(_checks_set)
    fd, tmp_path = tempfile.mkstemp(
        prefix="data.", suffix=".tmp", dir=str(DATA_PATH.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, DATA_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ────────────────────── Checks ──────────────────────

def is_checked(order_id: str) -> bool:
    return order_id in _checks_set


def all_checked() -> set[str]:
    return set(_checks_set)


def count_checked() -> int:
    return len(_checks_set)


def set_check(order_id: str, checked: bool) -> None:
    """Marca/desmarca y persiste."""
    if not order_id:
        return
    if checked:
        if order_id in _checks_set:
            return
        _checks_set.add(order_id)
    else:
        if order_id not in _checks_set:
            return
        _checks_set.discard(order_id)
    _save()


# ────────────────────── FOB / multiplicador ──────────────────────


def _fob_entry(sku: str) -> dict | None:
    """Devuelve el dict de FOB para un SKU, sea individual o combo."""
    if not sku:
        return None
    return _data["fob"].get(sku) or _data.get("fob_combo", {}).get(sku) or None


def is_combo(sku: str) -> bool:
    """True si el SKU tiene FOBs de combo configurados."""
    if not sku:
        return False
    return sku in _data.get("fob_combo", {})


def get_fob(sku: str) -> float | None:
    """Devuelve el FOB por unidad. Para combos, devuelve la suma de los items."""
    if not sku:
        return None
    # Combo tiene prioridad si existe
    combo_entry = _data.get("fob_combo", {}).get(sku)
    if combo_entry:
        items = combo_entry.get("items") or []
        if not items:
            return None
        try:
            total = sum(
                float(it.get("precio") or 0) * int(it.get("cant") or 1)
                for it in items
            )
            return total or None
        except (TypeError, ValueError):
            return None
    # Individual
    entry = _data["fob"].get(sku)
    if not entry:
        return None
    try:
        return float(entry.get("precio") or 0) or None
    except (TypeError, ValueError):
        return None


def get_fob_individual(sku: str) -> float | None:
    """Devuelve solo el FOB individual (ignora combos)."""
    if not sku:
        return None
    entry = _data["fob"].get(sku)
    if not entry:
        return None
    try:
        return float(entry.get("precio") or 0) or None
    except (TypeError, ValueError):
        return None


def set_fob(sku: str, precio_fob: float) -> None:
    """Si precio_fob es 0/None, borra el FOB y el multiplicador del SKU.
    Si el SKU era combo, borra el combo y lo pasa a individual."""
    if not sku:
        raise ValueError("SKU vacío")
    # Si estaba como combo, borrar la entrada combo
    _data.get("fob_combo", {}).pop(sku, None)
    if precio_fob is None or precio_fob <= 0:
        _data["fob"].pop(sku, None)
        _save()
        return
    entry = _data["fob"].setdefault(sku, {})
    entry["precio"] = float(precio_fob)
    _save()


def get_fob_combo_items(sku: str) -> list[dict] | None:
    """Devuelve la lista de items del combo [{desc, precio}, ...] o None."""
    if not sku:
        return None
    combo_entry = _data.get("fob_combo", {}).get(sku)
    if not combo_entry:
        return None
    return list(combo_entry.get("items") or [])


def set_fob_combo(sku: str, items: list[dict]) -> None:
    """Guarda FOBs de combo para un SKU. items = [{desc, precio}, ...].
    Si items está vacío, borra el combo. Si el SKU era individual, migra
    mult y markup al combo."""
    if not sku:
        raise ValueError("SKU vacío")
    if not items:
        _data.get("fob_combo", {}).pop(sku, None)
        _save()
        return
    # Migrar mult/markup del individual si existían
    old_individual = _data["fob"].pop(sku, None)
    combo = _data.setdefault("fob_combo", {}).setdefault(sku, {})
    combo["items"] = [
        {
            "desc": (it.get("desc") or "").strip(),
            "precio": float(it.get("precio") or 0),
            "cant": max(1, int(it.get("cant") or 1)),
        }
        for it in items
        if float(it.get("precio") or 0) > 0
    ]
    if not combo["items"]:
        _data["fob_combo"].pop(sku, None)
        _save()
        return
    # Preservar mult y markup si venían del individual
    if old_individual:
        if "mult" in old_individual:
            combo["mult"] = old_individual["mult"]
        if "markup" in old_individual:
            combo["markup"] = old_individual["markup"]
    _save()


def get_multiplicador(sku: str) -> int | None:
    if not sku:
        return None
    entry = _fob_entry(sku) or {}
    val = entry.get("mult")
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def set_multiplicador(sku: str, valor: int) -> None:
    """Requiere que ya exista FOB (individual o combo) para ese SKU."""
    if not sku:
        raise ValueError("SKU vacío")
    if valor is None or valor < 1:
        raise ValueError("El multiplicador debe ser un entero ≥ 1")
    entry = _fob_entry(sku)
    if not entry:
        raise ValueError(
            f"No hay precio FOB cargado para {sku}. Cargá el FOB primero."
        )
    entry["mult"] = int(valor)
    _save()


def get_markup(sku: str) -> float | None:
    """Markup de Andrés para un SKU. None si no hay override (cae al default
    GANANCIA_HERMANO_MULT en el caller)."""
    if not sku:
        return None
    entry = _fob_entry(sku) or {}
    val = entry.get("markup")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def set_markup(sku: str, valor: float | None) -> None:
    """Persiste el markup de Andrés para un SKU. Si valor es None, borra el
    override (vuelve al default). Requiere que ya exista FOB."""
    if not sku:
        raise ValueError("SKU vacío")
    entry = _fob_entry(sku)
    if not entry:
        raise ValueError(
            f"No hay precio FOB cargado para {sku}. Cargá el FOB primero."
        )
    if valor is None:
        if "markup" not in entry:
            return
        entry.pop("markup", None)
    else:
        if valor < 1:
            raise ValueError("El markup debe ser ≥ 1")
        entry["markup"] = float(valor)
    _save()


# ────────────────────── Notas ──────────────────────

def get_nota(order_id: str) -> str:
    if not order_id:
        return ""
    return _data["notas"].get(order_id, "")


def has_nota(order_id: str) -> bool:
    return bool(_data["notas"].get(order_id))


def count_with_nota() -> int:
    return len(_data["notas"])


def all_with_nota() -> set[str]:
    return set(_data["notas"].keys())


def set_nota(order_id: str, nota: str) -> None:
    """Si nota está vacía, borra la entrada."""
    if not order_id:
        return
    if nota:
        if _data["notas"].get(order_id) == nota:
            return
        _data["notas"][order_id] = nota
    else:
        if order_id not in _data["notas"]:
            return
        _data["notas"].pop(order_id, None)
    _save()


# ────────────────────── Etiquetas ──────────────────────

def etiquetas_catalogo() -> list[str]:
    """Lista ordenada de etiquetas posibles (el catálogo)."""
    return sorted(_data["etiquetas_catalogo"])


def add_etiqueta_catalogo(etiqueta: str) -> bool:
    """Agrega una etiqueta al catálogo. Devuelve True si era nueva."""
    et = (etiqueta or "").strip()
    if not et:
        return False
    if et in _data["etiquetas_catalogo"]:
        return False
    _data["etiquetas_catalogo"].append(et)
    _save()
    return True


def remove_etiqueta_catalogo(etiqueta: str) -> None:
    """Borra del catálogo Y de todos los SKUs que la tengan asignada."""
    if etiqueta not in _data["etiquetas_catalogo"]:
        return
    _data["etiquetas_catalogo"].remove(etiqueta)
    for sku, ets in _data["etiquetas_por_sku"].items():
        if etiqueta in ets:
            ets.remove(etiqueta)
    _save()


def get_etiquetas_sku(sku: str) -> list[str]:
    if not sku:
        return []
    return list(_data["etiquetas_por_sku"].get(sku) or [])


def add_etiqueta_a_sku(sku: str, etiqueta: str) -> None:
    """Asigna una etiqueta del catálogo a un SKU. Si la etiqueta no está en
    el catálogo, la agrega también."""
    if not sku or not etiqueta:
        return
    if etiqueta not in _data["etiquetas_catalogo"]:
        _data["etiquetas_catalogo"].append(etiqueta)
    ets = _data["etiquetas_por_sku"].setdefault(sku, [])
    if etiqueta in ets:
        return
    ets.append(etiqueta)
    _save()


def remove_etiqueta_de_sku(sku: str, etiqueta: str) -> None:
    if not sku or not etiqueta:
        return
    ets = _data["etiquetas_por_sku"].get(sku)
    if not ets or etiqueta not in ets:
        return
    ets.remove(etiqueta)
    if not ets:
        _data["etiquetas_por_sku"].pop(sku, None)
    _save()


# ────────────────────── Neto MP manual ──────────────────────

def get_neto_manual(order_id: str) -> float | None:
    """Devuelve el neto MP cargado a mano para una venta, o None si falta."""
    if not order_id:
        return None
    val = _data["neto_manual"].get(order_id)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def set_neto_manual(order_id: str, neto: float | None) -> None:
    """Persiste el neto. Si es None o 0, borra la entrada."""
    if not order_id:
        return
    if neto is None or neto == 0:
        if order_id not in _data["neto_manual"]:
            return
        _data["neto_manual"].pop(order_id, None)
    else:
        _data["neto_manual"][order_id] = float(neto)
    _save()


def count_neto_manual() -> int:
    return len(_data["neto_manual"])


# ────────────────────── Costo de envío manual (Flex) ──────────────────────
# Costo del envío que el seller paga afuera (típicamente Flex con Héctor).
# Si está cargado, se RESTA del neto MP para obtener el "neto efectivo" —
# es plata que ya salió del bolsillo del seller después de cobrar a MP.

def get_shipping_manual(order_id: str) -> float | None:
    if not order_id:
        return None
    val = _data["shipping_manual"].get(order_id)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def set_shipping_manual(order_id: str, costo: float | None) -> None:
    """Persiste el costo. Si es None o 0, borra la entrada."""
    if not order_id:
        return
    if costo is None or costo == 0:
        if order_id not in _data["shipping_manual"]:
            return
        _data["shipping_manual"].pop(order_id, None)
    else:
        _data["shipping_manual"][order_id] = float(costo)
    _save()


def count_shipping_manual() -> int:
    return len(_data["shipping_manual"])


def get_neto_efectivo(order_id: str) -> float | None:
    """Neto MP menos costo de envío manual. Devuelve None si falta el neto.
    Si no hay shipping cargado, devuelve el neto tal cual."""
    neto = get_neto_manual(order_id)
    if neto is None:
        return None
    shipping = get_shipping_manual(order_id) or 0.0
    return neto - shipping


# ────────────────────── Consolidados (sección Consolidados) ──────────────────────
# Lista de tarjetas independientes (no se vinculan con las ventas). Cada
# tarjeta representa una consolidación de pago entre Pablo y Andrés:
#   - fecha_creacion: cuándo se cargó (manual)
#   - fecha_desde / fecha_hasta: rango de la mercadería que se está consolidando
#   - fecha_pago: cuándo se paga (resaltado en la UI)
#   - monto_deuda: lo que Pablo le debe a Andrés
#   - credito: lo que Andrés le debe a Pablo (sueldo, devoluciones, etc.) → se resta
#   - activo: si está vigente o ya cerrada
#   - facturado: si Andrés ya facturó esa cantidad
#   - nota: texto libre
# Todo manual, no se infiere de ninguna otra parte de la app.

import uuid as _uuid


def list_consolidados() -> list[dict]:
    """Devuelve una copia de la lista entera (en orden de inserción)."""
    return [dict(c) for c in _data.get("consolidados", [])]


def add_consolidado(data: dict) -> str:
    """Agrega una tarjeta nueva. Devuelve el id generado."""
    cid = _uuid.uuid4().hex[:10]
    entry = {
        "id": cid,
        "fecha_creacion": data.get("fecha_creacion") or "",
        "fecha_desde": data.get("fecha_desde") or "",
        "fecha_hasta": data.get("fecha_hasta") or "",
        "fecha_pago": data.get("fecha_pago") or "",
        "monto_deuda": float(data.get("monto_deuda") or 0),
        "credito": float(data.get("credito") or 0),
        "activo": bool(data.get("activo", True)),
        "facturado": bool(data.get("facturado", False)),
        "nota": data.get("nota") or "",
    }
    _data.setdefault("consolidados", []).append(entry)
    _save()
    return cid


def update_consolidado(cid: str, **fields) -> None:
    """Actualiza campos de una tarjeta existente."""
    if not cid:
        return
    for c in _data.get("consolidados", []):
        if c.get("id") == cid:
            for k, v in fields.items():
                if k == "id":
                    continue
                if k in ("monto_deuda", "credito"):
                    c[k] = float(v or 0)
                elif k in ("activo", "facturado"):
                    c[k] = bool(v)
                else:
                    c[k] = v if v is not None else ""
            _save()
            return


def delete_consolidado(cid: str) -> None:
    if not cid:
        return
    cs = _data.get("consolidados", [])
    new = [c for c in cs if c.get("id") != cid]
    if len(new) != len(cs):
        _data["consolidados"] = new
        _save()


def get_consolidado(cid: str) -> dict | None:
    if not cid:
        return None
    for c in _data.get("consolidados", []):
        if c.get("id") == cid:
            return dict(c)
    return None


# ────────────────────── Liquidación: links MP por día ──────────────────────
# Diccionario {fecha_iso: [link1, link2, ...]}. fecha_iso es "YYYY-MM-DD".
# Manual: el usuario pega un link en el día que quiere y se persiste.

def get_links_dia(fecha: str) -> list[str]:
    if not fecha:
        return []
    return list(_data.get("liquidacion_links", {}).get(fecha, []))


def add_link_dia(fecha: str, link: str) -> None:
    if not fecha or not link:
        return
    d = _data.setdefault("liquidacion_links", {})
    d.setdefault(fecha, []).append(link)
    _save()


def remove_link_dia(fecha: str, idx: int) -> None:
    d = _data.get("liquidacion_links", {})
    links = d.get(fecha)
    if not links or idx < 0 or idx >= len(links):
        return
    links.pop(idx)
    if not links:
        d.pop(fecha, None)
    _save()


def dias_con_links() -> set[str]:
    """Devuelve el set de fechas (ISO) que tienen al menos un link."""
    return set(_data.get("liquidacion_links", {}).keys())


def count_links_dia(fecha: str) -> int:
    return len(_data.get("liquidacion_links", {}).get(fecha, []))
