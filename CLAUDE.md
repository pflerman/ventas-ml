# ventas-ml — contexto para Claude

App Tkinter en Python que lista las ventas de MercadoLibre (cuenta PaliShopping, USER_ID 24192412) en vivo, agrupadas por día, con un panel lateral que muestra el detalle del producto (etiquetas desde gestor-productos en Turso) y el desglose del cobro de Mercado Pago. Pensada para uso interno del vendedor.

## Dependencias externas (críticas)

`ventas-ml` no es autocontenido. Necesita 3 repos hermanos en `~/Proyectos/`:

1. **`gestor-productos/`** — `productos_lookup.py` lee `~/Proyectos/gestor-productos/.env` para conectarse a la misma base Turso del proyecto gestor-productos. Lookup `{sku → producto}` con etiquetas separadas por coma.
2. **`mercadolibre-mcp/`** — `app.py` hace `sys.path.insert` y `from ml_auth import MLAuth` para reusar el manejo de refresh tokens. Si este repo no existe, la app no arranca.
3. **`ml-scripts/config/ml_credentials_palishopping.json`** — JSON con `access_token` / `refresh_token` de ML. No está versionado en ningún lado (secrets).

## Arquitectura mental rápida

- **`app.py`**: monolito con la clase `VentasApp`. UI = `PanedWindow` horizontal: Treeview a la izquierda, panel de detalle a la derecha.
- **`productos_lookup.py`**: módulo separado con cache `{sku → dict}` en memoria. Lectura única al arrancar (~100 productos), refresca al pedido en `refresh()` y `_refresh_clicked_row()`.
- **`gen_icon.py`**: standalone, dibuja el ícono de la app con PIL. Los PNGs generados están versionados en el repo.

## ⚠️ Cosas no obvias / trampas

### La tupla `values` del Treeview (BUG histórico)

El Treeview almacena cada fila con esta forma exacta:

```python
values = (check, fecha, sku, cant, producto, precio, subtotal)
#         0      1      2    3     4         5       6
```

**Cuando agregues o muevas columnas, GREP OBLIGATORIO de `values\[` y `values =` antes de cerrar el cambio.** En el pasado se introdujo un bug silencioso al agregar la columna `cant`: `_copy_clicked_title` accedía a `values[3]` que pasó de ser el título a ser la cantidad, sin error en runtime. Sólo lo descubrió el usuario cuando vio que el clipboard tenía un número en vez del título.

**Preferí siempre el unpacking** (`_, fecha, sku, cant, producto, precio, subtotal = values`) sobre el acceso por índice — el unpacking falla loud si cambia la forma. El usuario decidió explícitamente NO refactorizar a `namedtuple` (anda bien así).

### `leaf_to_item` (dict por fila)

Cada fila del treeview tiene una entrada en `self.leaf_to_item[leaf_id]` con info que NO está en `values`: `item_id`, `variation_id`, `quantity`, `unit_price`, `line_total`, `payment_id`, `payment_method`, `total_amount`, `sale_fee`, `shipping_cost`, `taxes_amount`, `coupon_amount`, `neto`. Cuando agregues funcionalidad que necesite info por venta, **usá esto** en vez de re-parsear `values`.

### Helpers `_set_clipboard` y `_open_url` (NO TOCAR sin entender)

La app corre en **dos entornos**: Fedora (host del usuario) y **WSL en Win10** (en una PC de fábrica). En WSL hay dos quirks que requieren detección y código específico:

- **Clipboard**: Tk escribe al X11 buffer de WSLg, que no se sincroniza con el clipboard de Windows. Fix: en WSL usar `clip.exe` vía subprocess. En Fedora usar `clipboard_clear/append` de Tk normal.
- **Browser**: `webbrowser.open()` en WSL intenta `xdg-open` y no abre nada. Fix: en WSL usar `cmd.exe /c start` (delega al browser default de Windows). En Fedora `webbrowser.open` ya conoce Brave.

La detección está en `_IS_WSL` (lectura única de `/proc/version` al cargar el módulo). Si "limpiás" estos helpers porque parecen redundantes, **rompés el laburo en WSL silenciosamente**.

### URL del cobro de Mercado Pago

Alt+click abre `https://www.mercadopago.com.ar/activities?q={order_id}` (búsqueda) en vez del detalle directo. **No es por flojera** — el endpoint `/activities/detail/{id}` requiere un hash `purchase_v3-{...}` impredecible que solo conoce el frontend de MP. Probamos varias variantes (payment_id, order_id, etc) y todas fallaban con "esta actividad pertenece a otra cuenta". La búsqueda es la forma confiable; el usuario hace un click más y listo.

## Cómo correrla

**Fedora**:
```bash
cd ~/Proyectos/ventas-ml && python3 app.py
```
(O usa el `.desktop` de GNOME que apunta a `ventas-ml-icon.png`.)

**WSL**:
```bash
ventas    # alias en ~/.bashrc → cd + venv + python3 app.py
```
Necesita venv (`python3 -m venv venv && pip install -r requirements.txt`) y `python3-tk` instalado vía apt. WSLg tiene que estar funcionando (probá con `xeyes`).

## Git

- Branch principal: `master`
- Remote: `github.com/pflerman/ventas-ml`
- Estilo de commits: títulos en español, descriptivos, sin prefijo tipo `feat:`/`fix:`. Cuerpo explica el "por qué" cuando hace falta.
