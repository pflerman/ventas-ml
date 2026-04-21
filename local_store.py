"""Persistencia en PostgreSQL remoto (Hetzner VPS) con cache en memoria.

Al arrancar, carga todo en memoria (bulk SELECT). Las lecturas son
instantáneas (dict lookup). Las escrituras van a memoria + DB.
Para sincronizar entre máquinas, reiniciar la app (recarga desde DB).

Conexión configurada por variable de entorno DATABASE_URL o por default
al VPS de PaliShopping.
"""
import os
import uuid as _uuid

import psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://pali:eacYsPU17bcNAIgOAwjOzHCp@5.78.197.139:5432/ventas_ml",
)

_conn = None
_loaded = False

# ── Cache en memoria (se carga en init()) ──
_notas: dict[str, str] = {}
_fob: dict[str, dict] = {}          # {sku: {precio, mult, markup}}
_fob_combo: dict[str, dict] = {}    # {sku: {items: [...], mult, markup}}
_etiquetas_cat: list[str] = []
_etiquetas_sku: dict[str, list[str]] = {}
_neto_manual: dict[str, float] = {}
_shipping_manual: dict[str, float] = {}
_costo_ars: dict[str, dict] = {}   # {sku: {precio, mult}}
_consolidados: list[dict] = []
_liquidacion_links: dict[str, list[str]] = {}


# ────────────────────── Conexión ──────────────────────

def _get_conn():
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(DATABASE_URL)
        _conn.autocommit = True
    return _conn


def _q(sql, params=None, fetch=None):
    """Ejecuta una query. fetch='one'|'all'|None."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch == "one":
                return cur.fetchone()
            elif fetch == "all":
                return cur.fetchall()
            return None
    except psycopg2.OperationalError:
        global _conn
        _conn = None
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch == "one":
                return cur.fetchone()
            elif fetch == "all":
                return cur.fetchall()
            return None


# ────────────────────── Carga inicial ──────────────────────

def init() -> None:
    """Carga todo desde PostgreSQL a memoria."""
    global _loaded, _notas, _fob, _fob_combo, _costo_ars
    global _etiquetas_cat, _etiquetas_sku, _neto_manual, _shipping_manual
    global _consolidados, _liquidacion_links

    _get_conn()

    # Notas
    rows = _q("SELECT order_id, nota FROM notas", fetch="all") or []
    _notas = {r[0]: r[1] for r in rows}

    # FOB individual
    rows = _q("SELECT sku, precio, mult, markup FROM fob", fetch="all") or []
    _fob = {}
    for r in rows:
        _fob[r[0]] = {"precio": r[1], "mult": r[2], "markup": r[3]}

    # FOB combo
    rows = _q("SELECT sku, mult, markup FROM fob_combo", fetch="all") or []
    _fob_combo = {}
    for r in rows:
        _fob_combo[r[0]] = {"mult": r[1], "markup": r[2], "items": []}
    if _fob_combo:
        items = _q(
            "SELECT sku, descripcion, precio, cant FROM fob_combo_items ORDER BY id",
            fetch="all"
        ) or []
        for r in items:
            if r[0] in _fob_combo:
                _fob_combo[r[0]]["items"].append(
                    {"desc": r[1], "precio": r[2], "cant": r[3]}
                )

    # Costo ARS (proveedor Cris)
    rows = _q("SELECT sku, precio, mult FROM costo_ars", fetch="all") or []
    _costo_ars = {}
    for r in rows:
        _costo_ars[r[0]] = {"precio": r[1], "mult": r[2]}

    # Etiquetas catálogo
    rows = _q("SELECT etiqueta FROM etiquetas_catalogo ORDER BY etiqueta",
              fetch="all") or []
    _etiquetas_cat = [r[0] for r in rows]

    # Etiquetas por SKU
    rows = _q("SELECT sku, etiqueta FROM etiquetas_por_sku", fetch="all") or []
    _etiquetas_sku = {}
    for r in rows:
        _etiquetas_sku.setdefault(r[0], []).append(r[1])

    # Neto manual
    rows = _q("SELECT order_id, neto FROM neto_manual", fetch="all") or []
    _neto_manual = {r[0]: float(r[1]) for r in rows}

    # Shipping manual
    rows = _q("SELECT order_id, costo FROM shipping_manual", fetch="all") or []
    _shipping_manual = {r[0]: float(r[1]) for r in rows}

    # Consolidados
    rows = _q("""SELECT id, fecha_creacion, fecha_desde, fecha_hasta, fecha_pago,
                        monto_deuda, credito, activo, facturado, nota
                 FROM consolidados ORDER BY id""", fetch="all") or []
    _consolidados = [
        {
            "id": r[0], "fecha_creacion": r[1] or "", "fecha_desde": r[2] or "",
            "fecha_hasta": r[3] or "", "fecha_pago": r[4] or "",
            "monto_deuda": r[5] or 0, "credito": r[6] or 0,
            "activo": r[7] if r[7] is not None else True,
            "facturado": r[8] if r[8] is not None else False,
            "nota": r[9] or "",
        }
        for r in rows
    ]

    # Liquidación links
    rows = _q("SELECT fecha, link FROM liquidacion_links ORDER BY id",
              fetch="all") or []
    _liquidacion_links = {}
    for r in rows:
        _liquidacion_links.setdefault(r[0], []).append(r[1])

    _loaded = True


def loaded() -> bool:
    return _loaded


# ────────────────────── FOB / multiplicador ──────────────────────

def _fob_entry(sku: str) -> dict | None:
    if not sku:
        return None
    if sku in _fob_combo:
        return _fob_combo[sku]
    if sku in _fob:
        return _fob[sku]
    return None


def is_combo(sku: str) -> bool:
    if not sku:
        return False
    return sku in _fob_combo


def get_fob(sku: str) -> float | None:
    if not sku:
        return None
    # Combo tiene prioridad
    combo = _fob_combo.get(sku)
    if combo:
        items = combo.get("items") or []
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
    entry = _fob.get(sku)
    if not entry:
        return None
    try:
        return float(entry.get("precio") or 0) or None
    except (TypeError, ValueError):
        return None


def get_fob_individual(sku: str) -> float | None:
    if not sku:
        return None
    entry = _fob.get(sku)
    if not entry:
        return None
    try:
        return float(entry.get("precio") or 0) or None
    except (TypeError, ValueError):
        return None


def set_fob(sku: str, precio_fob: float) -> None:
    if not sku:
        raise ValueError("SKU vacío")
    # Si estaba como combo, borrar
    if sku in _fob_combo:
        del _fob_combo[sku]
        _q("DELETE FROM fob_combo_items WHERE sku = %s", (sku,))
        _q("DELETE FROM fob_combo WHERE sku = %s", (sku,))
    if precio_fob is None or precio_fob <= 0:
        _fob.pop(sku, None)
        _q("DELETE FROM fob WHERE sku = %s", (sku,))
        return
    entry = _fob.setdefault(sku, {})
    entry["precio"] = float(precio_fob)
    _q("""INSERT INTO fob (sku, precio) VALUES (%s, %s)
          ON CONFLICT (sku) DO UPDATE SET precio = EXCLUDED.precio""",
       (sku, float(precio_fob)))


def get_fob_combo_items(sku: str) -> list[dict] | None:
    if not sku:
        return None
    combo = _fob_combo.get(sku)
    if not combo:
        return None
    return list(combo.get("items") or [])


def set_fob_combo(sku: str, items: list[dict]) -> None:
    if not sku:
        raise ValueError("SKU vacío")
    if not items:
        _fob_combo.pop(sku, None)
        _q("DELETE FROM fob_combo_items WHERE sku = %s", (sku,))
        _q("DELETE FROM fob_combo WHERE sku = %s", (sku,))
        return
    # Migrar mult/markup del individual si existían
    old_individual = _fob.pop(sku, None)
    old_mult = (old_individual or {}).get("mult")
    old_markup = (old_individual or {}).get("markup")
    if old_individual:
        _q("DELETE FROM fob WHERE sku = %s", (sku,))

    valid_items = [
        {
            "desc": (it.get("desc") or "").strip(),
            "precio": float(it.get("precio") or 0),
            "cant": max(1, int(it.get("cant") or 1)),
        }
        for it in items
        if float(it.get("precio") or 0) > 0
    ]
    if not valid_items:
        _fob_combo.pop(sku, None)
        _q("DELETE FROM fob_combo_items WHERE sku = %s", (sku,))
        _q("DELETE FROM fob_combo WHERE sku = %s", (sku,))
        return

    # Actualizar cache
    combo = _fob_combo.setdefault(sku, {})
    combo["items"] = valid_items
    if old_mult is not None and "mult" not in combo:
        combo["mult"] = old_mult
    if old_markup is not None and "markup" not in combo:
        combo["markup"] = old_markup

    # Actualizar DB
    _q("""INSERT INTO fob_combo (sku, mult, markup) VALUES (%s, %s, %s)
          ON CONFLICT (sku) DO UPDATE SET
            mult = COALESCE(fob_combo.mult, EXCLUDED.mult),
            markup = COALESCE(fob_combo.markup, EXCLUDED.markup)""",
       (sku, old_mult, old_markup))
    _q("DELETE FROM fob_combo_items WHERE sku = %s", (sku,))
    for it in valid_items:
        _q("""INSERT INTO fob_combo_items (sku, descripcion, precio, cant)
              VALUES (%s, %s, %s, %s)""",
           (sku, it["desc"], it["precio"], it["cant"]))


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
    if sku in _fob_combo:
        _q("UPDATE fob_combo SET mult = %s WHERE sku = %s", (int(valor), sku))
    else:
        _q("UPDATE fob SET mult = %s WHERE sku = %s", (int(valor), sku))


def get_markup(sku: str) -> float | None:
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
    db_val = float(valor) if valor is not None else None
    if sku in _fob_combo:
        _q("UPDATE fob_combo SET markup = %s WHERE sku = %s", (db_val, sku))
    else:
        _q("UPDATE fob SET markup = %s WHERE sku = %s", (db_val, sku))


# ────────────────────── Costo ARS (proveedor Cris) ──────────────────────
# Precio directo en pesos argentinos. Sin FOB, sin dólar, sin nacionalización,
# sin markup. El precio es lo que Cris cobra por unidad.

def get_costo_ars(sku: str) -> float | None:
    if not sku:
        return None
    entry = _costo_ars.get(sku)
    if not entry:
        return None
    try:
        return float(entry.get("precio") or 0) or None
    except (TypeError, ValueError):
        return None


def get_costo_ars_mult(sku: str) -> int | None:
    if not sku:
        return None
    entry = _costo_ars.get(sku)
    if not entry:
        return None
    val = entry.get("mult")
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def set_costo_ars(sku: str, precio: float | None, mult: int | None = None) -> None:
    """Guarda el costo ARS. Si precio es None/0, borra la entrada.
    Al setear costo_ars, borra FOB individual y combo si existían (son mutuamente excluyentes)."""
    if not sku:
        raise ValueError("SKU vacío")
    if precio is None or precio <= 0:
        _costo_ars.pop(sku, None)
        _q("DELETE FROM costo_ars WHERE sku = %s", (sku,))
        return
    # Migrar multiplicador del FOB viejo si no se pasó uno explícito
    if mult is None:
        old_mult = get_multiplicador(sku)
        if old_mult is not None:
            mult = old_mult
    # Borrar FOB si existía (un SKU es o FOB o costo ARS, no ambos)
    if sku in _fob:
        del _fob[sku]
        _q("DELETE FROM fob WHERE sku = %s", (sku,))
    if sku in _fob_combo:
        del _fob_combo[sku]
        _q("DELETE FROM fob_combo_items WHERE sku = %s", (sku,))
        _q("DELETE FROM fob_combo WHERE sku = %s", (sku,))
    _costo_ars[sku] = {"precio": float(precio), "mult": int(mult) if mult else None}
    _q("""INSERT INTO costo_ars (sku, precio, mult) VALUES (%s, %s, %s)
          ON CONFLICT (sku) DO UPDATE SET precio = EXCLUDED.precio, mult = EXCLUDED.mult""",
       (sku, float(precio), int(mult) if mult else None))


def set_costo_ars_mult(sku: str, valor: int) -> None:
    if not sku:
        raise ValueError("SKU vacío")
    if valor is None or valor < 1:
        raise ValueError("El multiplicador debe ser un entero ≥ 1")
    entry = _costo_ars.get(sku)
    if not entry:
        raise ValueError(f"No hay costo ARS cargado para {sku}.")
    entry["mult"] = int(valor)
    _q("UPDATE costo_ars SET mult = %s WHERE sku = %s", (int(valor), sku))


def has_costo_ars(sku: str) -> bool:
    if not sku:
        return False
    return sku in _costo_ars


# ────────────────────── Notas ──────────────────────

def get_nota(order_id: str) -> str:
    if not order_id:
        return ""
    return _notas.get(order_id, "")


def has_nota(order_id: str) -> bool:
    return bool(_notas.get(order_id))


def count_with_nota() -> int:
    return len(_notas)


def all_with_nota() -> set[str]:
    return set(_notas.keys())


def set_nota(order_id: str, nota: str) -> None:
    if not order_id:
        return
    if nota:
        if _notas.get(order_id) == nota:
            return
        _notas[order_id] = nota
        _q("""INSERT INTO notas (order_id, nota) VALUES (%s, %s)
              ON CONFLICT (order_id) DO UPDATE SET nota = EXCLUDED.nota""",
           (order_id, nota))
    else:
        if order_id not in _notas:
            return
        _notas.pop(order_id, None)
        _q("DELETE FROM notas WHERE order_id = %s", (order_id,))


# ────────────────────── Etiquetas ──────────────────────

def etiquetas_catalogo() -> list[str]:
    return sorted(_etiquetas_cat)


def add_etiqueta_catalogo(etiqueta: str) -> bool:
    et = (etiqueta or "").strip()
    if not et:
        return False
    if et in _etiquetas_cat:
        return False
    _etiquetas_cat.append(et)
    _q("INSERT INTO etiquetas_catalogo (etiqueta) VALUES (%s) ON CONFLICT DO NOTHING",
       (et,))
    return True


def remove_etiqueta_catalogo(etiqueta: str) -> None:
    if etiqueta not in _etiquetas_cat:
        return
    _etiquetas_cat.remove(etiqueta)
    for sku, ets in _etiquetas_sku.items():
        if etiqueta in ets:
            ets.remove(etiqueta)
    _q("DELETE FROM etiquetas_por_sku WHERE etiqueta = %s", (etiqueta,))
    _q("DELETE FROM etiquetas_catalogo WHERE etiqueta = %s", (etiqueta,))


def get_etiquetas_sku(sku: str) -> list[str]:
    if not sku:
        return []
    return list(_etiquetas_sku.get(sku) or [])


def add_etiqueta_a_sku(sku: str, etiqueta: str) -> None:
    if not sku or not etiqueta:
        return
    if etiqueta not in _etiquetas_cat:
        _etiquetas_cat.append(etiqueta)
        _q("INSERT INTO etiquetas_catalogo (etiqueta) VALUES (%s) ON CONFLICT DO NOTHING",
           (etiqueta,))
    ets = _etiquetas_sku.setdefault(sku, [])
    if etiqueta in ets:
        return
    ets.append(etiqueta)
    _q("""INSERT INTO etiquetas_por_sku (sku, etiqueta) VALUES (%s, %s)
          ON CONFLICT DO NOTHING""", (sku, etiqueta))


def remove_etiqueta_de_sku(sku: str, etiqueta: str) -> None:
    if not sku or not etiqueta:
        return
    ets = _etiquetas_sku.get(sku)
    if not ets or etiqueta not in ets:
        return
    ets.remove(etiqueta)
    if not ets:
        _etiquetas_sku.pop(sku, None)
    _q("DELETE FROM etiquetas_por_sku WHERE sku = %s AND etiqueta = %s",
       (sku, etiqueta))


# ────────────────────── Neto MP manual ──────────────────────

def get_neto_manual(order_id: str) -> float | None:
    if not order_id:
        return None
    val = _neto_manual.get(order_id)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def set_neto_manual(order_id: str, neto: float | None) -> None:
    if not order_id:
        return
    if neto is None or neto == 0:
        if order_id not in _neto_manual:
            return
        _neto_manual.pop(order_id, None)
        _q("DELETE FROM neto_manual WHERE order_id = %s", (order_id,))
    else:
        _neto_manual[order_id] = float(neto)
        _q("""INSERT INTO neto_manual (order_id, neto) VALUES (%s, %s)
              ON CONFLICT (order_id) DO UPDATE SET neto = EXCLUDED.neto""",
           (order_id, float(neto)))


def count_neto_manual() -> int:
    return len(_neto_manual)


# ────────────────────── Costo de envío manual (Flex) ──────────────────────

def get_shipping_manual(order_id: str) -> float | None:
    if not order_id:
        return None
    val = _shipping_manual.get(order_id)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def set_shipping_manual(order_id: str, costo: float | None) -> None:
    if not order_id:
        return
    if costo is None or costo == 0:
        if order_id not in _shipping_manual:
            return
        _shipping_manual.pop(order_id, None)
        _q("DELETE FROM shipping_manual WHERE order_id = %s", (order_id,))
    else:
        _shipping_manual[order_id] = float(costo)
        _q("""INSERT INTO shipping_manual (order_id, costo) VALUES (%s, %s)
              ON CONFLICT (order_id) DO UPDATE SET costo = EXCLUDED.costo""",
           (order_id, float(costo)))


def count_shipping_manual() -> int:
    return len(_shipping_manual)


def get_neto_efectivo(order_id: str) -> float | None:
    neto = get_neto_manual(order_id)
    if neto is None:
        return None
    shipping = get_shipping_manual(order_id) or 0.0
    return neto - shipping


# ────────────────────── Consolidados ──────────────────────

def list_consolidados() -> list[dict]:
    return [dict(c) for c in _consolidados]


def add_consolidado(data: dict) -> str:
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
    _consolidados.append(entry)
    _q("""INSERT INTO consolidados
          (id, fecha_creacion, fecha_desde, fecha_hasta, fecha_pago,
           monto_deuda, credito, activo, facturado, nota)
          VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
       (cid, entry["fecha_creacion"], entry["fecha_desde"],
        entry["fecha_hasta"], entry["fecha_pago"],
        entry["monto_deuda"], entry["credito"],
        entry["activo"], entry["facturado"], entry["nota"]))
    return cid


def update_consolidado(cid: str, **fields) -> None:
    if not cid:
        return
    # Actualizar cache
    for c in _consolidados:
        if c.get("id") == cid:
            sets = []
            vals = []
            for k, v in fields.items():
                if k == "id":
                    continue
                if k in ("monto_deuda", "credito"):
                    c[k] = float(v or 0)
                    sets.append(f"{k} = %s")
                    vals.append(float(v or 0))
                elif k in ("activo", "facturado"):
                    c[k] = bool(v)
                    sets.append(f"{k} = %s")
                    vals.append(bool(v))
                else:
                    c[k] = v if v is not None else ""
                    sets.append(f"{k} = %s")
                    vals.append(v if v is not None else "")
            if sets:
                vals.append(cid)
                _q(f"UPDATE consolidados SET {', '.join(sets)} WHERE id = %s", vals)
            return


def delete_consolidado(cid: str) -> None:
    if not cid:
        return
    new = [c for c in _consolidados if c.get("id") != cid]
    if len(new) != len(_consolidados):
        _consolidados.clear()
        _consolidados.extend(new)
        _q("DELETE FROM consolidados WHERE id = %s", (cid,))


def get_consolidado(cid: str) -> dict | None:
    if not cid:
        return None
    for c in _consolidados:
        if c.get("id") == cid:
            return dict(c)
    return None


# ────────────────────── Liquidación: links MP por día ──────────────────────

def get_links_dia(fecha: str) -> list[str]:
    if not fecha:
        return []
    return list(_liquidacion_links.get(fecha, []))


def add_link_dia(fecha: str, link: str) -> None:
    if not fecha or not link:
        return
    _liquidacion_links.setdefault(fecha, []).append(link)
    _q("INSERT INTO liquidacion_links (fecha, link) VALUES (%s, %s)",
       (fecha, link))


def remove_link_dia(fecha: str, idx: int) -> None:
    links = _liquidacion_links.get(fecha)
    if not links or idx < 0 or idx >= len(links):
        return
    links.pop(idx)
    if not links:
        _liquidacion_links.pop(fecha, None)
    # En DB: borrar y reinsertar para mantener el orden
    _q("DELETE FROM liquidacion_links WHERE fecha = %s", (fecha,))
    for link in (links or []):
        _q("INSERT INTO liquidacion_links (fecha, link) VALUES (%s, %s)",
           (fecha, link))


def dias_con_links() -> set[str]:
    return set(_liquidacion_links.keys())


def count_links_dia(fecha: str) -> int:
    return len(_liquidacion_links.get(fecha, []))
