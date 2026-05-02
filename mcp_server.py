"""MCP Server para ventas-ml — costeo y datos locales de ventas MercadoLibre."""
import sys
import os
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import subprocess
from collections import defaultdict
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(__file__))

from mcp.server.fastmcp import FastMCP
import local_store

ML_MCP_DIR = Path.home() / "Proyectos" / "mercadolibre-mcp"
CREDENTIALS_PATH = (
    Path.home() / "Proyectos" / "ml-scripts" / "config" / "ml_credentials_palishopping.json"
)
sys.path.insert(0, str(ML_MCP_DIR))
from ml_auth import MLAuth

USER_ID = 24192412
API_URL = "https://api.mercadolibre.com/orders/search"
_TZ_AR = timezone(timedelta(hours=-3))

mcp = FastMCP("ventas-ml")


def _parse_ar(iso: str):
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_TZ_AR)


def _fetch_orders_by_date(auth: MLAuth, fecha_desde: str, fecha_hasta: str) -> list[dict]:
    desde_date = datetime.strptime(fecha_desde, "%Y-%m-%d").date()
    hasta_date = datetime.strptime(fecha_hasta, "%Y-%m-%d").date()
    query_desde = (desde_date - timedelta(days=1)).isoformat()
    query_hasta = (hasta_date + timedelta(days=1)).isoformat()
    all_results = []
    offset = 0
    while True:
        params = {
            "seller": USER_ID,
            "sort": "date_desc",
            "order.date_created.from": f"{query_desde}T00:00:00.000-03:00",
            "order.date_created.to": f"{query_hasta}T23:59:59.999-03:00",
            "offset": offset,
            "limit": 50,
        }
        url = f"{API_URL}?{urlencode(params)}"
        req = Request(url, headers={"Authorization": f"Bearer {auth.access_token}"})
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        results = data.get("results") or []
        all_results.extend(results)
        total = data.get("paging", {}).get("total", 0)
        offset += len(results)
        if offset >= total or not results:
            break
    filtered = []
    for order in all_results:
        dt = _parse_ar(order.get("date_created", ""))
        if dt and desde_date <= dt.date() <= hasta_date:
            filtered.append(order)
    return filtered


def _extract_sku(order_item: dict) -> str:
    item = order_item.get("item") or {}
    sku = item.get("seller_sku")
    if sku:
        return str(sku)
    sku = item.get("seller_custom_field")
    if sku:
        return str(sku)
    for attr in item.get("variation_attributes") or []:
        if (attr.get("id") or "").upper() == "SELLER_SKU":
            v = attr.get("value_name")
            if v:
                return str(v)
    return ""


@mcp.tool()
def ping() -> str:
    """Verifica que el server está conectado a la base de datos."""
    local_store.init()
    return f"OK — {local_store.count_neto_manual()} netos cargados, {local_store.count_shipping_manual()} envíos cargados"


@mcp.tool()
def ventas_por_fecha(fecha_desde: str, fecha_hasta: str = "") -> str:
    """Lista ventas de MercadoLibre agrupadas por fecha.

    Args:
        fecha_desde: Fecha inicio en formato YYYY-MM-DD.
        fecha_hasta: Fecha fin en formato YYYY-MM-DD. Si no se pasa, usa fecha_desde (un solo día).
    """
    if not fecha_hasta:
        fecha_hasta = fecha_desde
    auth = MLAuth(str(CREDENTIALS_PATH))
    orders = _fetch_orders_by_date(auth, fecha_desde, fecha_hasta)

    if not orders:
        return f"No hay ventas entre {fecha_desde} y {fecha_hasta}."

    by_day: dict[str, list[dict]] = defaultdict(list)
    for order in orders:
        dt = _parse_ar(order.get("date_created", ""))
        day_key = dt.strftime("%Y-%m-%d") if dt else "sin-fecha"
        items = order.get("order_items") or []
        first = items[0] if items else {}
        item_data = first.get("item") or {}
        quantity = int(first.get("quantity") or 1)
        unit_price = float(first.get("unit_price") or 0)
        total = float(order.get("total_amount") or unit_price * quantity)
        sku = _extract_sku(first)
        pack_id = order.get("pack_id")
        order_id = order.get("id")
        by_day[day_key].append({
            "titulo": item_data.get("title", "(sin título)"),
            "cantidad": quantity,
            "precio_unitario": unit_price,
            "total": total,
            "sku": sku or None,
            "pack_id": pack_id,
            "order_id": order_id,
        })

    lines = []
    grand_total = 0.0
    grand_count = 0
    for day in sorted(by_day.keys(), reverse=True):
        ventas = by_day[day]
        day_total = sum(v["total"] for v in ventas)
        grand_total += day_total
        grand_count += len(ventas)
        lines.append(f"## {day}  —  {len(ventas)} venta{'s' if len(ventas) != 1 else ''}  —  ${day_total:,.2f}")
        for v in ventas:
            sku_part = f"  ·  SKU: {v['sku']}" if v["sku"] else ""
            id_part = f"pack_id={v['pack_id']}" if v["pack_id"] else f"order_id={v['order_id']}"
            lines.append(
                f"  {v['cantidad']}x  {v['titulo']}{sku_part}  ·  "
                f"${v['precio_unitario']:,.2f} c/u  ·  Total: ${v['total']:,.2f}  ({id_part})"
            )
        lines.append("")

    if len(by_day) > 1 or grand_count > 1:
        lines.append(f"**Total general: {grand_count} ventas  —  ${grand_total:,.2f}**")

    return "\n".join(lines)


FLEX_ZONA_1 = 4490
FLEX_ZONA_2 = 6490
FLEX_ZONA_3 = 8490

_ZONA_1_CIUDADES = {
    "hurlingham", "morón", "moron", "san martín", "san martin",
    "tres de febrero", "caseros", "ciudadela", "santos lugares",
    "villa bosch", "el palomar", "haedo", "ramos mejía", "ramos mejia",
    "villa sarmiento", "villa tesei", "william morris",
}

_ZONA_2_CIUDADES = {
    "ituzaingó", "ituzaingo", "malvinas argentinas",
    "san isidro", "san miguel", "vicente lópez", "vicente lopez",
    "la matanza", "villa luzuriaga", "san justo", "ramos mejía",
    "tablada", "aldo bonzi", "tapiales", "isidro casanova",
    "la tablada", "ciudad evita",
    "olivos", "florida", "munro", "martínez", "martinez",
    "acassuso", "béccar", "beccar", "boulogne",
    "bella vista", "muñiz", "muniz", "josé c. paz", "jose c. paz",
    "los polvorines", "grand bourg", "tortuguitas",
    "campo de mayo", "don torcuato", "del viso",
}

_CABA_STATE = "capital federal"


def _costo_flex(receiver_address: dict) -> int:
    city = (receiver_address.get("city", {}).get("name") or "").strip().lower()
    state = (receiver_address.get("state", {}).get("name") or "").strip().lower()
    if state == _CABA_STATE:
        return FLEX_ZONA_2
    if city in _ZONA_1_CIUDADES:
        return FLEX_ZONA_1
    if city in _ZONA_2_CIUDADES:
        return FLEX_ZONA_2
    return FLEX_ZONA_3


NACIONALIZACION_MULT = 1.9
GANANCIA_HERMANO_MULT = 1.30


def _cotizacion_dolar() -> float | None:
    try:
        req = Request("https://dolarapi.com/v1/dolares/oficial", headers={"User-Agent": "ventas-ml"})
        with urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return float(data["venta"])
    except Exception:
        return None


def _calcular_margen(order: dict, cot: float | None) -> dict:
    """Calcula ganancia y margen de una venta. Retorna dict con el resultado."""
    order_id = str(order.get("id", ""))
    items = order.get("order_items") or []
    first = items[0] if items else {}
    item = first.get("item") or {}
    sku = _extract_sku(first)
    title = item.get("title", "(sin título)")
    quantity = int(first.get("quantity") or 1)
    bruto = float(order.get("total_amount") or float(first.get("unit_price") or 0) * quantity)

    result = {"order_id": order_id, "title": title, "sku": sku, "quantity": quantity, "bruto": bruto}

    neto_raw = local_store.get_neto_manual(order_id)
    shipping = local_store.get_shipping_manual(order_id) or 0.0
    if neto_raw is None:
        result["error"] = "sin neto MP"
        return result
    neto_efectivo = neto_raw - shipping
    result["neto_efectivo"] = neto_efectivo
    result["shipping"] = shipping

    costo_total = None
    if sku:
        costo_ars = local_store.get_costo_ars(sku)
        if costo_ars is not None:
            mult = local_store.get_costo_ars_mult(sku)
            if mult is None:
                result["error"] = "sin multiplicador (Cris)"
                return result
            costo_total = costo_ars * mult * quantity
        else:
            fob = local_store.get_fob(sku)
            if not fob or fob <= 0:
                result["error"] = "sin FOB"
                return result
            mult = local_store.get_multiplicador(sku)
            if mult is None:
                result["error"] = "sin multiplicador"
                return result
            if cot is None:
                result["error"] = "sin cotización dólar"
                return result
            markup = local_store.get_markup(sku) or GANANCIA_HERMANO_MULT
            costo_total = fob * NACIONALIZACION_MULT * cot * markup * mult * quantity
    else:
        result["error"] = "sin SKU"
        return result

    if costo_total is None:
        result["error"] = "sin costo"
        return result

    ganancia = neto_efectivo - costo_total
    margen_pct = (ganancia / bruto * 100) if bruto > 0 else 0
    result["costo_total"] = costo_total
    result["ganancia"] = ganancia
    result["margen_pct"] = margen_pct
    return result


@mcp.tool()
def margenes(fecha_desde: str, fecha_hasta: str = "") -> str:
    """Muestra el margen de ganancia de cada venta en el rango de fechas.

    Args:
        fecha_desde: Fecha inicio en formato YYYY-MM-DD.
        fecha_hasta: Fecha fin en formato YYYY-MM-DD. Si no se pasa, usa fecha_desde (un solo día).
    """
    if not fecha_hasta:
        fecha_hasta = fecha_desde
    local_store.init()
    auth = MLAuth(str(CREDENTIALS_PATH))
    orders = _fetch_orders_by_date(auth, fecha_desde, fecha_hasta)

    if not orders:
        return f"No hay ventas entre {fecha_desde} y {fecha_hasta}."

    cot = _cotizacion_dolar()

    completos = []
    incompletos = []

    for order in orders:
        r = _calcular_margen(order, cot)
        if "error" in r:
            incompletos.append(f"  {r['quantity']}x  {r['title']}  ·  {r['sku'] or 'SIN SKU'}  →  {r['error']}")
        else:
            if r["margen_pct"] < 10:
                tier = "MUY BAJO"
            elif r["margen_pct"] < 20:
                tier = "BAJO"
            elif r["margen_pct"] < 30:
                tier = "BUENO"
            else:
                tier = "EXCELENTE"
            completos.append(
                f"  {r['quantity']}x  {r['title']}  ·  {r['sku']}\n"
                f"      Bruto: ${r['bruto']:,.2f}  →  Ganancia: ${r['ganancia']:,.2f}  →  Margen: {r['margen_pct']:.1f}% {tier}"
            )

    lines = []
    if completos:
        lines.append(f"Márgenes ({len(completos)}):")
        lines.extend(completos)
    if incompletos:
        lines.append(f"\nIncompletos ({len(incompletos)}):")
        lines.extend(incompletos)

    return "\n".join(lines)


@mcp.tool()
def estado_costos(fecha_desde: str, fecha_hasta: str = "") -> str:
    """Muestra si cada venta tiene costo cargado (FOB, combo o costo ARS/Cris).

    Args:
        fecha_desde: Fecha inicio en formato YYYY-MM-DD.
        fecha_hasta: Fecha fin en formato YYYY-MM-DD. Si no se pasa, usa fecha_desde (un solo día).
    """
    if not fecha_hasta:
        fecha_hasta = fecha_desde
    local_store.init()
    auth = MLAuth(str(CREDENTIALS_PATH))
    orders = _fetch_orders_by_date(auth, fecha_desde, fecha_hasta)

    if not orders:
        return f"No hay ventas entre {fecha_desde} y {fecha_hasta}."

    ok = []
    sin_costo = []

    for order in orders:
        items = order.get("order_items") or []
        first = items[0] if items else {}
        item = first.get("item") or {}
        sku = _extract_sku(first)
        title = item.get("title", "(sin título)")
        qty = int(first.get("quantity") or 1)

        if not sku:
            sin_costo.append(f"  {qty}x  {title}  →  SIN SKU")
            continue

        combo = local_store.get_fob_combo_items(sku)
        fob = local_store.get_fob(sku)
        costo_ars = local_store.get_costo_ars(sku)

        if combo:
            ok.append(f"  {qty}x  {title}  ·  {sku}  →  COMBO (USD {fob:.2f})")
        elif costo_ars:
            ok.append(f"  {qty}x  {title}  ·  {sku}  →  CRIS (${costo_ars:,.2f})")
        elif fob:
            ok.append(f"  {qty}x  {title}  ·  {sku}  →  FOB (USD {fob:.2f})")
        else:
            sin_costo.append(f"  {qty}x  {title}  ·  {sku}  →  SIN COSTO")

    lines = []
    if sin_costo:
        lines.append(f"Sin costo ({len(sin_costo)}):")
        lines.extend(sin_costo)
    if ok:
        lines.append(f"\nCon costo ({len(ok)}):")
        lines.extend(ok)
    if not sin_costo:
        lines.append("Todas las ventas tienen costo cargado.")

    return "\n".join(lines)


@mcp.tool()
def cargar_envios(fecha_desde: str, fecha_hasta: str = "") -> str:
    """Carga el costo de envío Flex para ventas que no lo tengan. Solo aplica a envíos self_service (Flex).

    Args:
        fecha_desde: Fecha inicio en formato YYYY-MM-DD.
        fecha_hasta: Fecha fin en formato YYYY-MM-DD. Si no se pasa, usa fecha_desde (un solo día).
    """
    if not fecha_hasta:
        fecha_hasta = fecha_desde
    local_store.init()
    auth = MLAuth(str(CREDENTIALS_PATH))
    orders = _fetch_orders_by_date(auth, fecha_desde, fecha_hasta)

    if not orders:
        return f"No hay ventas entre {fecha_desde} y {fecha_hasta}."

    cargados = []
    ya_tenian = []
    no_flex = []
    errores = []

    for order in orders:
        order_id = str(order.get("id", ""))
        items = order.get("order_items") or []
        first = items[0] if items else {}
        title = (first.get("item") or {}).get("title", "(sin título)")
        ship_id = (order.get("shipping") or {}).get("id")

        if not ship_id:
            errores.append(f"  {order_id}  {title}  →  sin shipping_id")
            continue

        try:
            url = f"https://api.mercadolibre.com/shipments/{ship_id}"
            req = Request(url, headers={"Authorization": f"Bearer {auth.access_token}"})
            with urlopen(req, timeout=30) as resp:
                sdata = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            errores.append(f"  {order_id}  {title}  →  {e}")
            continue

        if sdata.get("logistic_type") != "self_service":
            no_flex.append(f"  {order_id}  {title}  →  {sdata.get('logistic_type')}")
            continue

        if local_store.get_shipping_manual(order_id) is not None:
            ya_tenian.append(f"  {order_id}  {title}")
            continue

        addr = sdata.get("receiver_address") or {}
        costo = _costo_flex(addr)
        city = (addr.get("city", {}).get("name") or "?")
        state = (addr.get("state", {}).get("name") or "?")
        local_store.set_shipping_manual(order_id, float(costo))
        cargados.append(f"  {order_id}  {title}  →  ${costo:,}  ({city}, {state})")

    lines = []
    if cargados:
        lines.append(f"Envíos Flex cargados ({len(cargados)}):")
        lines.extend(cargados)
    if ya_tenian:
        lines.append(f"\nYa tenían envío ({len(ya_tenian)}):")
        lines.extend(ya_tenian)
    if no_flex:
        lines.append(f"\nNo son Flex ({len(no_flex)}):")
        lines.extend(no_flex)
    if errores:
        lines.append(f"\nErrores ({len(errores)}):")
        lines.extend(errores)
    if not cargados and not errores:
        lines.append("No hay envíos Flex pendientes de cargar.")

    return "\n".join(lines)


@mcp.tool()
def cargar_netos(fecha_desde: str, fecha_hasta: str = "") -> str:
    """Carga automáticamente el neto MP de cada venta que no lo tenga, consultando la API de ML.

    Args:
        fecha_desde: Fecha inicio en formato YYYY-MM-DD.
        fecha_hasta: Fecha fin en formato YYYY-MM-DD. Si no se pasa, usa fecha_desde (un solo día).
    """
    if not fecha_hasta:
        fecha_hasta = fecha_desde
    local_store.init()
    auth = MLAuth(str(CREDENTIALS_PATH))
    orders = _fetch_orders_by_date(auth, fecha_desde, fecha_hasta)

    if not orders:
        return f"No hay ventas entre {fecha_desde} y {fecha_hasta}."

    cargados = []
    ya_tenian = []
    errores = []

    for order in orders:
        order_id = str(order.get("id", ""))
        items = order.get("order_items") or []
        first = items[0] if items else {}
        item_data = first.get("item") or {}
        title = item_data.get("title", "(sin título)")

        if local_store.get_neto_manual(order_id) is not None:
            ya_tenian.append(f"  {order_id}  {title}")
            continue

        payments = order.get("payments") or []
        payment_id = payments[0].get("id") if payments else None
        if not payment_id:
            errores.append(f"  {order_id}  {title}  →  sin payment_id")
            continue

        try:
            url = f"https://api.mercadolibre.com/collections/{payment_id}"
            req = Request(url, headers={"Authorization": f"Bearer {auth.access_token}"})
            with urlopen(req, timeout=30) as resp:
                pdata = json.loads(resp.read().decode("utf-8"))
            neto = pdata.get("net_received_amount")
            if neto is None:
                errores.append(f"  {order_id}  {title}  →  net_received_amount no encontrado")
                continue
            neto = float(neto)
            local_store.set_neto_manual(order_id, neto)
            cargados.append(f"  {order_id}  {title}  →  ${neto:,.2f}")
        except Exception as e:
            errores.append(f"  {order_id}  {title}  →  {e}")

    lines = []
    if cargados:
        lines.append(f"Netos cargados ({len(cargados)}):")
        lines.extend(cargados)
    if ya_tenian:
        lines.append(f"\nYa tenían neto ({len(ya_tenian)}):")
        lines.extend(ya_tenian)
    if errores:
        lines.append(f"\nErrores ({len(errores)}):")
        lines.extend(errores)
    if not cargados and not errores:
        lines.append("Todas las ventas ya tenían neto cargado.")

    return "\n".join(lines)


def _margen_tier(r: dict) -> str:
    if "error" in r:
        return "❓ N/A"
    pct = r["margen_pct"]
    if pct < 10:
        return f"🔴 {pct:.1f}% MUY BAJO"
    elif pct < 20:
        return f"🟡 {pct:.1f}% BAJO"
    elif pct < 30:
        return f"🟢 {pct:.1f}% BUENO"
    else:
        return f"⭐ {pct:.1f}% EXCELENTE"


TELEGRAM_TOKEN = "8414853454:AAEZtysmuVcxMQFEqg4xhdNBZ_8xmUA7zCY"
TELEGRAM_CHAT_ID = "8239777724"


def _telegram(texto: str):
    try:
        data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": texto}).encode()
        req = Request(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        urlopen(req, timeout=10)
    except Exception:
        pass


@mcp.tool()
def procesar_dia(fecha: str) -> str:
    """Procesa un día completo: verifica costos, carga netos, envíos y sube al sheet.
    Si alguna venta no tiene costo (FOB/Cris) o SKU, frena y avisa por Telegram.

    Args:
        fecha: Fecha en formato YYYY-MM-DD.
    """
    local_store.init()
    auth = MLAuth(str(CREDENTIALS_PATH))
    orders = _fetch_orders_by_date(auth, fecha, fecha)

    if not orders:
        msg = f"No hay ventas el {fecha}."
        _telegram(msg)
        return msg

    _telegram(f"⏳ Procesando {fecha} — {len(orders)} ventas...")

    # 1. Verificar SKU y costos
    sin_sku = []
    sin_costo = []
    for order in orders:
        items = order.get("order_items") or []
        first = items[0] if items else {}
        item = first.get("item") or {}
        sku = _extract_sku(first)
        title = item.get("title", "(sin título)")
        qty = int(first.get("quantity") or 1)

        if not sku:
            sin_sku.append(f"• {qty}x {title}")
            continue
        combo = local_store.get_fob_combo_items(sku)
        fob = local_store.get_fob(sku)
        costo_ars = local_store.get_costo_ars(sku)
        if not combo and not fob and costo_ars is None:
            sin_costo.append(f"• {qty}x {title} · {sku}")

    if sin_costo:
        partes = [f"🚫 {fecha} — no se puede procesar:"]
        if sin_costo:
            partes.append(f"\nSIN COSTO ({len(sin_costo)}):")
            partes.extend(sin_costo)
        partes.append("\nCargalos en la app y volvé a correr.")
        msg = "\n".join(partes)
        _telegram(msg)
        return msg

    # 2. Cargar netos
    netos_ok = []
    netos_ya = []
    netos_err = []
    for order in orders:
        order_id = str(order.get("id", ""))
        items = order.get("order_items") or []
        first = items[0] if items else {}
        title = (first.get("item") or {}).get("title", "(sin título)")

        if local_store.get_neto_manual(order_id) is not None:
            netos_ya.append(order_id)
            continue
        payments = order.get("payments") or []
        payment_id = payments[0].get("id") if payments else None
        if not payment_id:
            netos_err.append(f"  {order_id}  {title}  →  sin payment_id")
            continue
        try:
            url = f"https://api.mercadolibre.com/collections/{payment_id}"
            req = Request(url, headers={"Authorization": f"Bearer {auth.access_token}"})
            with urlopen(req, timeout=30) as resp:
                pdata = json.loads(resp.read().decode("utf-8"))
            neto = pdata.get("net_received_amount")
            if neto is None:
                netos_err.append(f"  {order_id}  {title}  →  net_received_amount no encontrado")
                continue
            local_store.set_neto_manual(order_id, float(neto))
            netos_ok.append(order_id)
        except Exception as e:
            netos_err.append(f"  {order_id}  {title}  →  {e}")

    # 3. Cargar envíos Flex
    envios_ok = []
    envios_ya = []
    envios_no_flex = []
    for order in orders:
        order_id = str(order.get("id", ""))
        ship_id = (order.get("shipping") or {}).get("id")
        if not ship_id:
            continue
        try:
            url = f"https://api.mercadolibre.com/shipments/{ship_id}"
            req = Request(url, headers={"Authorization": f"Bearer {auth.access_token}"})
            with urlopen(req, timeout=30) as resp:
                sdata = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue
        if sdata.get("logistic_type") != "self_service":
            envios_no_flex.append(order_id)
            continue
        if local_store.get_shipping_manual(order_id) is not None:
            envios_ya.append(order_id)
            continue
        addr = sdata.get("receiver_address") or {}
        costo = _costo_flex(addr)
        local_store.set_shipping_manual(order_id, float(costo))
        city = (addr.get("city", {}).get("name") or "?")
        envios_ok.append(f"{order_id} → ${costo:,} ({city})")

    # 4. Subir al sheet
    cot = _cotizacion_dolar()
    new_rows = []
    sheet_err = []
    for order in orders:
        r = _calcular_margen(order, cot)
        items = order.get("order_items") or []
        first = items[0] if items else {}
        item = first.get("item") or {}
        sku = _extract_sku(first)
        title = item.get("title", "(sin título)")
        quantity = int(first.get("quantity") or 1)
        dt = _parse_ar(order.get("date_created", ""))
        fecha_fmt = dt.strftime("%d/%m/%Y") if dt else ""
        costo, _ = _costo_para_pagar(sku, quantity, cot)
        if costo is None:
            sheet_err.append(f"  {quantity}x  {title}  ·  {sku}  →  sin costo calculable")
            continue
        margen_txt = _margen_tier(r)
        new_rows.append([fecha_fmt, str(quantity), title, f"{costo:.2f}".replace(".", ","), margen_txt])

    if new_rows:
        _escribir_sheet(new_rows)

    # Resumen
    lines = [f"=== {fecha} — {len(orders)} ventas ===", ""]
    lines.append(f"Netos: {len(netos_ok)} cargados, {len(netos_ya)} ya tenían")
    if netos_err:
        lines.append(f"  Errores neto: {len(netos_err)}")
        lines.extend(netos_err)
    lines.append(f"Envíos: {len(envios_ok)} Flex cargados, {len(envios_ya)} ya tenían, {len(envios_no_flex)} no Flex")
    if envios_ok:
        for e in envios_ok:
            lines.append(f"  {e}")
    lines.append(f"Sheet: {len(new_rows)} filas cargadas")
    if sheet_err:
        lines.extend(sheet_err)
    if sin_sku:
        lines.append(f"Sin SKU ({len(sin_sku)}) — salteadas:")
        lines.extend(sin_sku)

    resumen = "\n".join(lines)
    tg_msg = f"✅ {fecha} procesado — {len(orders)} ventas, {len(new_rows)} al sheet"
    if sin_sku:
        tg_msg += f" ({len(sin_sku)} sin SKU salteadas)"
    _telegram(tg_msg)
    return resumen


COLOR_BLANCO = "#FFFFFF"
COLOR_GRIS = "#F3F3F3"

GDRIVE_SERVER = str(Path.home() / "Proyectos" / "gdrive-mcp" / "server.js")
SHEET_ID = "1PxMOw5uwhITBfu4RdZZULFEsbJyKSxJRV515ZaRsz4s"
SHEET_NAME = "Ventas totales"


def _gdrive_call(tool_name: str, arguments: dict) -> str:
    init_msg = json.dumps({
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "cli", "version": "1.0.0"}},
    })
    call_msg = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    })
    result = subprocess.run(
        ["node", GDRIVE_SERVER],
        input=f"{init_msg}\n{call_msg}\n",
        capture_output=True, text=True, timeout=30,
    )
    for line in result.stdout.strip().split("\n"):
        try:
            msg = json.loads(line)
            if msg.get("id") == 1:
                return msg.get("result", {}).get("content", [{}])[0].get("text", "")
        except (json.JSONDecodeError, IndexError):
            continue
    return ""



def _escribir_sheet(new_rows: list[list[str]]):
    """Inserta filas nuevas arriba del sheet y recolorea por día."""
    n = len(new_rows)
    _gdrive_call("insert_rows", {
        "spreadsheet_id": SHEET_ID,
        "sheet_id": 0,
        "start_row": 1,
        "count": n,
    })
    _gdrive_call("write_spreadsheet", {
        "spreadsheet_id": SHEET_ID,
        "range": f"{SHEET_NAME}!A2:E{n + 1}",
        "values": new_rows,
        "value_input_option": "USER_ENTERED",
    })
    # Leer columna A completa para recolorear por día
    fechas_text = _gdrive_call("read_spreadsheet", {
        "spreadsheet_id": SHEET_ID,
        "range": f"{SHEET_NAME}!A2:A",
    })
    fechas = []
    if fechas_text and "No data found" not in fechas_text:
        for line in fechas_text.split("\n")[1:]:
            f = line.strip()
            if f:
                fechas.append(f)
    if fechas:
        _colorear_dias([[f] for f in fechas])


def _colorear_dias(all_rows: list[list[str]]):
    """Aplica fondo alternado blanco/gris por día a todas las filas del sheet."""
    if not all_rows:
        return
    blocks = []
    current_day = None
    block_start = 0
    day_index = 0
    for i, row in enumerate(all_rows):
        fecha = row[0] if row else ""
        if fecha != current_day:
            if current_day is not None:
                color = COLOR_BLANCO if day_index % 2 == 0 else COLOR_GRIS
                blocks.append({"start_row": block_start + 1, "end_row": i + 1, "color": color})
                day_index += 1
            current_day = fecha
            block_start = i
    if current_day is not None:
        color = COLOR_BLANCO if day_index % 2 == 0 else COLOR_GRIS
        blocks.append({"start_row": block_start + 1, "end_row": len(all_rows) + 1, "color": color})
    if blocks:
        _gdrive_call("set_row_backgrounds", {
            "spreadsheet_id": SHEET_ID,
            "sheet_id": 0,
            "blocks": blocks,
        })


def _costo_para_pagar(sku: str, quantity: int, cot: float | None) -> tuple[float | None, str]:
    """Retorna (costo_total, proveedor) — lo que hay que pagar a Andrés o Cris."""
    if not sku:
        return None, ""
    costo_ars = local_store.get_costo_ars(sku)
    if costo_ars is not None:
        mult = local_store.get_costo_ars_mult(sku)
        if mult is None:
            return None, "Cris"
        return costo_ars * mult * quantity, "Cris"
    fob = local_store.get_fob(sku)
    if not fob or fob <= 0:
        return None, ""
    mult = local_store.get_multiplicador(sku)
    if mult is None or cot is None:
        return None, "Andrés"
    markup = local_store.get_markup(sku) or GANANCIA_HERMANO_MULT
    total = fob * NACIONALIZACION_MULT * cot * markup * mult * quantity
    return total, "Andrés"


@mcp.tool()
def cargar_ventas_sheet(fecha_desde: str, fecha_hasta: str = "") -> str:
    """Carga las ventas al Google Sheet de liquidación (solapa Ventas totales).
    Los días nuevos se insertan arriba, después del header.

    Args:
        fecha_desde: Fecha inicio en formato YYYY-MM-DD.
        fecha_hasta: Fecha fin en formato YYYY-MM-DD. Si no se pasa, usa fecha_desde (un solo día).
    """
    if not fecha_hasta:
        fecha_hasta = fecha_desde
    local_store.init()
    auth = MLAuth(str(CREDENTIALS_PATH))
    orders = _fetch_orders_by_date(auth, fecha_desde, fecha_hasta)

    if not orders:
        return f"No hay ventas entre {fecha_desde} y {fecha_hasta}."

    cot = _cotizacion_dolar()

    new_rows = []
    errores = []
    for order in orders:
        items = order.get("order_items") or []
        first = items[0] if items else {}
        item = first.get("item") or {}
        sku = _extract_sku(first)
        title = item.get("title", "(sin título)")
        quantity = int(first.get("quantity") or 1)
        dt = _parse_ar(order.get("date_created", ""))
        fecha = dt.strftime("%d/%m/%Y") if dt else ""

        costo, proveedor = _costo_para_pagar(sku, quantity, cot)
        if costo is None:
            errores.append(f"  {quantity}x  {title}  ·  {sku or 'SIN SKU'}  →  sin costo ({proveedor or 'sin proveedor'})")
            continue

        r = _calcular_margen(order, cot)
        margen_txt = _margen_tier(r)
        new_rows.append([fecha, str(quantity), title, f"{costo:.2f}".replace(".", ","), margen_txt])

    if not new_rows and not errores:
        return "No hay ventas para cargar."

    if new_rows:
        _escribir_sheet(new_rows)

    lines = []
    if new_rows:
        lines.append(f"Cargadas {len(new_rows)} ventas al sheet:")
        for r in new_rows:
            lines.append(f"  {r[0]}  {r[1]}x  {r[2]}  →  ${r[3]}")
    if errores:
        lines.append(f"\nNo se pudieron cargar ({len(errores)}):")
        lines.extend(errores)

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
