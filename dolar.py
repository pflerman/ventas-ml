"""Cotización del dólar oficial (dolarapi.com).

Se carga UNA vez al arrancar la app y queda en memoria hasta cerrarla.
Para refrescar hay que reiniciar.
"""
import requests

# https://dolarapi.com/v1/dolares
# Opciones: oficial, blue, bolsa (MEP), contadoconliqui, tarjeta, mayorista, cripto
_ENDPOINT = "https://dolarapi.com/v1/dolares/oficial"

_valor: float | None = None
_loaded = False


def cargar() -> float | None:
    """Trae la cotización (venta) y la cachea. Retorna el valor o None si falla."""
    global _valor, _loaded
    try:
        r = requests.get(_ENDPOINT, timeout=5)
        r.raise_for_status()
        data = r.json()
        _valor = float(data["venta"])
    except Exception:
        _valor = None
    _loaded = True
    return _valor


def get() -> float | None:
    return _valor


def loaded() -> bool:
    return _loaded
