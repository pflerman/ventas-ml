# ventas-ml — contexto para Claude

App Tkinter en Python que lista las ventas de MercadoLibre (cuenta PaliShopping, USER_ID 24192412) en vivo, agrupadas por día. Panel lateral con detalle del producto, costeo de importación FOB → precio final → ganancia neta sobre cobro de Mercado Pago, margen porcentual con chip de "BAJO/BUENO/EXCELENTE", chips diferenciando envío Flex (Héctor) vs Mercado Envíos, filtro de búsqueda/exclusión sobre el listado, modal de totales seleccionados con filas clicables para copiar al portapapeles, y un modal extra de "Frase del día" que llama a la API de Anthropic on-demand y permite enviarse la frase por WhatsApp. Pensada para uso interno del vendedor.

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

- **`leaf_to_item[leaf_id]`** — datos por venta que no están en `values`: `item_id`, `variation_id`, `quantity`, `unit_price`, `line_total`, `payment_id`, `payment_method`, `total_amount`, `sale_fee`, `shipment_id`, `shipping_cost`, `shipping_loaded` (flag), `logistic_type`, `pago_hector`, `taxes_amount`, `neto`, `title`. Cuando agregues funcionalidad por venta, **usá esto** en vez de re-parsear `values`. Notar que `coupon_amount` ya NO está — se eliminó del cálculo del neto al descubrir que `total_amount` ya incluye el listado completo (ver sección "Cálculo del neto MP" más abajo).
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

### Constantes comerciales hardcodeadas al tope de `app.py`

```python
NACIONALIZACION_MULT = 1.9     # impuestos importación China
GANANCIA_HERMANO_MULT = 1.30   # markup que cobra Andrés
PAGO_HECTOR_FLEX = 6500.0      # promedio que paga Pablo a Héctor por entrega Flex
```

Si los multiplicadores cambian (ej. Andrés sube su markup a 35%, Héctor cambia tarifa, cambian las alícuotas de importación), se editan ahí. **No están en Turso a propósito** — son políticas del usuario, no del catálogo. Si la app se compartiera entre múltiples vendedores, ahí sí habría que moverlas a config por usuario.

`PAGO_HECTOR_FLEX` arrancó como aproximación; cuando Pablo confirme el número exacto (o pase una regla por zona/peso) hay que actualizar el valor o, si la regla es compleja, refactorear a una función `calcular_pago_hector(shipping_data) → float` y llamarla desde `_on_shipping_cost_loaded`.

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

### URL del cobro de Mercado Pago — buscar por payment_id, NO order_id

Alt+click abre `https://www.mercadopago.com.ar/activities?q={payment_id}` (búsqueda) en vez del detalle directo (`/activities/detail/{id}` requiere un hash `purchase_v3-{...}` impredecible que solo conoce el frontend de MP).

**Bug histórico**: antes pasábamos `order_id` al search de MP. A veces matcheaba (cuando MP había indexado la relación) y a veces tiraba "No encontramos resultados" en órdenes válidas. **El search de MP busca por su ID nativo (`payment_id`), no por el `order_id` de ML.** La función `_on_alt_click` lee `info["payment_id"]` con fallback al `order_id` por seguridad. Si lo "limpiás" volviendo al order_id directo, la mitad de los Alt+clicks van a fallar.

### Cálculo del neto MP — qué se resta y qué NO

Fórmula actual del `neto` en `_on_data` (inicial) y `_on_shipping_cost_loaded` (final):

```python
neto = total_amount - sale_fee - shipping_cost - taxes_amount - pago_hector
```

**Lo que NO restamos** (y por qué):

- **`coupon_amount` NO se resta**. Bug histórico: antes lo restábamos y daba ~$1k de menos por orden con cupón. `total_amount` ya es el precio listado completo; el cupón es un crédito que el marketplace le devuelve al seller (MP lo muestra como "Cobro por descuento a tu contraparte" positivo), no un gasto. **No volver a restarlo.**
- **Retenciones IIBB (Tucumán + SIRTAC + otras provincias)**: ML/MP no las exponen en ningún endpoint del API público. Son retenciones impositivas que MP aplica al liquidar y dependen de la jurisdicción del comprador y del padrón en cada provincia (cambia mes a mes). **Aceptamos una diferencia residual de ~0.5%** entre el "Ganancia Mercado Pago" del app y el "Total a recibir" real de MP por este motivo.

**De dónde sale cada componente** (importante porque los endpoints son varios):

| Campo | Endpoint | Path JSON |
|---|---|---|
| `total_amount` | `/orders/search` | `order.total_amount` |
| `sale_fee` | `/orders/search` | `order.order_items[0].sale_fee × quantity` (incluye TODO: cargo por vender + costo fijo + costo cuotas) |
| `shipping_cost` | `/shipments/{id}/costs` | `senders[0].cost` (background) |
| `logistic_type` | `/shipments/{id}` | `logistic_type` (background, mismo batch) |
| `taxes_amount` | `/orders/search` | `order.taxes.amount` (viene null casi siempre) |
| `pago_hector` | constante en código | `PAGO_HECTOR_FLEX` (solo si Flex) |

`order.shipping_cost` directo viene **siempre null** en `/orders/search` — no usar ese campo. El cost real al seller siempre va por `/shipments/{id}/costs`.

### Shipping cost en background con dos endpoints + cache

`/orders/search` no trae el shipping cost real del seller, así que `_refresh_shipping_costs_batch` lo trae aparte después de cargar las orders. Hace **DOS requests por shipment_id único**:

1. `/shipments/{id}/costs` → `senders[0].cost` (lo que ML le cobra al seller)
2. `/shipments/{id}` → `logistic_type` (para detectar Flex / `self_service`)

ThreadPoolExecutor con 8 workers en paralelo. **Cache de proceso** en `self._shipping_cost_cache: dict[shipment_id → (cost, logistic_type)]` para no repetir nunca el par. Cuando llega cada respuesta, callback `_on_shipping_cost_loaded` recalcula `info["neto"]`, redibuja el panel si la fila está seleccionada, y refresca el mini totalizador (los netos van bajando en vivo a medida que llegan).

**Convención del flag `shipping_loaded`** (importante):
- Arranca en `False`, `shipping_cost = 0`. Mientras está en False, el panel muestra "Envío: cargando…" en gris.
- Cuando llega el cost se setea en `True` y se recalcula el neto.
- **NO usar `shipping_cost == 0` para distinguir "no cargado" de "cargado pero envío gratis"** — los dos casos son válidos. Siempre mirar el flag.

### Bonificación Flex no se puede capturar del API público de ML

En órdenes Flex (`logistic_type == "self_service"` con `shipping_cost == 0`), MP le suma al seller una **bonificación por el envío** que **no aparece en ningún endpoint del API público de ML**. Probé y descarté: `/orders/{id}`, `/orders/{id}/billing_info`, `/orders/{id}/discounts`, `/shipments/{id}`, `/shipments/{id}/costs`, `/shipments/{id}/charges`, `/shipments/{id}/compensations`, `/shipments/{id}/lead_time`, `/packs/{pack_id}`, `/billing/integration/group/MLA/details`, `/sites/MLA/shipping/options/{id}`. Algunos 404, los que responden no traen el monto.

La bonificación probablemente vive en el sistema de billing/liquidación de Mercado Pago (no Mercado Libre), y para sacarla habría que pedir scope `read_billing_info` en el OAuth y parsear los reportes mensuales. **Decisión explícita: NO ir por ese camino** — es mucho laburo, no garantizado, y la app ya tiene una alternativa pragmática (ver siguiente).

### Pago a Héctor en ventas Flex (constante, NO heurística)

Para órdenes Flex Pablo paga afuera a "Héctor" por hacer la entrega. Eso es un costo real que ML/MP no expone, así que se modela como constante:

```python
PAGO_HECTOR_FLEX = 6500.0  # promedio inicial al tope de app.py
```

Se aplica **solo cuando `logistic_type == "self_service"` Y `shipping_cost == 0`** (la definición operacional de "es Flex"), en `_on_shipping_cost_loaded`. Aparece como línea propia "Pago Héctor (Flex)" en el panel.

**Por qué constante y no heurística**: cuando la sesión empezó a explorar formas de "estimar" la bonificación Flex que MP suma, se decidió explícitamente NO inventar un número (heurística) porque te lleva a sobre-confianza en datos falsos. En cambio se modela el costo de Héctor (que vos sí conocés) y se avisa visualmente con el chip que la bonificación queda fuera. El número final es deliberadamente **conservador**: subestima el real, lo cual es mejor para tomar decisiones de precio (asumir lo peor).

**Limitación conocida — carrito multi-unidad del mismo comprador**: cuando un buyer compra N unidades del mismo SKU, ML lo desarma en N orders separadas (cada una con su shipment_id distinto). La app resta `N × $6,500` aunque Héctor probablemente solo cobre 1 entrega (lleva las N unidades en un viaje al mismo domicilio). Es un edge case (B2B / revendedores) y se identifica visualmente porque ves N filas seguidas con mismo SKU, hora y precio. **No detectamos ni agrupamos automáticamente** porque correlacionar por buyer + día + dirección agrega complejidad y solo aplica al ~5% de las ventas. Pablo lo maneja a ojo.

### Caso "carrito de N unidades del mismo SKU"

Cuando un comprador hace una compra de varias unidades en un solo carrito, ML lo divide en **N órdenes separadas con `pack_id` distintos** y cada una con su propio `shipment_id` y `payment_id`. La app las muestra como N filas en el tree (correcto desde la perspectiva del API). MP agrupa esas N órdenes en un solo cobro a nivel de **frontend** con un ID `purchase_v3-{hash}`, pero ese hash **no está expuesto en el API** — la página `/activities/detail/purchase_v3-...` que ves al hacer Alt+click se renderiza del lado del frontend de MP a partir de la búsqueda por payment_id.

Implicancias:
- El `_calcular_totales_seleccionados` suma N veces el neto y N veces el costo, lo cual es contablemente correcto.
- Si las N son Flex, suma N veces `PAGO_HECTOR_FLEX` (ver limitación arriba).
- No hay forma de "agrupar visualmente" estas N filas en una sola sin perder datos. **Decidir explícitamente NO agrupar.**

### Chips de tipo de envío y de margen — son señalización, no decoración

`_render_payment` muestra **uno de dos chips** según `logistic_type`:

- **`⚡ Flex (Héctor descontado)`** (naranja `#d35400`) cuando es self_service + cost 0. Avisa que el cálculo ya restó el promedio a Héctor pero **no incluye la bonificación** que MP suma al seller.
- **`📦 Mercado Envíos`** (azul `#1f4e9d`) cuando ML maneja el envío. Cálculo confiable: el cost al seller ya está descontado en la línea "Envío" del panel.

`_render_ganancia` muestra el chip de margen sobre el bruto (`ganancia / total_amount × 100`):

| Margen | Chip | Color |
|---|---|---|
| < 10% | **MUY BAJO** | `#c0392b` rojo |
| 10-20% | **BAJO** | `#d35400` naranja |
| 20-30% | **BUENO** | `#1e7a1e` verde |
| > 30% | **EXCELENTE** | `#7d3c98` violeta |

**Los umbrales son aproximados para revendedores de importación en ML Argentina**, no son universales. Si Pablo cambia de rubro o estrategia, son los primeros números a revisar.

**Importante: NO simplificar a un solo chip "tipo envío"** — la diferencia entre "cálculo confiable (Mercado Envíos)" y "cálculo con dos componentes ciegos (Flex)" es justo la información valiosa. Tampoco renombrar los chips con nombres genéricos: el texto "Héctor descontado" / "Mercado Envíos" es lo que comunica al usuario qué confianza tener en el número.

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
