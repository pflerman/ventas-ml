# ventas-ml

App Tkinter que lista las ventas de MercadoLibre (PaliShopping, USER_ID 24192412) en vivo, agrupadas por día, con costeo de importación, neto MP, notas libres por venta y un modal de "Frase del día" que llama a Anthropic API. Uso interno del vendedor.

## Dependencias externas

No es autocontenido. Necesita:

- **`~/Proyectos/gestor-productos/.env`** — Turso URL/token + `ANTHROPIC_API_KEY`. Lo leen `productos_lookup.py`, `ventas_db.py` y `frase.py`.
- **`~/Proyectos/mercadolibre-mcp/`** — `app.py` hace `from ml_auth import MLAuth` para reusar el refresh de tokens de ML.
- **`~/Proyectos/ml-scripts/config/ml_credentials_palishopping.json`** — access/refresh tokens de ML, no versionado.
- **`dolarapi.com`** — cotización oficial al arrancar (`dolar.py`). Si está caída, los pesos no aparecen pero la app sigue.
- **whatsapp-mcp en `localhost:3100`** — solo Fedora. En WSL el botón "Mandar a Pablo" del modal de Frase falla (esperado).
- **Fuentes Fedora** en `/usr/share/fonts/` (LiberationMono-Bold + Symbola) — el modal de Frase rinde con PIL porque Tk-Linux no muestra emojis. Si las paths no existen, el modal tira error pero el resto anda.

## Trampas

### Tupla `values` del Treeview

`values = (check, fecha, sku, cant, producto, precio, subtotal)`. Cuando muevas o agregues columnas, **grep obligatorio de `values\[` y `values =`** antes de cerrar el cambio. Ya pasó: agregar `cant` rompió `_copy_clicked_title` que accedía por índice. Preferí siempre unpacking sobre acceso por índice — falla loud si cambia la forma.

### `leaf_to_item` vs `row_to_order` vs `_all_leaves()`

- **`leaf_to_item[leaf_id]`** → datos por venta que no están en `values` (item_id, payment_id, neto, shipping_loaded, etc). Usar esto para funcionalidad por venta, no re-parsear `values`.
- **`row_to_order`** → TODAS las leaves cargadas, incluso detached por filtro. Usar para totales que no deben depender del filtro.
- **`_all_leaves()`** → solo visibles. Usar para conteos visuales.

El mini totalizador y `_calcular_totales_seleccionados` iteran `row_to_order` a propósito. Si los pasás a `_all_leaves()`, los totales se rompen al filtrar.

### Cálculo del neto MP

```python
neto = total_amount - sale_fee - shipping_cost - taxes_amount - pago_hector
```

**No restar `coupon_amount`** — bug histórico, `total_amount` ya incluye el listado completo, el cupón es un crédito al seller. Aceptamos ~0.5% de diferencia residual con el "Total a recibir" real de MP por retenciones IIBB que ML/MP no exponen en el API público.

`order.shipping_cost` viene **siempre null** en `/orders/search`. El cost real al seller siempre va por `/shipments/{id}/costs` (en background, con cache por shipment_id). Hay un flag `shipping_loaded` — no usar `shipping_cost == 0` para distinguir "no cargado" de "envío gratis", son dos casos válidos distintos.

### Bonificación Flex no se puede sacar del API público

Ya lo investigué y descarté: `/orders/{id}`, `/orders/{id}/billing_info`, `/shipments/{id}`, `/shipments/{id}/costs`, `/shipments/{id}/charges`, `/shipments/{id}/compensations`, `/packs/{pack_id}`, `/billing/integration/group/MLA/details`. Vive en el billing de MP, requeriría scope `read_billing_info` y parsear reportes mensuales. **Decisión: no ir por ahí.** En su lugar modelamos `PAGO_HECTOR_FLEX = 6500.0` (constante al tope de `app.py`) que se aplica solo cuando `logistic_type == "self_service"` y `shipping_cost == 0`. Es deliberadamente conservador.

### URL de Mercado Pago — buscar por payment_id, NO order_id

`Alt+click` abre `mercadopago.com.ar/activities?q={payment_id}`. Antes pasábamos `order_id` y la mitad de los Alt+clicks fallaban porque MP busca por su ID nativo, no por el de ML. No "limpiar" volviendo al order_id.

### Helpers de WSL (`_IS_WSL`, `_set_clipboard`, `_open_url`)

La app corre en Fedora y en WSL/Win10. En WSL, Tk no escribe al clipboard de Windows (hay que usar `clip.exe`) y `webbrowser.open` no abre nada (hay que usar `cmd.exe /c start`). Si los "limpiás" por parecer redundantes, rompés WSL silenciosamente.

### Cache de checks/fobs/notas: optimista + write-through async

`ventas_db` expone `mark_local` (sync, solo cache, para que la UI cambie al instante) y `persist_check` (escribe a Turso desde un thread). **No las fusiones** — si las unís, cada click espera 100-300ms de Turso y la UI se siente lenta. Mismo patrón para `set_nota_local`/`persist_nota` y FOBs.

### Constantes comerciales hardcodeadas

```python
NACIONALIZACION_MULT = 1.9     # impuestos importación China
GANANCIA_HERMANO_MULT = 1.30   # markup de Andrés
PAGO_HECTOR_FLEX = 6500.0      # pago a Héctor por entrega Flex
```

Al tope de `app.py`. No están en Turso a propósito — son políticas del usuario, no del catálogo.

## Cómo correrla

**Fedora**: `cd ~/Proyectos/ventas-ml && python3 app.py` (o el `.desktop` de GNOME).
**WSL**: alias `ventas` en `~/.bashrc`. Necesita venv y `python3-tk` instalado.

## Git

Branch `master`, remote `github.com/pflerman/ventas-ml`. Commits en español, sin prefijos `feat:`/`fix:`, el cuerpo explica el porqué cuando hace falta.
