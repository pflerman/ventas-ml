"""Persistencia local en JSON. Reemplaza Turso + ventas_db + productos_lookup.

Todo vive en `data.json` al lado del proyecto. Es 100% local, sync, sin
dependencias externas. Para esta escala (cientos a miles de ventas) la
escritura completa del JSON es instantánea.

Estructura del JSON:
{
    "checks":             ["order_id1", ...],            # ventas marcadas
    "notas":              {"order_id": "texto"},         # nota libre por venta
    "fob":                {"SKU": {"precio": 12.5, "mult": 1}},
    "etiquetas_catalogo": ["ordenador", "blanco", ...],  # valores permitidos
    "etiquetas_por_sku":  {"SKU": ["ordenador", ...]}    # asignaciones
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
    "etiquetas_catalogo": [],
    "etiquetas_por_sku": {},
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

def get_fob(sku: str) -> float | None:
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
    """Si precio_fob es 0/None, borra el FOB y el multiplicador del SKU."""
    if not sku:
        raise ValueError("SKU vacío")
    if precio_fob is None or precio_fob <= 0:
        _data["fob"].pop(sku, None)
        _save()
        return
    entry = _data["fob"].setdefault(sku, {})
    entry["precio"] = float(precio_fob)
    _save()


def get_multiplicador(sku: str) -> int | None:
    if not sku:
        return None
    entry = _data["fob"].get(sku) or {}
    val = entry.get("mult")
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def set_multiplicador(sku: str, valor: int) -> None:
    """Requiere que ya exista FOB para ese SKU (mismo contrato que antes)."""
    if not sku:
        raise ValueError("SKU vacío")
    if valor is None or valor < 1:
        raise ValueError("El multiplicador debe ser un entero ≥ 1")
    entry = _data["fob"].get(sku)
    if not entry or not entry.get("precio"):
        raise ValueError(
            f"No hay precio FOB cargado para {sku}. Cargá el FOB primero."
        )
    entry["mult"] = int(valor)
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
