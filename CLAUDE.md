# ventas-ml — contexto para Claude

App Tkinter en Python que lista las ventas de MercadoLibre (cuenta PaliShopping, USER_ID 24192412) en vivo, agrupadas por día. Panel lateral con detalle del producto, costeo de importación FOB → precio final → ganancia neta sobre cobro de Mercado Pago, y filtro de búsqueda/exclusión sobre el listado. Pensada para uso interno del vendedor.

## Dependencias externas (críticas)

`ventas-ml` no es autocontenido. Necesita 3 repos hermanos en `~/Proyectos/`:

1. **`gestor-productos/`** — `productos_lookup.py` y `ventas_db.py` leen `~/Proyectos/gestor-productos/.env` para conectarse a la misma DB Turso. Lookup `{sku → producto}` (etiquetas) más tablas propias `ventas_checks` (order_id → marcado) y `ventas_fob` (sku → precio_fob, multiplicador).
2. **`mercadolibre-mcp/`** — `app.py` hace `sys.path.insert` y `from ml_auth import MLAuth` para reusar el manejo de refresh tokens. Si este repo no existe, la app no arranca.
3. **`ml-scripts/config/ml_credentials_palishopping.json`** — JSON con `access_token` / `refresh_token` de ML. No está versionado en ningún lado (secrets).

Además, llamadas HTTP públicas a:
- **`dolarapi.com/v1/dolares/oficial`** — cotización del dólar oficial, una vez al arrancar (`dolar.py`). Si está caída, los cálculos de FOB en pesos no aparecen pero el resto de la app funciona.

## Arquitectura mental rápida

- **`app.py`** — monolito con la clase `VentasApp`. UI = barra de filtro arriba del tree, `PanedWindow` horizontal (Treeview izq, panel scrollable der), bottom bar con status + cotización + mini totalizador + botones.
- **`productos_lookup.py`** — cache `{sku → dict}` en memoria, lectura única al arrancar (~100 productos). Provee título y etiquetas.
- **`ventas_db.py`** — cache en memoria + write-through asíncrono para checks y FOBs. Mismo patrón que `gestor-productos/app/db.py`. Tablas `ventas_checks` y `ventas_fob` (con columna `multiplicador` nullable).
- **`dolar.py`** — fetch único de la cotización al arrancar, queda en memoria hasta cerrar la app.
- **`gen_icon.py`** — standalone, dibuja el ícono con PIL. Los PNGs están versionados.

## ⚠️ Cosas no obvias / trampas

### La tupla `values` del Treeview (BUG histórico)

```python
values = (check, fecha, sku, cant, producto, precio, subtotal)
#         0      1      2    3     4         5       6
```

**Cuando agregues o muevas columnas, GREP OBLIGATORIO de `values\[` y `values =` antes de cerrar el cambio.** En el pasado se introdujo un bug silencioso al agregar la columna `cant`: `_copy_clicked_title` accedía a `values[3]` que pasó de ser el título a ser la cantidad, sin error en runtime.

**Preferí siempre el unpacking** (`_, fecha, sku, cant, producto, precio, subtotal = values`) sobre el acceso por índice — el unpacking falla loud si cambia la forma. El usuario decidió explícitamente NO refactorizar a `namedtuple` (anda bien así).

### `leaf_to_item` (dict por fila) vs `row_to_order` vs `_all_leaves()` vs `_leaves_meta`

Tres estructuras paralelas que hacen cosas distintas — confundirlas rompe los totales y el filtro:

- **`leaf_to_item[leaf_id]`** — datos por venta que no están en `values`: `item_id`, `variation_id`, `quantity`, `unit_price`, `line_total`, `payment_id`, `payment_method`, `total_amount`, `sale_fee`, `shipping_cost`, `taxes_amount`, `coupon_amount`, `neto`, `title`. Cuando agregues funcionalidad por venta, **usá esto** en vez de re-parsear `values`.
- **`row_to_order[leaf_id] → order_id`** — TODAS las leaves cargadas, incluso las **detached** por el filtro. Usar para totales/conteos que no deben depender del filtro.
- **`_all_leaves()`** — solo las leaves **visibles** en el tree (excluye detached). Usar para conteos visuales (ej. "X de Y mostradas").
- **`_leaves_meta[leaf_id]`** — tracking del filtro: `day_key`, `row_text` normalizado, `order` (contador para preservar orden de inserción).

**Regla**: el mini totalizador de la barra inferior y `_calcular_totales_seleccionados` iteran `row_to_order` (filtro-agnóstico). Si los pasás a `_all_leaves()`, los totales se rompen al filtrar.

### `_refresh_leaf_meta` después de actualizar un SKU

Hay **3 lugares** donde un SKU puede cambiar en runtime: `_on_sku_updated` (modal manual), `_on_leaf_refreshed` (right-click → Refrescar fila), `_apply_sku_updates` (sync background al cargar). Los 3 **deben** llamar `self._refresh_leaf_meta(leaf_id)` después de actualizar `info["sku"]`. Si no, el `row_text` del filtro queda con el SKU viejo y la fila deja de matchear silenciosamente.

### Cache de checks: optimista + write-through async

`ventas_db` expone dos funciones distintas a propósito:

- **`mark_local(order_id, checked)`** — sync, main thread. Solo actualiza la cache. Para que la UI cambie al instante.
- **`persist_check(order_id, checked)`** — escribe en Turso. Se llama desde un thread (`_persist_check_async`). Si falla, `_on_check_failed` revierte la cache y re-renderiza la fila.

**No las fusiones en una sola función sync** — si lo hacés, cada click de check espera la latencia de Turso (100-300ms) y la UI se siente lenta. La separación es deliberada.

### `set_multiplicador` requiere FOB previo

El constraint "no podés tener multiplicador sin FOB" vive en código (`set_multiplicador` hace `UPDATE ... WHERE sku = ?` y verifica `affected_row_count > 0`), no en SQL. Si querés relajarlo, recordá actualizar también `_on_ctrl_shift_alt_click` que valida lo mismo antes de abrir el modal.

### Constantes del cálculo de FOB hardcodeadas

`NACIONALIZACION_MULT = 1.9` y `GANANCIA_HERMANO_MULT = 1.30` están al tope de `app.py`. Si los multiplicadores cambian (ej. el hermano sube su markup a 35%), se editan ahí. **No están en Turso a propósito** — son políticas del usuario, no del catálogo. Si la app se compartiera entre múltiples vendedores, ahí sí habría que moverlas a config por usuario.

### Bindings de modificadores

Hay 6 acciones distintas según qué tecla pretás al hacer click:

| Combinación | Acción |
|---|---|
| `Click` | Toggle check |
| `Doble click` | Editar SKU (modal) |
| `Ctrl + Click` | Abrir publicación en browser |
| `Shift + Click` | Abrir detalle de la venta |
| `Alt + Click` | Abrir cobro en Mercado Pago |
| `Ctrl + Shift + Click` | Editar precio FOB (modal) |
| `Ctrl + Shift + Alt + Click` | Editar multiplicador (modal) |

Cada combinación tiene su binding propio en `_build_ui` y devuelve `"break"` para no propagar al `_on_click` de toggle. Si agregás una combinación nueva, sumá también un branch en `_refresh_modifier_hint` o el hint de la status bar queda mudo.

### URL del cobro de Mercado Pago

Alt+click abre `https://www.mercadopago.com.ar/activities?q={order_id}` (búsqueda) en vez del detalle directo. **No es por flojera** — el endpoint `/activities/detail/{id}` requiere un hash `purchase_v3-{...}` impredecible que solo conoce el frontend de MP. La búsqueda es la forma confiable.

## Helpers / decisiones que parecen raras pero tienen razón

### `_IS_WSL`, `_set_clipboard`, `_open_url` (NO TOCAR sin entender)

La app corre en **dos entornos**: Fedora (host del usuario) y WSL en Win10 (PC de fábrica). Quirks de WSL:

- **Clipboard**: Tk escribe al X11 buffer de WSLg, que no se sincroniza con el clipboard de Windows. Fix: en WSL usar `clip.exe` vía subprocess.
- **Browser**: `webbrowser.open()` en WSL intenta `xdg-open` y no abre nada. Fix: en WSL usar `cmd.exe /c start`.

`_IS_WSL` se calcula una vez al cargar el módulo leyendo `/proc/version`. Si "limpiás" estos helpers porque parecen redundantes, **rompés el laburo en WSL silenciosamente**.

### Mouse wheel del panel scrollable

El bind del wheel sobre el panel lateral está hecho con `bind_all` y se activa solo mientras el cursor está sobre el panel (`<Enter>` lo bindea, `<Leave>` lo unbindea). Esto es **a propósito**: si quedás con el bind global permanente, el wheel sobre el Treeview también scrollea el panel y al revés. Cubre los dos formatos: `<Button-4>`/`<Button-5>` (Linux/WSLg) y `<MouseWheel>` (Windows nativo).

## Cómo correrla

**Fedora**:
```bash
cd ~/Proyectos/ventas-ml && python3 app.py
```
(O usá el `.desktop` de GNOME que apunta a `ventas-ml-icon.png`.)

**WSL**:
```bash
ventas    # alias en ~/.bashrc → cd + venv + python3 app.py
```
Necesita venv (`python3 -m venv venv && pip install -r requirements.txt`) y `python3-tk` instalado vía apt. WSLg tiene que estar funcionando (probá con `xeyes`).

## Git

- Branch principal: `master`
- Remote: `github.com/pflerman/ventas-ml`
- Estilo de commits: títulos en español, descriptivos, sin prefijo tipo `feat:`/`fix:`. Cuerpo explica el "por qué" cuando hace falta.
