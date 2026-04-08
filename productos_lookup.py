"""Lookup de productos en gestor-productos (Turso) por SKU.

Lee las credenciales del .env de gestor-productos y mantiene un dict
{sku: {nombre, etiquetas, ...}} en memoria para lookups O(1).
"""
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

_GESTOR_ENV = Path.home() / "Proyectos" / "gestor-productos" / ".env"
load_dotenv(_GESTOR_ENV)

TURSO_URL = os.getenv("TURSO_DB_URL", "")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN", "")

# Cache: {sku: {nombre, etiquetas, ...}}
_by_sku: dict[str, dict] = {}
_loaded = False


def _execute(sql: str) -> dict:
    resp = requests.post(
        f"{TURSO_URL}/v2/pipeline",
        headers={"Authorization": f"Bearer {TURSO_TOKEN}"},
        json={"requests": [{"type": "execute", "stmt": {"sql": sql}}, {"type": "close"}]},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    result = data["results"][0]
    if result["type"] == "error":
        raise Exception(f"Turso error: {result['error']['message']}")
    return result["response"]["result"]


def cargar() -> int:
    """Descarga todos los productos y los indexa por SKU. Retorna cantidad."""
    global _by_sku, _loaded
    response = _execute("SELECT sku, nombre, etiquetas FROM productos")
    cols = [c["name"] for c in response["cols"]]
    by_sku: dict[str, dict] = {}
    for row in response["rows"]:
        d = {}
        for i, col in enumerate(cols):
            val = row[i]
            d[col] = val["value"] if val["type"] != "null" else None
        sku = d.get("sku")
        if sku:
            by_sku[sku] = d
    _by_sku = by_sku
    _loaded = True
    return len(_by_sku)


def get(sku: str) -> dict | None:
    """Retorna el producto para un SKU, o None si no existe."""
    if not sku:
        return None
    return _by_sku.get(sku)


def loaded() -> bool:
    return _loaded
