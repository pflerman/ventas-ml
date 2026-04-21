"""Migra data.json → PostgreSQL (una sola vez)."""
import json
from pathlib import Path

import local_store

DATA_PATH = Path(__file__).parent / "data.json"


def migrate():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    local_store.init()

    # Notas
    notas = data.get("notas") or {}
    for oid, nota in notas.items():
        if nota:
            local_store.set_nota(oid, nota)
    print(f"  notas: {len(notas)}")

    # FOB individual
    fob = data.get("fob") or {}
    for sku, entry in fob.items():
        precio = entry.get("precio")
        if precio:
            local_store.set_fob(sku, float(precio))
            mult = entry.get("mult")
            if mult is not None:
                local_store.set_multiplicador(sku, int(mult))
            markup = entry.get("markup")
            if markup is not None:
                local_store.set_markup(sku, float(markup))
    print(f"  fob individual: {len(fob)}")

    # FOB combo
    fob_combo = data.get("fob_combo") or {}
    for sku, entry in fob_combo.items():
        items = entry.get("items") or []
        if items:
            local_store.set_fob_combo(sku, items)
            # mult y markup se migran desde set_fob_combo si estaban en fob individual,
            # pero si están en el combo entry hay que setearlos explícitamente
            mult = entry.get("mult")
            if mult is not None:
                local_store.set_multiplicador(sku, int(mult))
            markup = entry.get("markup")
            if markup is not None:
                local_store.set_markup(sku, float(markup))
    print(f"  fob combo: {len(fob_combo)}")

    # Etiquetas catálogo
    catalogo = data.get("etiquetas_catalogo") or []
    for et in catalogo:
        local_store.add_etiqueta_catalogo(et)
    print(f"  etiquetas catálogo: {len(catalogo)}")

    # Etiquetas por SKU
    ets_sku = data.get("etiquetas_por_sku") or {}
    count_et = 0
    for sku, ets in ets_sku.items():
        for et in ets:
            local_store.add_etiqueta_a_sku(sku, et)
            count_et += 1
    print(f"  etiquetas por SKU: {count_et} asignaciones")

    # Neto manual
    netos = data.get("neto_manual") or {}
    for oid, neto in netos.items():
        local_store.set_neto_manual(oid, float(neto))
    print(f"  neto manual: {len(netos)}")

    # Shipping manual
    shipping = data.get("shipping_manual") or {}
    for oid, costo in shipping.items():
        local_store.set_shipping_manual(oid, float(costo))
    print(f"  shipping manual: {len(shipping)}")

    # Consolidados
    consolidados = data.get("consolidados") or []
    for c in consolidados:
        # Insertar directamente para preservar el id original
        local_store._q(
            """INSERT INTO consolidados
               (id, fecha_creacion, fecha_desde, fecha_hasta, fecha_pago,
                monto_deuda, credito, activo, facturado, nota)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO NOTHING""",
            (c.get("id", ""),
             c.get("fecha_creacion", ""),
             c.get("fecha_desde", ""),
             c.get("fecha_hasta", ""),
             c.get("fecha_pago", ""),
             float(c.get("monto_deuda") or 0),
             float(c.get("credito") or 0),
             bool(c.get("activo", True)),
             bool(c.get("facturado", False)),
             c.get("nota", "")))
    print(f"  consolidados: {len(consolidados)}")

    # Liquidación links
    links = data.get("liquidacion_links") or {}
    count_links = 0
    for fecha, link_list in links.items():
        for link in link_list:
            local_store.add_link_dia(fecha, link)
            count_links += 1
    print(f"  liquidación links: {count_links}")

    print("\nMigración completada OK.")


if __name__ == "__main__":
    print("Migrando data.json → PostgreSQL...")
    migrate()
