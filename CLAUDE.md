# ventas-ml

App Tkinter que lista las ventas de MercadoLibre (PaliShopping, USER_ID 24192412) en vivo, agrupadas por día, con costeo de importación, neto MP heurístico, notas libres por venta, etiquetas locales por SKU, y un modal de "Frase del día" que llama a Anthropic API. Uso interno del vendedor.

**App 100% local**: la única red que se hace al cargar es a `/orders/search` de ML. Todo lo demás (checks, notas, FOBs, etiquetas) vive en `data.json` al lado del proyecto.

## Dependencias externas

- **`.env` local del proyecto** — solo `ANTHROPIC_API_KEY=...` para el modal de Frase. Si no existe el archivo, igual funciona si la variable está en el environment.
- **`~/Proyectos/mercadolibre-mcp/`** — `app.py` hace `from ml_auth import MLAuth` para reusar el refresh de tokens de ML.
- **`~/Proyectos/ml-scripts/config/ml_credentials_palishopping.json`** — access/refresh tokens de ML, no versionado.
- **`dolarapi.com`** — cotización oficial al arrancar (`dolar.py`). Si está caída, los pesos no aparecen pero la app sigue.
- **whatsapp-mcp en `localhost:3100`** — solo Fedora. En WSL el botón "Mandar a Pablo" del modal de Frase falla (esperado).
- **Fuentes Fedora** en `/usr/share/fonts/` (LiberationMono-Bold + Symbola) — el modal de Frase rinde con PIL porque Tk-Linux no muestra emojis.

## Persistencia: `local_store.py` + `data.json`

Toda la persistencia está en un único JSON al lado del proyecto. No hay Turso, no hay base remota, no hay write-through async — escribir el JSON entero es instantáneo a esta escala. `local_store` expone API sync para checks, FOBs, multiplicadores, notas y etiquetas. Cada `set_*` reescribe el archivo de forma atómica (tmp + rename).

Estructura del JSON:
```json
{
  "checks":             ["order_id1", ...],
  "notas":              {"order_id": "texto"},
  "fob":                {"SKU": {"precio": 12.5, "mult": 1}},
  "etiquetas_catalogo": ["ordenador", "blanco", ...],
  "etiquetas_por_sku":  {"SKU": ["ordenador", ...]}
}
```

`data.json` está en `.gitignore`.

## Trampas

### Tupla `values` del Treeview

`values = (check, fecha, sku, cant, producto, precio, subtotal)`. Cuando muevas o agregues columnas, **grep obligatorio de `values\[` y `values =`** antes de cerrar el cambio. Preferí siempre unpacking sobre acceso por índice — falla loud si cambia la forma.

### `leaf_to_item` vs `row_to_order` vs `_all_leaves()`

- **`leaf_to_item[leaf_id]`** → datos por venta que no están en `values` (item_id, payment_id, neto, shipping_cost, etc).
- **`row_to_order`** → TODAS las leaves cargadas, incluso detached por filtro. Usar para totales que no deben depender del filtro.
- **`_all_leaves()`** → solo visibles. Usar para conteos visuales.

El mini totalizador y `_calcular_totales_seleccionados` iteran `row_to_order` a propósito. Si los pasás a `_all_leaves()`, los totales se rompen al filtrar.

### Cálculo del neto MP — heurístico

```python
neto = total_amount - sale_fee - shipping_cost - taxes_amount
```

Donde `shipping_cost` es **un heurístico inline** calculado al cargar la orden, sin llamadas extra a `/shipments/{id}/costs`. Reglas:

- Si la orden tiene shipment Y `total_amount >= SHIPPING_FREE_THRESHOLD` → `shipping_cost = total_amount * SHIPPING_FREE_RATE`
- Si no → `shipping_cost = 0`

Las dos constantes están al tope de `app.py` (`SHIPPING_FREE_THRESHOLD = 30000`, `SHIPPING_FREE_RATE = 0.07`). Son ajustables a ojo. **No buscar precisión** — el objetivo era sacar todas las llamadas por shipment porque metían cientos de requests por carga y la app se sentía lenta.

**No restar `coupon_amount`** — bug histórico, `total_amount` ya incluye el listado completo, el cupón es un crédito al seller.

### SKU viene tal cual de la API

Antes había un `_refresh_skus_batch` que después de cargar las órdenes pegaba a `/items?ids=...` para "refrescar" el SKU porque el del listado de orders puede ser snapshot histórico. Eso se eliminó completamente. **El SKU es el que viene en `order_items[].item.seller_sku` (o `seller_custom_field` o `variation_attributes[SELLER_SKU]`) y no se toca.** Si está desactualizado, se edita a mano por doble click.

### URL de Mercado Pago — buscar por payment_id, NO order_id

`Alt+click` abre `mercadopago.com.ar/activities?q={payment_id}`. MP busca por su ID nativo, no por el de ML. No "limpiar" volviendo al order_id.

### Helpers de WSL (`_IS_WSL`, `_set_clipboard`, `_open_url`)

La app corre en Fedora y en WSL/Win10. En WSL, Tk no escribe al clipboard de Windows (hay que usar `clip.exe`) y `webbrowser.open` no abre nada (hay que usar `cmd.exe /c start`). Si los "limpiás" por parecer redundantes, rompés WSL silenciosamente.

### Etiquetas con dropdown, no texto libre

Las etiquetas se asignan a SKUs desde un combobox alimentado por un catálogo. El catálogo se llena con el botón `+` al lado del combo. Es deliberado: si fuera texto libre se ensucia con variantes ("blanco" / "Blanco" / "blanc"). Para borrar una etiqueta de un SKU, click en el `✕` rojo del chip. Para borrar una etiqueta del catálogo entero, no hay UI todavía — editar `data.json` o agregar `local_store.remove_etiqueta_catalogo`.

### Constantes comerciales hardcodeadas

```python
NACIONALIZACION_MULT = 1.9          # impuestos importación China
GANANCIA_HERMANO_MULT = 1.30        # markup de Andrés
SHIPPING_FREE_THRESHOLD = 30000.0   # umbral para asumir envío gratis ML
SHIPPING_FREE_RATE = 0.07           # % del total que paga el seller cuando aplica
```

Al tope de `app.py`. Son políticas del usuario, no del catálogo.

## Cómo correrla

**Fedora**: `cd ~/Proyectos/ventas-ml && python3 app.py` (o el `.desktop` de GNOME).
**WSL**: alias `ventas` en `~/.bashrc`. Necesita venv y `python3-tk` instalado.

## Git

Branch `master`, remote `github.com/pflerman/ventas-ml`. Commits en español, sin prefijos `feat:`/`fix:`, el cuerpo explica el porqué cuando hace falta.
