# ventas-ml

App Tkinter que lista las ventas de MercadoLibre (PaliShopping, USER_ID 24192412) en vivo, agrupadas por día, con costeo de importación, neto MP manual, notas libres por venta, etiquetas locales por SKU, y un modal de "Frase del día" que llama a Anthropic API. Uso interno del vendedor.

## Dependencias externas

- **`.env` local del proyecto** — solo `ANTHROPIC_API_KEY=...` para el modal de Frase. Si no existe el archivo, igual funciona si la variable está en el environment.
- **`~/Proyectos/mercadolibre-mcp/`** — `app.py` hace `from ml_auth import MLAuth` para reusar el refresh de tokens de ML.
- **`~/Proyectos/ml-scripts/config/ml_credentials_palishopping.json`** — access/refresh tokens de ML, no versionado.
- **`dolarapi.com`** — cotización oficial al arrancar (`dolar.py`). Si está caída, los pesos no aparecen pero la app sigue.
- **whatsapp-mcp en `localhost:3100`** — solo Fedora. En WSL el botón "Mandar a Pablo" del modal de Frase falla (esperado).
- **Fuentes Fedora** en `/usr/share/fonts/` (LiberationMono-Bold + Symbola) — el modal de Frase rinde con PIL porque Tk-Linux no muestra emojis.

## Persistencia: `local_store.py` + PostgreSQL (Hetzner VPS)

La persistencia está en PostgreSQL remoto (VPS Hetzner) con cache en memoria. `local_store` expone API sync para FOBs, multiplicadores, notas, etiquetas, neto manual y envío. Cada `set_*` escribe a PostgreSQL y actualiza el cache en memoria.

`data.json` es un legado de la migración — ya no es la fuente de verdad pero sigue en el repo como backup.

## UI: Treeview nativo con breakdown expandible

La UI principal es un **treeview nativo de Tkinter** usando solo la columna `#0`. No hay panel de detalle lateral. No hay columnas visibles de datos.

### Estructura del tree

1. **Nodos día** (raíz) — `21/04/2026  —  2 ventas  —  $97,120.00`, arrancan abiertos
2. **Nodos venta** (hijos del día) — texto inline: `2x  Organizador De Remeras...  ·  SKU-ABC  ·  $60,000.00`
3. **Breakdown** (hijos de la venta) — desglose financiero cerrado por default:
   - **Cobro Mercado Pago**: bruto, neto MP, envío Flex, neto efectivo, método
   - **Costo importación**: FOB/costo ARS, multiplicador, nacionalización, markup Andrés
   - **Ganancia**: ganancia Pablo, margen %

### Edición por doble click en breakdown

Cada fila editable del breakdown tiene una acción asociada en `_breakdown_action`. Doble click abre el modal correspondiente:
- Neto MP → modal neto
- Envío (Flex) → modal envío
- FOB / costo unitario → modal FOB
- Multiplicador → modal multiplicador
- Markup Andrés → modal markup
- Sin SKU → modal editar SKU

### Tracking de breakdown rows

```python
_breakdown_rows: dict[str, list[str]]   # leaf_id -> [row_ids del breakdown]
_breakdown_row_set: set[str]            # lookup rápido para saber si un row es breakdown
_breakdown_action: dict[str, tuple]     # row_id -> ("accion", ...args) para doble click
```

Al refrescar un breakdown (tras editar un dato), se preserva el estado open/closed del nodo padre. Las 3 estructuras se limpian en `refresh()`.

## Atajos de teclado y mouse

Sobre la fila seleccionada del Treeview:

| Tecla / mouse        | Acción                                              |
|----------------------|-----------------------------------------------------|
| Doble click          | En breakdown: abre modal del dato clickeado          |
| Ctrl + click         | Modal "Editar precio FOB"                           |
| Alt + click          | Modal "Editar multiplicador"                        |
| Click derecho        | Menú contextual (copiar, editar SKU, neto, envío)   |
| E                    | Expandir nodo (recursivo)                           |
| C                    | Colapsar nodo y sus hijos                           |
| F1                   | Detalle de la venta en ML (`/ventas/{id}/detalle`)  |
| F2                   | Cobro en Mercado Pago (`activities?q={payment_id}`) |
| F3                   | Editor de la publicación (`/publicaciones/{id}/modificar`) |
| F4                   | Publicación pública (`articulo.mercadolibre.com.ar`) |
| F6                   | Abre las cuatro de F1-F4 en pestañas separadas      |

Botones en la toolbar: **Expandir todo** / **Colapsar todo** (todos los nodos del tree).

Los handlers de F-keys están centralizados en una sección con `_selected_leaf_data()` (devuelve `(leaf_id, info, order_id)` validados) + URL builders chiquitos (`_open_detalle_venta`, `_open_pago_mp`, `_open_publi_edit`, `_open_publi_publica`). F6 reusa los builders sin duplicar la validación. Si tocás los URLs, tocá el builder, no los handlers.

## Trampas

### Tupla `values` del Treeview

`values = (fecha, sku, cant, producto, precio, subtotal)`. Cuando muevas o agregues columnas, **grep obligatorio de `values\[` y `values =`** antes de cerrar el cambio. Preferí siempre unpacking sobre acceso por índice — falla loud si cambia la forma.

El `text` del nodo (column #0) es el display inline y se construye aparte. Si cambiás el format del `display_text`, grep `display_text` para encontrar todos los lugares donde se arma (insert y refresh de SKU).

### Guards en breakdown rows

Los breakdown rows NO son seleccionables, NO disparan acciones de items, y NO entran en iteraciones de ventas:
- `_on_select`: si la selección es breakdown, `selection_remove`
- `_on_right_click`: si es breakdown, redirige a `tree.parent(row)`
- `_on_click`: no hace toggle (checks eliminados)
- Iteraciones de totales/export: usan `row_to_order` que solo tiene leaves reales

### `leaf_to_item` vs `row_to_order` vs `_all_leaves()`

- **`leaf_to_item[leaf_id]`** → datos por venta que no están en `values` (item_id, variation_id, payment_id, total_amount, quantity, etc).
- **`row_to_order`** → TODAS las leaves cargadas, incluso detached por filtro. Usar para totales que no deben depender del filtro.
- **`_all_leaves()`** → solo visibles. Usar para conteos visuales.

El mini totalizador y `_calcular_totales_seleccionados` iteran `row_to_order` a propósito. Si los pasás a `_all_leaves()`, los totales se rompen al filtrar.

### Neto MP — manual por venta

**El neto NO se calcula desde la API.** Pablo va al detalle de la venta en Mercado Pago, copia el neto real, y lo carga via doble click en la fila "Neto MP" del breakdown (o click derecho → "Cargar neto MP"). Se persiste por `order_id` en `local_store.neto_manual`.

Si no está cargado, el breakdown muestra "Neto MP: no cargado" en rojo y la venta no entra en el cómputo de ganancia/totales.

### SKU viene tal cual de la API

**El SKU es el que viene en `order_items[].item.seller_sku` (o `seller_custom_field` o `variation_attributes[SELLER_SKU]`) y no se toca.** Si está desactualizado, se edita via click derecho → "Editar SKU".

### URL de Mercado Pago — buscar por payment_id, NO order_id

F2 abre `mercadopago.com.ar/activities?q={payment_id}`. MP busca por su ID nativo, no por el de ML. No "limpiar" volviendo al order_id.

### Helpers de WSL (`_IS_WSL`, `_set_clipboard`, `_open_url`)

La app corre en Fedora y en WSL/Win10. En WSL, Tk no escribe al clipboard de Windows (hay que usar `clip.exe`) y `webbrowser.open` no abre nada (hay que usar `cmd.exe /c start`). Si los "limpiás" por parecer redundantes, rompés WSL silenciosamente.

### Constantes comerciales hardcodeadas

```python
NACIONALIZACION_MULT = 1.9     # impuestos importación China
GANANCIA_HERMANO_MULT = 1.30   # markup de Andrés
```

Al tope de `app.py`. Son políticas del usuario, no del catálogo.

## Cómo correrla

**Fedora**: `cd ~/Proyectos/ventas-ml && python3 app.py` (o el `.desktop` de GNOME).
**WSL**: alias `ventas` en `~/.bashrc`. Necesita venv y `python3-tk` instalado.

## Git

Branch `master`, remote `github.com/pflerman/ventas-ml`. Commits en español, sin prefijos `feat:`/`fix:`, el cuerpo explica el porqué cuando hace falta.
