"""Persistencia de checks de ventas, precios FOB y notas en Turso (compartida con gestor-productos).

Cache en memoria + write-through, mismo patrón que `gestor-productos/app/db.py`.

- ventas_checks: una fila por order_id marcado. Presencia = checkeado.
- ventas_fob:    una fila por SKU con su precio FOB en USD.
- ventas_notas:  una fila por order_id con la nota libre del usuario.

Las credenciales se leen del .env de gestor-productos (la DB es la misma).
"""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

_GESTOR_ENV = Path.home() / "Proyectos" / "gestor-productos" / ".env"
load_dotenv(_GESTOR_ENV)

TURSO_URL = os.getenv("TURSO_DB_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

# Cache en memoria
_checks: set[str] = set()           # order_ids marcados
_fob: dict[str, float] = {}         # sku -> precio_fob (USD)
_mult: dict[str, int] = {}          # sku -> multiplicador (unidades por publicación)
_notas: dict[str, str] = {}         # order_id -> texto libre de la nota
_loaded = False


def _execute(sql: str, args: list | None = None) -> dict:
    """Ejecuta una query SQL en Turso vía HTTP API."""
    stmt: dict = {"sql": sql}
    if args:
        typed_args = []
        for a in args:
            if a is None:
                typed_args.append({"type": "null"})
            elif isinstance(a, bool):
                typed_args.append({"type": "integer", "value": "1" if a else "0"})
            elif isinstance(a, int):
                typed_args.append({"type": "integer", "value": str(a)})
            elif isinstance(a, float):
                typed_args.append({"type": "float", "value": a})
            else:
                typed_args.append({"type": "text", "value": str(a)})
        stmt["args"] = typed_args

    resp = requests.post(
        f"{TURSO_URL}/v2/pipeline",
        headers={"Authorization": f"Bearer {TURSO_TOKEN}"},
        json={"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    result = data["results"][0]
    if result["type"] == "error":
        raise Exception(f"Turso error: {result['error']['message']}")
    return result["response"]["result"]


def init_db() -> None:
    """Crea las tablas si no existen y carga la cache en memoria."""
    global _loaded
    _execute("""
        CREATE TABLE IF NOT EXISTS ventas_checks (
            order_id TEXT PRIMARY KEY
        )
    """)
    _execute("""
        CREATE TABLE IF NOT EXISTS ventas_fob (
            sku        TEXT PRIMARY KEY,
            precio_fob REAL NOT NULL
        )
    """)
    # Migración: agregar columna multiplicador si no existe (NULL = falta cargar).
    try:
        _execute("ALTER TABLE ventas_fob ADD COLUMN multiplicador INTEGER")
    except Exception:
        pass  # Ya existe
    _execute("""
        CREATE TABLE IF NOT EXISTS ventas_notas (
            order_id   TEXT PRIMARY KEY,
            nota       TEXT NOT NULL,
            updated_at INTEGER
        )
    """)
    _load_cache()
    _loaded = True


def _load_cache() -> None:
    """Descarga checks, FOBs, multiplicadores y notas a memoria. Una request por tabla."""
    global _checks, _fob, _mult, _notas

    resp = _execute("SELECT order_id FROM ventas_checks")
    _checks = {
        row[0]["value"]
        for row in resp["rows"]
        if row[0]["type"] != "null"
    }

    resp = _execute("SELECT sku, precio_fob, multiplicador FROM ventas_fob")
    fob: dict[str, float] = {}
    mult: dict[str, int] = {}
    for row in resp["rows"]:
        if row[0]["type"] == "null":
            continue
        sku = row[0]["value"]
        try:
            fob[sku] = float(row[1]["value"]) if row[1]["type"] != "null" else 0.0
        except (TypeError, ValueError):
            continue
        if row[2]["type"] != "null":
            try:
                mult[sku] = int(row[2]["value"])
            except (TypeError, ValueError):
                pass
    _fob = fob
    _mult = mult

    resp = _execute("SELECT order_id, nota FROM ventas_notas")
    notas: dict[str, str] = {}
    for row in resp["rows"]:
        if row[0]["type"] == "null" or row[1]["type"] == "null":
            continue
        notas[row[0]["value"]] = row[1]["value"]
    _notas = notas


def loaded() -> bool:
    return _loaded


# ---------- Checks ----------

def is_checked(order_id: str) -> bool:
    return order_id in _checks


def all_checked() -> set[str]:
    """Retorna una copia del set de order_ids checkeados."""
    return set(_checks)


def count_checked() -> int:
    return len(_checks)


def mark_local(order_id: str, checked: bool) -> None:
    """Actualiza solo la cache en memoria (sin I/O). Para updates optimistas en main thread."""
    if checked:
        _checks.add(order_id)
    else:
        _checks.discard(order_id)


def persist_check(order_id: str, checked: bool) -> None:
    """Escribe el check en Turso. NO toca la cache. Pensado para correr en un thread."""
    if checked:
        _execute(
            "INSERT OR IGNORE INTO ventas_checks (order_id) VALUES (?)",
            [order_id],
        )
    else:
        _execute("DELETE FROM ventas_checks WHERE order_id = ?", [order_id])


# ---------- Precios FOB ----------

def get_fob(sku: str) -> float | None:
    if not sku:
        return None
    return _fob.get(sku)


def set_fob(sku: str, precio_fob: float) -> None:
    """Write-through. Si precio_fob es 0 o None, borra la fila (también el multiplicador)."""
    if not sku:
        raise ValueError("SKU vacío")
    if precio_fob is None or precio_fob <= 0:
        _execute("DELETE FROM ventas_fob WHERE sku = ?", [sku])
        _fob.pop(sku, None)
        _mult.pop(sku, None)
        return
    _execute(
        "INSERT INTO ventas_fob (sku, precio_fob) VALUES (?, ?) "
        "ON CONFLICT(sku) DO UPDATE SET precio_fob = excluded.precio_fob",
        [sku, float(precio_fob)],
    )
    _fob[sku] = float(precio_fob)


# ---------- Multiplicador ----------

def get_multiplicador(sku: str) -> int | None:
    if not sku:
        return None
    return _mult.get(sku)


def set_multiplicador(sku: str, valor: int) -> None:
    """Write-through. Requiere que ya exista una fila en ventas_fob para ese SKU."""
    if not sku:
        raise ValueError("SKU vacío")
    if valor is None or valor < 1:
        raise ValueError("El multiplicador debe ser un entero ≥ 1")
    valor = int(valor)
    # UPDATE — la fila tiene que existir (el FOB se carga primero).
    resp = _execute(
        "UPDATE ventas_fob SET multiplicador = ? WHERE sku = ?",
        [valor, sku],
    )
    if resp.get("affected_row_count", 0) == 0:
        raise ValueError(
            f"No hay precio FOB cargado para {sku}. Cargá el FOB primero."
        )
    _mult[sku] = valor


# ---------- Notas ----------

def get_nota(order_id: str) -> str:
    """Devuelve la nota del order_id, o "" si no tiene."""
    if not order_id:
        return ""
    return _notas.get(order_id, "")


def has_nota(order_id: str) -> bool:
    return bool(_notas.get(order_id))


def count_with_nota() -> int:
    return len(_notas)


def all_with_nota() -> set[str]:
    """Retorna copia del set de order_ids que tienen nota (no vacía)."""
    return set(_notas.keys())


def set_nota_local(order_id: str, nota: str) -> None:
    """Actualiza solo la cache en memoria. Para updates optimistas main thread.
    Si nota vacía, borra de la cache."""
    if nota:
        _notas[order_id] = nota
    else:
        _notas.pop(order_id, None)


def persist_nota(order_id: str, nota: str) -> None:
    """Escribe la nota en Turso. NO toca la cache. Pensado para correr en thread.
    Si la nota es vacía, borra la fila."""
    import time
    if nota:
        _execute(
            "INSERT INTO ventas_notas (order_id, nota, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(order_id) DO UPDATE SET nota = excluded.nota, "
            "updated_at = excluded.updated_at",
            [order_id, nota, int(time.time())],
        )
    else:
        _execute("DELETE FROM ventas_notas WHERE order_id = ?", [order_id])
