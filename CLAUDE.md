# ventas-ml — contexto para Claude

App Tkinter en Python que lista las ventas de MercadoLibre (cuenta PaliShopping, USER_ID 24192412) en vivo, agrupadas por día. Panel lateral con detalle del producto, costeo de importación FOB → precio final → ganancia neta sobre cobro de Mercado Pago, filtro de búsqueda/exclusión sobre el listado, modal de totales seleccionados con filas clicables para copiar al portapapeles, y un modal extra de "Frase del día" que llama a la API de Anthropic on-demand y permite enviarse la frase por WhatsApp. Pensada para uso interno del vendedor.

## Dependencias externas (críticas)

`ventas-ml` no es autocontenido. Necesita 3 repos hermanos en `~/Proyectos/`:

1. **`gestor-productos/`** — `productos_lookup.py`, `ventas_db.py` y **`frase.py`** leen `~/Proyectos/gestor-productos/.env`. Los dos primeros para conectarse a la misma DB Turso (lookup `{sku → producto}` + tablas propias `ventas_checks` y `ventas_fob`). `frase.py` reusa la **`ANTHROPIC_API_KEY`** que también vive en ese mismo `.env` — si la rotás ahí, automáticamente vale acá.
2. **`mercadolibre-mcp/`** — `app.py` hace `sys.path.insert` y `from ml_auth import MLAuth` para reusar el manejo de refresh tokens. Si este repo no existe, la app no arranca.
3. **`ml-scripts/config/ml_credentials_palishopping.json`** — JSON con `access_token` / `refresh_token` de ML. No está versionado en ningún lado (secrets).

Además, servicios y APIs externas:
- **`dolarapi.com/v1/dolares/oficial`** — cotización del dólar oficial, una vez al arrancar (`dolar.py`). Si está caída, los cálculos de FOB en pesos no aparecen pero el resto de la app funciona.
- **API de Anthropic** — `frase.py` la llama on-demand cuando apretás el botón "Frase del día". Modelo `claude-haiku-4-5-20251001`. Si falta la key o la API está caída, el modal muestra "(no se pudo cargar)" y el resto de la app sigue funcionando.
- **`whatsapp-mcp` (servicio systemd)** — el botón "Mandar a Pablo" del modal de Frase usa `whatsapp_send.py`, que abre una sesión efímera al MCP server en `http://localhost:3100/mcp` (StreamableHTTP) vía el SDK oficial `mcp` de Python. El servicio vive en `~/Proyectos/whatsapp-mcp/` corriendo como `whatsapp-mcp.service` con `Restart=always`. **Solo funciona en Fedora** — en WSL no está corrido, así que el botón va a fallar con error de conexión (esperado).

### Fuentes del sistema (Fedora)

El modal de "Frase del día" usa **paths absolutos** a dos fuentes que vienen con Fedora:
- `/usr/share/fonts/liberation-mono-fonts/LiberationMono-Bold.ttf` (texto en mono bold)
- `/usr/share/fonts/gdouros-symbola/Symbola.ttf` (emojis monocromos — la única fuente del sistema con Latin **y** emojis en un solo archivo)

Si las paths no existen, el render PIL tira excepción y el modal muestra el error. En WSL probablemente no estén instaladas, así que el modal de Frase no va a funcionar ahí (igual que el botón de WhatsApp).

## Arquitectura mental rápida

- **`app.py`** — monolito con la clase `VentasApp`. UI = barra de filtro arriba del tree, `PanedWindow` horizontal (Treeview izq, panel scrollable der), bottom bar con status + cotización + mini totalizador + botones.
- **`productos_lookup.py`** — cache `{sku → dict}` en memoria, lectura única al arrancar (~100 productos). Provee título y etiquetas.
- **`ventas_db.py`** — cache en memoria + write-through asíncrono para checks y FOBs. Mismo patrón que `gestor-productos/app/db.py`. Tablas `ventas_checks` y `ventas_fob` (con columna `multiplicador` nullable).
- **`dolar.py`** — fetch único de la cotización al arrancar, queda en memoria hasta cerrar la app.
- **`frase.py`** — gemelo a `dolar.py` (mismo patrón `cargar()` / `get()` / `loaded()`), pero **no se llama al arrancar**. Solo cuando el usuario abre el modal de "Frase del día". Cada apertura del modal llama a `cargar()` de nuevo, así que cada vez es una frase fresca.
- **`whatsapp_send.py`** — cliente mínimo del MCP de whatsapp-mcp. Función sync `enviar(mensaje, contacto)` que abre una sesión MCP efímera, llama al tool `enviar_mensaje` y cierra. Devuelve `(ok, detalle)`. Quien lo llama es el que decide si lo corre en thread (la UI sí lo hace).
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

### Emojis no se rinden en Tk-Linux — la frase usa PIL

Tk en Linux (Fedora con `python3-tk`) **no rinde emojis Unicode** con la fuente default — aparecen como rectangulitos vacíos. Por eso `_render_frase_image` (en `app.py`, dentro del bloque "Frase del día (modal)") arma la frase como una imagen PNG con PIL: dos fuentes (`LiberationMono-Bold` para texto, `Symbola` para emojis), heurística simple `cp < 0x2000 → mono, sino → emoji`, wrap manual por palabras, render char-por-char advanzando `x` con `getbbox`. Después se entrega como `ImageTk.PhotoImage` a un Label.

**No "simplificar" volviendo a un Label de texto plano** — los emojis desaparecen y la frase queda mocha. Si querés cambiar la apariencia (colores, tamaño), tocá las constantes al tope de `_render_frase_image` (`color`, `bg`, `font_size`). El look actual es **CRT verde fósforo `#33ff33` sobre negro**, estilo terminal de los 80, decisión explícita del usuario.

La regla `cp < 0x2000` es un hack pero alcanza para ASCII + Latin-1 + signos básicos en mono y todo lo "raro" (emojis, símbolos, dingbats) en Symbola. Si aparecen casos raros, ajustar la heurística.

### Frase del día se carga on-demand, no al arrancar

`frase.cargar()` **no se llama** desde `__init__` ni desde `_cargar_*_async`. Solo se dispara cuando abrís el modal con el botón "✨ Frase del día ✨" (al final del panel de detalle). Cada apertura genera frase nueva. Es a propósito: no spammeamos la API en cada arranque y no distraemos al usuario con texto cambiante en el panel principal.

Si en el futuro quieren que la frase quede visible inline en el panel, hay que decidir explícitamente cuándo refrescarla — no caer en "cada vez que el panel re-renderiza" porque dispara un request por cada click en el tree.

### JID de Pablo hardcodeado en `whatsapp_send.py`

`PABLO_JID = "5491140461603@s.whatsapp.net"` — sacado del `Environment=WHATSAPP_PHONE=...` del unit file `/etc/systemd/system/whatsapp-mcp.service`. **No vive en `contactos.json` de whatsapp-mcp** porque Pablo no se tiene a sí mismo guardado como contacto. Si Pablo cambia de número, hay que actualizar acá Y en el unit file de whatsapp-mcp.

Mando al JID directo en vez de un nombre justamente para no depender de `contactos.json`.

### Modal de totales: filas centradas con `grid` + `columnconfigure`, no `pack(anchor="center")`

Las filas "Total para pagar a Andrés" y "Ganancia de Pablo total" del modal de totales se centran horizontalmente con un `center_box` que usa `grid_columnconfigure(0, weight=1)` + `columnconfigure(2, weight=1)` y los rows en `column=1`. **Probé primero con `pack(anchor="center")` y NO centra bien** — Tk asigna un parcel de altura limitada al ancho del contenido, no del parent, así que las filas quedaban desplazadas a la derecha. El grid resuelve correctamente con las dos columnas vacías absorbiendo el sobrante por igual. Si tocás esto, no vuelvas a pack.

### Filas clicables del modal de totales copian al portapapeles

Las filas rojo/verde del modal "Totales de seleccionadas" tienen `cursor="hand2"` y bind `<Button-1>` sobre el frame y los dos labels. Click → `_set_clipboard(...)` + `_flash_status(...)`. El cursor manito **no es decorativo**: es la única señal visual de que son clicables. No lo saques.

## Helpers / decisiones que parecen raras pero tienen razón

### `_IS_WSL`, `_set_clipboard`, `_open_url` (NO TOCAR sin entender)

La app corre en **dos entornos**: Fedora (host del usuario) y WSL en Win10 (PC de fábrica). Quirks de WSL:

- **Clipboard**: Tk escribe al X11 buffer de WSLg, que no se sincroniza con el clipboard de Windows. Fix: en WSL usar `clip.exe` vía subprocess.
- **Browser**: `webbrowser.open()` en WSL intenta `xdg-open` y no abre nada. Fix: en WSL usar `cmd.exe /c start`.

`_IS_WSL` se calcula una vez al cargar el módulo leyendo `/proc/version`. Si "limpiás" estos helpers porque parecen redundantes, **rompés el laburo en WSL silenciosamente**.

### Paths absolutos a fuentes del sistema en `_render_frase_image`

Las constantes `_SYMBOLA_PATH` y `_MONO_PATH` son **paths absolutos** a `/usr/share/fonts/...` específicos de Fedora. No hay fallback ni búsqueda dinámica. **No las "limpies" buscando con `fontconfig`** — el render PIL necesita un path TTF concreto, no un nombre de familia. Si la app se va a correr en otra distro habría que parametrizar, pero por ahora WSL no usa el modal de Frase (no tiene el servicio whatsapp-mcp tampoco) así que el problema no se manifiesta.

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
