#!/usr/bin/env python3
"""Ventas PaliShopping - Lista ventas de MercadoLibre en vivo."""
import io
import json
import os
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, font as tkfont
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

import productos_lookup

# WSL no sincroniza el clipboard de Tk (X11/WSLg) con el de Windows.
# Si estamos en WSL usamos clip.exe directo para que Ctrl+V funcione en apps Windows.
def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False

_IS_WSL = _is_wsl()
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

USER_ID = 24192412
PAGE_SIZE = 50
API_URL = "https://api.mercadolibre.com/orders/search"
SELECTIONS_PATH = Path(__file__).resolve().parent / "seleccionadas.json"

CHECKED = "☑"
UNCHECKED = "☐"


def load_selections() -> set:
    if not SELECTIONS_PATH.exists():
        SELECTIONS_PATH.write_text("[]", encoding="utf-8")
        return set()
    try:
        data = json.loads(SELECTIONS_PATH.read_text(encoding="utf-8"))
        return {str(x) for x in data}
    except (json.JSONDecodeError, OSError):
        return set()


def save_selections(ids: set) -> None:
    SELECTIONS_PATH.write_text(
        json.dumps(sorted(ids), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

# Reutilizamos MLAuth del proyecto mercadolibre-mcp para manejar refresh.
ML_MCP_DIR = Path.home() / "Proyectos" / "mercadolibre-mcp"
CREDENTIALS_PATH = (
    Path.home() / "Proyectos" / "ml-scripts" / "config" / "ml_credentials_palishopping.json"
)
sys.path.insert(0, str(ML_MCP_DIR))
from ml_auth import MLAuth  # noqa: E402


def get_auth() -> MLAuth:
    return MLAuth(str(CREDENTIALS_PATH))


def fetch_orders(auth: MLAuth, offset: int = 0, limit: int = PAGE_SIZE) -> dict:
    params = {
        "seller": USER_ID,
        "sort": "date_desc",
        "offset": offset,
        "limit": limit,
    }
    url = f"{API_URL}?{urlencode(params)}"
    req = Request(url, headers={"Authorization": f"Bearer {auth.access_token}"})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_iso(iso: str):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_day(dt) -> str:
    return dt.strftime("%d/%m/%Y") if dt else ""


def format_time(dt) -> str:
    return dt.strftime("%H:%M") if dt else ""


def extract_sku(order_item: dict) -> str:
    """SKU puede venir en distintos campos según el item."""
    item = order_item.get("item") or {}
    # 1. seller_sku directo del item
    sku = item.get("seller_sku")
    if sku:
        return str(sku)
    # 2. seller_custom_field (legacy)
    sku = item.get("seller_custom_field")
    if sku:
        return str(sku)
    # 3. variation_attributes con SELLER_SKU
    for attr in item.get("variation_attributes") or []:
        if (attr.get("id") or "").upper() == "SELLER_SKU":
            v = attr.get("value_name")
            if v:
                return str(v)
    return ""


def format_price(value) -> str:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    return f"${n:,.2f}"


class VentasApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Ventas PaliShopping")
        self.root.geometry("900x600")

        try:
            self.auth = get_auth()
        except Exception as e:
            messagebox.showerror("Error", f"No se pudieron cargar las credenciales:\n{e}")
            raise SystemExit(1)

        self.offset = 0
        self.total = 0
        self.loading = False
        self.selected_ids: set = load_selections()
        self.row_to_order: dict = {}  # tree item id -> order_id (solo hojas)
        self.row_base: dict = {}  # tree item id -> "odd"/"even"
        self.leaf_to_item: dict = {}  # tree leaf id -> {item_id, variation_id, sku, title}
        self.day_nodes: dict = {}  # "DD/MM/YYYY" -> parent row id
        self.day_count: dict = {}  # "DD/MM/YYYY" -> int
        self.day_total: dict = {}  # "DD/MM/YYYY" -> float

        self._build_ui()
        self._cargar_productos_async()
        self.refresh()

    def _build_ui(self):
        base_font = tkfont.nametofont("TkDefaultFont")
        base_font.configure(size=11)
        self.root.option_add("*Font", base_font)

        style = ttk.Style()
        style.configure("Treeview", rowheight=28, font=("TkDefaultFont", 11))
        style.configure("Treeview.Heading", font=("TkDefaultFont", 11, "bold"))

        # Tk 8.6 bug: tag backgrounds ignored a menos que se reescriba el style.map.
        def _fixed_map(option):
            return [
                e for e in style.map("Treeview", query_opt=option)
                if e[:2] != ("!disabled", "!selected")
            ]
        style.map(
            "Treeview",
            foreground=_fixed_map("foreground"),
            background=_fixed_map("background"),
        )

        container = ttk.Frame(self.root, padding=10)
        container.pack(fill="both", expand=True)

        paned = ttk.PanedWindow(container, orient="horizontal")
        paned.pack(fill="both", expand=True)

        tree_frame = ttk.Frame(paned)
        paned.add(tree_frame, weight=3)

        self.detail_frame = ttk.Frame(paned, padding=(10, 4))
        paned.add(self.detail_frame, weight=1)
        self._build_detail_panel()

        columns = ("check", "fecha", "sku", "cant", "producto", "precio", "subtotal")
        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="Día")
        self.tree.heading("check", text=UNCHECKED, command=self._toggle_all)
        self.tree.heading("fecha", text="Hora")
        self.tree.heading("sku", text="SKU")
        self.tree.heading("cant", text="Cant")
        self.tree.heading("producto", text="Producto")
        self.tree.heading("precio", text="Precio U.")
        self.tree.heading("subtotal", text="Subtotal")
        self.tree.column("#0", width=200, anchor="w", stretch=False)
        self.tree.column("check", width=40, anchor="center", stretch=False)
        self.tree.column("fecha", width=60, anchor="w", stretch=False)
        self.tree.column("sku", width=90, anchor="w", stretch=False)
        self.tree.column("cant", width=50, anchor="center", stretch=False)
        self.tree.column("producto", width=280, anchor="w")
        self.tree.column("precio", width=110, anchor="e", stretch=False)
        self.tree.column("subtotal", width=110, anchor="e", stretch=False)
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Control-Button-1>", self._on_ctrl_click)
        self.tree.bind("<Shift-Button-1>", self._on_shift_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Alt-Button-1>", self._on_alt_click)

        self._hint_active = None  # "ctrl" / "shift" / None
        self._status_before_hint = ""
        self.root.bind("<KeyPress-Control_L>", lambda e: self._show_hint("ctrl"))
        self.root.bind("<KeyPress-Control_R>", lambda e: self._show_hint("ctrl"))
        self.root.bind("<KeyRelease-Control_L>", lambda e: self._hide_hint("ctrl"))
        self.root.bind("<KeyRelease-Control_R>", lambda e: self._hide_hint("ctrl"))
        self.root.bind("<KeyPress-Shift_L>", lambda e: self._show_hint("shift"))
        self.root.bind("<KeyPress-Shift_R>", lambda e: self._show_hint("shift"))
        self.root.bind("<KeyRelease-Shift_L>", lambda e: self._hide_hint("shift"))
        self.root.bind("<KeyRelease-Shift_R>", lambda e: self._hide_hint("shift"))

        self.context_menu = tk.Menu(self.tree, tearoff=0)
        self.context_menu.add_command(
            label="Copiar SKU", command=self._copy_clicked_sku
        )
        self.context_menu.add_command(
            label="Copiar título", command=self._copy_clicked_title
        )
        self.context_menu.add_command(
            label="Copiar ID publicación", command=self._copy_clicked_item_id
        )
        self.context_menu.add_command(
            label="Copiar selección (WhatsApp)",
            command=self._copy_selected_to_clipboard,
        )
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Refrescar fila", command=self._refresh_clicked_row
        )
        self.context_menu.add_command(label="Refrescar todo", command=self.refresh)
        self.context_menu.bind("<FocusOut>", lambda e: self.context_menu.unpost())
        self._right_clicked_row = None

        self.tree.tag_configure("odd", background="#f5f5f5")
        self.tree.tag_configure("even", background="#ffffff")
        self.tree.tag_configure("selected", background="#C6EFCE")
        self.tree.tag_configure(
            "day", background="#dce6f0", font=("TkDefaultFont", 11, "bold")
        )

        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        bottom = ttk.Frame(container)
        bottom.pack(fill="x", pady=(10, 0))

        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left")

        self.btn_more = ttk.Button(bottom, text="Cargar más", command=self.load_more)
        self.btn_more.pack(side="right", padx=(6, 0))
        self.btn_refresh = ttk.Button(bottom, text="Actualizar", command=self.refresh)
        self.btn_refresh.pack(side="right")
        self.btn_export = ttk.Button(
            bottom, text="Exportar Excel", command=self.export_excel
        )
        self.btn_export.pack(side="right", padx=(0, 6))

    def _build_detail_panel(self):
        ttk.Label(
            self.detail_frame,
            text="Producto",
            font=("TkDefaultFont", 12, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        self.detail_status_var = tk.StringVar(value="Seleccioná una venta")
        self.detail_status_lbl = ttk.Label(
            self.detail_frame,
            textvariable=self.detail_status_var,
            foreground="#888",
            wraplength=240,
            justify="left",
        )
        self.detail_status_lbl.pack(anchor="w", pady=(0, 8))

        ttk.Label(
            self.detail_frame,
            text="Etiquetas",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor="w", pady=(8, 4))

        self.detail_tags_frame = ttk.Frame(self.detail_frame)
        self.detail_tags_frame.pack(fill="x", anchor="w")

        ttk.Separator(self.detail_frame, orient="horizontal").pack(
            fill="x", pady=(12, 8)
        )

        ttk.Label(
            self.detail_frame,
            text="Cobro Mercado Pago",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(anchor="w", pady=(0, 4))

        self.detail_payment_frame = ttk.Frame(self.detail_frame)
        self.detail_payment_frame.pack(fill="x", anchor="w")

        self.detail_payment_hint = ttk.Label(
            self.detail_frame,
            text="Alt + click en la fila → abrir en MP",
            foreground="#888",
            font=("TkDefaultFont", 9, "italic"),
        )
        self.detail_payment_hint.pack(anchor="w", pady=(8, 0))

    def _on_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        leaf_id = sel[0]
        info = self.leaf_to_item.get(leaf_id)
        if not info:
            # Es una fila de día, no una venta
            self._update_detail(None, None, None)
            return
        sku = info.get("sku") or ""
        producto = productos_lookup.get(sku) if sku else None
        self._update_detail(sku, producto, info)

    def _update_detail(self, sku: str | None, producto: dict | None, info: dict | None):
        # Limpiar chips anteriores
        for w in self.detail_tags_frame.winfo_children():
            w.destroy()
        for w in self.detail_payment_frame.winfo_children():
            w.destroy()

        self._render_payment(info)

        if sku is None:
            self.detail_status_var.set("Seleccioná una venta")
            self.detail_status_lbl.configure(foreground="#888")
            return

        if not productos_lookup.loaded():
            self.detail_status_var.set("Cargando productos…")
            self.detail_status_lbl.configure(foreground="#888")
            return

        if not sku:
            self.detail_status_var.set("⚠️ Esta venta no tiene SKU")
            self.detail_status_lbl.configure(foreground="#c0392b")
            return

        if not producto:
            self.detail_status_var.set(
                f"⚠️ SKU {sku} no encontrado en gestor-productos"
            )
            self.detail_status_lbl.configure(foreground="#c0392b")
            return

        nombre = producto.get("nombre") or ""
        self.detail_status_var.set(f"{sku}\n{nombre}")
        self.detail_status_lbl.configure(foreground="#333")

        etiquetas_raw = producto.get("etiquetas") or ""
        etiquetas = [e.strip() for e in etiquetas_raw.split(",") if e.strip()]
        if not etiquetas:
            ttk.Label(
                self.detail_tags_frame,
                text="(sin etiquetas)",
                foreground="#888",
            ).pack(anchor="w")
            return
        for et in etiquetas:
            chip = tk.Label(
                self.detail_tags_frame,
                text=et,
                background="#dce6f0",
                foreground="#1a3a5c",
                padx=8,
                pady=2,
                borderwidth=0,
            )
            chip.pack(anchor="w", pady=2)

    def _render_payment(self, info: dict | None):
        if not info or info.get("payment_id") is None:
            ttk.Label(
                self.detail_payment_frame,
                text="(sin datos de pago)",
                foreground="#888",
            ).pack(anchor="w")
            return

        rows = [
            ("Bruto", info.get("total_amount") or 0, "#1a3a5c"),
            ("Comisión ML", -(info.get("sale_fee") or 0), "#c0392b"),
        ]
        if info.get("shipping_cost"):
            rows.append(("Envío", -(info.get("shipping_cost") or 0), "#c0392b"))
        if info.get("taxes_amount"):
            rows.append(("Impuestos", -(info.get("taxes_amount") or 0), "#c0392b"))
        if info.get("coupon_amount"):
            rows.append(("Cupón", -(info.get("coupon_amount") or 0), "#c0392b"))
        rows.append(("Neto", info.get("neto") or 0, "#1e7a1e"))

        for label, value, color in rows:
            row = ttk.Frame(self.detail_payment_frame)
            row.pack(fill="x", anchor="w", pady=1)
            is_total = label in ("Bruto", "Neto")
            font = ("TkDefaultFont", 10, "bold") if is_total else ("TkDefaultFont", 10)
            ttk.Label(row, text=label, font=font).pack(side="left")
            tk.Label(
                row,
                text=format_price(value),
                foreground=color,
                font=font,
            ).pack(side="right")

        method = info.get("payment_method") or ""
        if method:
            ttk.Label(
                self.detail_payment_frame,
                text=f"Método: {method}",
                foreground="#888",
                font=("TkDefaultFont", 9),
            ).pack(anchor="w", pady=(6, 0))

        ttk.Label(
            self.detail_payment_frame,
            text=f"Pago #{info.get('payment_id')}",
            foreground="#888",
            font=("TkDefaultFont", 9),
        ).pack(anchor="w")

    def _on_alt_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        order_id = self.row_to_order.get(row)
        if not order_id:
            return
        # MP busca por order_id de ML y muestra el cobro asociado.
        # Usamos /activities?q=ID porque /activities/detail/{id} requiere un hash
        # purchase_v3-{...} impredecible que solo conoce el frontend de MP.
        url = f"https://www.mercadopago.com.ar/activities?q={order_id}"
        self._open_url(url)
        return "break"

    def _cargar_productos_async(self):
        def worker():
            try:
                n = productos_lookup.cargar()
                self.root.after(0, lambda: self._on_productos_cargados(n, None))
            except Exception as e:
                self.root.after(0, lambda: self._on_productos_cargados(0, str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def _on_productos_cargados(self, n: int, error: str | None):
        if error:
            self.detail_status_var.set(f"⚠️ Error cargando productos:\n{error}")
            self.detail_status_lbl.configure(foreground="#c0392b")
            return
        # Refrescar el detalle si ya hay una fila seleccionada
        self._on_select()

    def _all_leaves(self) -> list:
        leaves = []
        for parent in self.tree.get_children(""):
            leaves.extend(self.tree.get_children(parent))
        return leaves

    def _set_loading(self, loading: bool):
        self.loading = loading
        state = "disabled" if loading else "normal"
        self.btn_refresh.configure(state=state)
        if loading:
            self.btn_more.configure(state="disabled")
            self.status_var.set("Cargando...")
        else:
            self._update_status()
            shown = len(self._all_leaves())
            if shown < self.total:
                self.btn_more.configure(state="normal")
            else:
                self.btn_more.configure(state="disabled")

    def _update_status(self):
        shown = len(self._all_leaves())
        days = len(self.tree.get_children(""))
        self.status_var.set(
            f"{shown} de {self.total} ventas en {days} días  •  "
            f"{len(self.selected_ids)} seleccionadas"
        )

    def _save(self):
        try:
            save_selections(self.selected_ids)
        except OSError as e:
            messagebox.showerror("Error", f"No se pudo guardar selecciones:\n{e}")

    def _set_leaf_check(self, leaf_id: str, checked: bool):
        order_id = self.row_to_order.get(leaf_id)
        if not order_id:
            return
        if checked:
            self.selected_ids.add(order_id)
        else:
            self.selected_ids.discard(order_id)
        values = list(self.tree.item(leaf_id, "values"))
        values[0] = CHECKED if checked else UNCHECKED
        self.tree.item(leaf_id, values=values, tags=self._row_tags(leaf_id, order_id))

    def _toggle(self, leaf_id: str):
        order_id = self.row_to_order.get(leaf_id)
        if order_id is None:
            return
        self._set_leaf_check(leaf_id, order_id not in self.selected_ids)
        parent = self.tree.parent(leaf_id)
        if parent:
            self._update_day_check(parent)
        self._save()
        self._update_header_check()
        self._update_status()

    def _toggle_day(self, parent_id: str):
        leaves = self.tree.get_children(parent_id)
        if not leaves:
            return
        all_checked = all(
            self.row_to_order.get(l) in self.selected_ids for l in leaves
        )
        for l in leaves:
            self._set_leaf_check(l, not all_checked)
        self._update_day_check(parent_id)
        self._save()
        self._update_header_check()
        self._update_status()

    def _toggle_all(self):
        leaves = self._all_leaves()
        if not leaves:
            return
        all_checked = all(
            self.row_to_order.get(l) in self.selected_ids for l in leaves
        )
        for l in leaves:
            self._set_leaf_check(l, not all_checked)
        for parent in self.tree.get_children(""):
            self._update_day_check(parent)
        self._save()
        self._update_header_check()
        self._update_status()

    def _update_day_check(self, parent_id: str):
        leaves = self.tree.get_children(parent_id)
        if not leaves:
            return
        all_checked = all(
            self.row_to_order.get(l) in self.selected_ids for l in leaves
        )
        values = list(self.tree.item(parent_id, "values"))
        values[0] = CHECKED if all_checked else UNCHECKED
        self.tree.item(parent_id, values=values)

    def _update_header_check(self):
        leaves = self._all_leaves()
        if leaves and all(
            self.row_to_order.get(l) in self.selected_ids for l in leaves
        ):
            self.tree.heading("check", text=CHECKED)
        else:
            self.tree.heading("check", text=UNCHECKED)

    def _row_tags(self, row_id: str, order_id: str) -> tuple:
        base = self.row_base.get(row_id, "even")
        if order_id in self.selected_ids:
            return ("selected",)
        return (base,)

    def _on_right_click(self, event):
        row = self.tree.identify_row(event.y)
        self._right_clicked_row = row
        # Habilitar/deshabilitar items que requieren una hoja.
        is_leaf = bool(row) and self.tree.parent(row) != ""
        leaf_state = "normal" if is_leaf else "disabled"
        self.context_menu.entryconfig("Copiar título", state=leaf_state)
        self.context_menu.entryconfig("Copiar SKU", state=leaf_state)
        self.context_menu.entryconfig("Copiar ID publicación", state=leaf_state)
        self.context_menu.entryconfig("Refrescar fila", state=leaf_state)
        self.context_menu.tk_popup(event.x_root, event.y_root)
        self.context_menu.focus_set()

    def _on_ctrl_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row or self.tree.parent(row) == "":
            return "break"
        info = self.leaf_to_item.get(row)
        item_id = (info or {}).get("item_id")
        if not item_id:
            return "break"
        # MLA1234567890 -> articulo.mercadolibre.com.ar/MLA-1234567890 (redirige al canónico)
        if len(item_id) > 3 and item_id[:3].isalpha():
            url = f"https://articulo.mercadolibre.com.ar/{item_id[:3]}-{item_id[3:]}"
        else:
            url = f"https://articulo.mercadolibre.com.ar/{item_id}"
        self._open_url(url)
        self._flash_status(f"Abriendo publicación {item_id}")
        return "break"

    def _on_shift_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row or self.tree.parent(row) == "":
            return "break"
        order_id = self.row_to_order.get(row)
        if not order_id:
            return "break"
        url = f"https://www.mercadolibre.com.ar/ventas/{order_id}/detalle"
        self._open_url(url)
        self._flash_status(f"Abriendo venta {order_id}")
        return "break"

    def _on_double_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row or self.tree.parent(row) == "":
            return
        info = self.leaf_to_item.get(row)
        if not info or not info.get("item_id"):
            return
        self._open_sku_modal(row, info)

    def _open_sku_modal(self, leaf_id: str, info: dict):
        win = tk.Toplevel(self.root)
        win.title("Editar SKU")
        win.transient(self.root)
        win.resizable(False, False)

        frame = ttk.Frame(win, padding=15)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=info.get("title", ""),
            wraplength=420,
            font=("TkDefaultFont", 11, "bold"),
        ).pack(anchor="w", pady=(0, 4))

        meta = info.get("item_id", "")
        if info.get("variation_id"):
            meta += f"  ·  variación {info['variation_id']}"
        ttk.Label(frame, text=meta, foreground="#666").pack(anchor="w", pady=(0, 12))

        ttk.Label(frame, text="SKU:").pack(anchor="w")
        sku_var = tk.StringVar(value=info.get("sku") or "")
        entry = ttk.Entry(frame, textvariable=sku_var, width=42)
        entry.pack(fill="x", pady=(2, 12))
        entry.focus_set()
        entry.select_range(0, "end")

        btns = ttk.Frame(frame)
        btns.pack(fill="x")

        save_btn = ttk.Button(btns, text="Guardar")
        cancel_btn = ttk.Button(btns, text="Cancelar", command=win.destroy)
        save_btn.pack(side="right")
        cancel_btn.pack(side="right", padx=(0, 6))

        def do_save():
            new_sku = sku_var.get().strip()
            if new_sku == (info.get("sku") or ""):
                win.destroy()
                return
            save_btn.configure(state="disabled", text="Guardando...")
            cancel_btn.configure(state="disabled")
            entry.configure(state="disabled")

            def worker():
                try:
                    self._put_item_sku(
                        info["item_id"], info.get("variation_id"), new_sku
                    )
                except HTTPError as e:
                    body = ""
                    try:
                        body = e.read().decode("utf-8", errors="replace")[:300]
                    except Exception:
                        pass
                    msg = f"HTTP {e.code}: {e.reason}\n{body}"
                    self.root.after(
                        0,
                        lambda m=msg: self._sku_save_failed(
                            win, save_btn, cancel_btn, entry, m
                        ),
                    )
                    return
                except Exception as e:
                    msg = str(e)
                    self.root.after(
                        0,
                        lambda m=msg: self._sku_save_failed(
                            win, save_btn, cancel_btn, entry, m
                        ),
                    )
                    return
                self.root.after(
                    0, lambda: self._on_sku_updated(leaf_id, info, new_sku, win)
                )

            threading.Thread(target=worker, daemon=True).start()

        save_btn.configure(command=do_save)
        entry.bind("<Return>", lambda e: do_save())
        win.bind("<Escape>", lambda e: win.destroy())

        # Centrar sobre la ventana principal.
        win.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - win.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - win.winfo_height()) // 2
        win.geometry(f"+{x}+{y}")

        # En Wayland la ventana puede no estar "viewable" todavía cuando intentamos
        # hacer grab_set, lo que da TclError. Reintentamos hasta que esté visible.
        def _set_grab():
            try:
                win.grab_set()
            except tk.TclError:
                win.after(50, _set_grab)
        win.after(10, _set_grab)

    def _sku_save_failed(self, win, save_btn, cancel_btn, entry, msg: str):
        save_btn.configure(state="normal", text="Guardar")
        cancel_btn.configure(state="normal")
        entry.configure(state="normal")
        messagebox.showerror("Error", f"No se pudo actualizar SKU:\n{msg}", parent=win)

    def _on_sku_updated(self, leaf_id: str, info: dict, new_sku: str, win):
        info["sku"] = new_sku
        if leaf_id in self.tree.get_children(self.tree.parent(leaf_id)):
            values = list(self.tree.item(leaf_id, "values"))
            # values = (check, time, sku, title, price)
            values[2] = new_sku
            self.tree.item(leaf_id, values=values)
        win.destroy()
        self._flash_status(f"SKU actualizado: {new_sku}")

    def _put_item_sku(self, item_id: str, variation_id, new_sku: str):
        item_url = f"https://api.mercadolibre.com/items/{item_id}"

        # Decidir si el SKU vive en la variación o en el item.
        target_var = None
        vid = None
        if variation_id:
            try:
                vid = int(variation_id)
            except (TypeError, ValueError):
                vid = variation_id
            item_data = self._get_item(item_id)
            for v in item_data.get("variations") or []:
                if v.get("id") == vid:
                    target_var = v
                    break
            print(f"\n[SKU UPDATE] item={item_id} variation={vid}", flush=True)
            if target_var:
                print(f"  variation.seller_sku = {target_var.get('seller_sku')!r}", flush=True)
                print(f"  variation.attributes = {target_var.get('attributes')!r}", flush=True)
                print(f"  variation.attribute_combinations = {target_var.get('attribute_combinations')!r}", flush=True)
            print(f"  → setting to: {new_sku!r}", flush=True)

        # La variación tiene SKU propio sólo si su array attributes existe.
        # Si attributes es None, las variaciones se diferencian sólo por
        # combinations (color/talle) y el SKU vive a nivel item.
        use_variation_path = bool(target_var and target_var.get("attributes"))

        if use_variation_path:
            attrs = list(target_var.get("attributes") or [])
            updated = False
            for i, attr in enumerate(attrs):
                if (attr.get("id") or "").upper() == "SELLER_SKU":
                    attrs[i] = {"id": "SELLER_SKU", "value_name": new_sku}
                    updated = True
                    break
            if not updated:
                attrs.append({"id": "SELLER_SKU", "value_name": new_sku})

            try:
                result = self._do_put(
                    item_url,
                    {"variations": [{"id": vid, "attributes": attrs}]},
                )
            except HTTPError as e:
                body_text = self._read_err_body(e)
                if e.code == 400 and "item.pictures.max" in body_text:
                    msg = (
                        "El item tiene más de 12 fotos y la categoría ya no "
                        "permite ese límite. Reducí las fotos desde el panel "
                        "de Mercado Libre y volvé a intentar.\n\n" + body_text
                    )
                else:
                    msg = body_text
                raise HTTPError(
                    e.url, e.code, e.reason, e.headers,
                    io.BytesIO(msg.encode("utf-8")),
                )
        elif target_var is not None:
            # Item con variaciones pero la variación no tiene attributes propios.
            # No podemos usar item.attributes[SELLER_SKU] porque ML rechaza con
            # "item.attributes.invalid: Same attributes are used in item and
            # variations". El único camino que funciona es seller_custom_field.
            print(f"  → item con variaciones sin SKU per-variación → seller_custom_field", flush=True)
            result = self._do_put(item_url, {"seller_custom_field": new_sku})
        else:
            # Item plano sin variaciones: probar attributes primero; si falla
            # por fotos, fallback a seller_custom_field.
            print(f"  → item plano, intentando attributes[SELLER_SKU]", flush=True)
            try:
                result = self._do_put(
                    item_url,
                    {"attributes": [{"id": "SELLER_SKU", "value_name": new_sku}]},
                )
            except HTTPError as e:
                body_text = self._read_err_body(e)
                if e.code == 400 and (
                    "item.pictures.max" in body_text
                    or "item.attributes.invalid" in body_text
                ):
                    print(f"  → fallback a seller_custom_field", flush=True)
                    result = self._do_put(
                        item_url, {"seller_custom_field": new_sku}
                    )
                else:
                    raise HTTPError(
                        e.url, e.code, e.reason, e.headers,
                        io.BytesIO(body_text.encode("utf-8")),
                    )

        # Verificar mirando todos los lugares posibles.
        verify = self._get_item(item_id)
        if self._sku_present_anywhere(verify, vid, new_sku):
            actual = self._extract_sku_from_item(verify, vid)
            print(f"  ← after PUT, actual SKU = {actual!r} ✓", flush=True)
            return result

        actual = self._extract_sku_from_item(verify, vid)
        print(f"  ← after PUT, actual SKU = {actual!r} ✗", flush=True)
        raise RuntimeError(
            f"ML aceptó el PUT (200 OK) pero NO aplicó el cambio.\n\n"
            f"SKU que intentamos setear: {new_sku!r}\n"
            f"SKU actual en ML: {actual!r}"
        )

    def _sku_present_anywhere(self, item_data: dict, variation_id, expected: str) -> bool:
        """True si `expected` aparece en cualquier campo plausible de SKU."""
        if not expected:
            return False
        if variation_id:
            try:
                vid = int(variation_id)
            except (TypeError, ValueError):
                vid = variation_id
            for v in item_data.get("variations") or []:
                if v.get("id") == vid:
                    if v.get("seller_sku") == expected:
                        return True
                    for attr in v.get("attributes") or []:
                        if (attr.get("id") or "").upper() == "SELLER_SKU" and attr.get("value_name") == expected:
                            return True
                    break
        if item_data.get("seller_custom_field") == expected:
            return True
        if item_data.get("seller_sku") == expected:
            return True
        for attr in item_data.get("attributes") or []:
            if (attr.get("id") or "").upper() == "SELLER_SKU" and attr.get("value_name") == expected:
                return True
        return False

    def _get_item(self, item_id: str) -> dict:
        url = f"https://api.mercadolibre.com/items/{item_id}"
        req = Request(
            url,
            headers={"Authorization": f"Bearer {self.auth.access_token}"},
        )
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _read_err_body(self, e: HTTPError) -> str:
        try:
            return e.read().decode("utf-8", errors="replace")
        except Exception:
            return ""

    def _do_put(self, url: str, body: dict):
        data = json.dumps(body).encode("utf-8")
        req = Request(
            url,
            data=data,
            method="PUT",
            headers={
                "Authorization": f"Bearer {self.auth.access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _refresh_clicked_row(self):
        row = self._right_clicked_row
        if not row or self.tree.parent(row) == "":
            return
        # Recargamos también el cache de productos por si el usuario editó
        # etiquetas en gestor-productos.
        self._cargar_productos_async()
        self._refresh_leaf(row)

    def _refresh_leaf(self, leaf_id: str):
        info = self.leaf_to_item.get(leaf_id)
        if not info or not info.get("item_id"):
            return
        item_id = info["item_id"]
        variation_id = info.get("variation_id")
        self._flash_status(f"Refrescando {item_id}...", ms=10000)

        def worker():
            try:
                url = f"https://api.mercadolibre.com/items/{item_id}"
                req = Request(
                    url,
                    headers={"Authorization": f"Bearer {self.auth.access_token}"},
                )
                with urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except HTTPError as e:
                msg = f"Error refrescando: HTTP {e.code}"
                self.root.after(0, lambda m=msg: self._flash_status(m))
                return
            except Exception as e:
                msg = f"Error refrescando: {e}"
                self.root.after(0, lambda m=msg: self._flash_status(m))
                return

            new_sku = self._extract_sku_from_item(data, variation_id)
            print(
                f"\n[REFRESH FILA] item={item_id} variation={variation_id}",
                flush=True,
            )
            print(
                f"  item.seller_custom_field = {data.get('seller_custom_field')!r}",
                flush=True,
            )
            print(
                f"  item.seller_sku = {data.get('seller_sku')!r}",
                flush=True,
            )
            attr_sku = next(
                (
                    a.get("value_name")
                    for a in data.get("attributes") or []
                    if (a.get("id") or "").upper() == "SELLER_SKU"
                ),
                None,
            )
            print(f"  item.attributes[SELLER_SKU] = {attr_sku!r}", flush=True)
            print(f"  has variations = {bool(data.get('variations'))}", flush=True)
            print(f"  → extract returned: {new_sku!r}", flush=True)
            self.root.after(0, lambda: self._on_leaf_refreshed(leaf_id, new_sku))

        threading.Thread(target=worker, daemon=True).start()

    def _extract_sku_from_item(self, item_data: dict, variation_id) -> str:
        has_variations = bool(item_data.get("variations"))

        # 1) Variación con sus propios atributos: ese es el SKU canónico.
        if variation_id:
            try:
                vid = int(variation_id)
            except (TypeError, ValueError):
                vid = variation_id
            for v in item_data.get("variations") or []:
                if v.get("id") == vid:
                    for attr in v.get("attributes") or []:
                        if (attr.get("id") or "").upper() == "SELLER_SKU":
                            val = attr.get("value_name")
                            if val:
                                return str(val)
                    sku = v.get("seller_sku")
                    if sku:
                        return str(sku)
                    break

        # 2) Item-level. La prioridad depende de si el item tiene variaciones:
        #    - Items con variaciones: ML solo deja escribir seller_custom_field,
        #      así que ese es el campo "vivo". El attribute puede quedar viejo.
        #    - Items planos: ML usa attributes[SELLER_SKU] como canónico, y
        #      seller_custom_field puede quedar viejo si lo escribieron antes.
        if has_variations:
            sku = item_data.get("seller_custom_field")
            if sku:
                return str(sku)
            for attr in item_data.get("attributes") or []:
                if (attr.get("id") or "").upper() == "SELLER_SKU":
                    val = attr.get("value_name")
                    if val:
                        return str(val)
        else:
            for attr in item_data.get("attributes") or []:
                if (attr.get("id") or "").upper() == "SELLER_SKU":
                    val = attr.get("value_name")
                    if val:
                        return str(val)
            sku = item_data.get("seller_custom_field")
            if sku:
                return str(sku)

        sku = item_data.get("seller_sku")
        if sku:
            return str(sku)
        return ""

    def _on_leaf_refreshed(self, leaf_id: str, new_sku: str):
        info = self.leaf_to_item.get(leaf_id)
        if info is not None:
            info["sku"] = new_sku
        if leaf_id in self.tree.get_children(self.tree.parent(leaf_id)):
            values = list(self.tree.item(leaf_id, "values"))
            values[2] = new_sku
            self.tree.item(leaf_id, values=values)
        self._flash_status(f"SKU actual: {new_sku or '(vacío)'}")

    def _copy_clicked_title(self):
        row = self._right_clicked_row
        if not row or self.tree.parent(row) == "":
            return
        values = self.tree.item(row, "values")
        # values = (check, time, sku, cant, producto, precio, subtotal)
        if len(values) < 5:
            return
        title = values[4]
        self._set_clipboard(title)
        self._flash_status("Título copiado ✓")

    def _open_url(self, url: str):
        """Abre una URL. En WSL usa cmd.exe /c start (browser default de
        Windows, típicamente Chrome); en Linux nativo usa webbrowser.open
        (que respeta xdg-open / BROWSER, típicamente Brave/Firefox)."""
        if _IS_WSL:
            try:
                import subprocess
                # cmd.exe /c start "" <url> — comillas vacías son el title
                subprocess.run(
                    ["cmd.exe", "/c", "start", "", url],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return
            except (OSError, subprocess.CalledProcessError):
                pass  # fallback a webbrowser
        webbrowser.open(url)

    def _set_clipboard(self, text: str):
        """Copia al clipboard. En WSL usa clip.exe (clipboard de Windows);
        en Linux nativo usa el clipboard de Tk."""
        if _IS_WSL:
            try:
                import subprocess
                subprocess.run(
                    ["clip.exe"],
                    input=text.encode("utf-16le"),
                    check=True,
                )
                return
            except (OSError, subprocess.CalledProcessError):
                pass  # fallback al clipboard de Tk
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()

    def _copy_clicked_item_id(self):
        row = self._right_clicked_row
        if not row or self.tree.parent(row) == "":
            return
        info = self.leaf_to_item.get(row) or {}
        item_id = info.get("item_id") or ""
        if not item_id:
            self._flash_status("Esta venta no tiene ID de publicación")
            return
        self._set_clipboard(item_id)
        self._flash_status("ID publicación copiado ✓")

    def _copy_clicked_sku(self):
        row = self._right_clicked_row
        if not row or self.tree.parent(row) == "":
            return
        values = self.tree.item(row, "values")
        if len(values) < 3:
            return
        sku = values[2] or ""
        if not sku:
            self._flash_status("Esta venta no tiene SKU")
            return
        self._set_clipboard(sku)
        self._flash_status("SKU copiado ✓")

    def _collect_selected(self):
        """Devuelve (ordered_days, days_dict, grand_total, count). Lee del Treeview."""
        days: dict = {}
        parent_to_day = {v: k for k, v in self.day_nodes.items()}
        grand_total = 0.0
        for parent_id in self.tree.get_children(""):
            day_key = parent_to_day.get(parent_id, "")
            for leaf_id in self.tree.get_children(parent_id):
                order_id = self.row_to_order.get(leaf_id)
                if order_id not in self.selected_ids:
                    continue
                values = self.tree.item(leaf_id, "values")
                _, time_str, sku, qty, title, price_str, subtotal_str = values
                info = self.leaf_to_item.get(leaf_id) or {}
                quantity = int(info.get("quantity") or qty or 1)
                unit_price = float(info.get("unit_price") or 0.0)
                line_total = float(info.get("line_total") or 0.0)
                grand_total += line_total
                days.setdefault(day_key, []).append(
                    (time_str, sku, quantity, title, unit_price, line_total)
                )

        def _key(d):
            try:
                return datetime.strptime(d, "%d/%m/%Y")
            except ValueError:
                return datetime.min

        ordered = sorted(days.keys(), key=_key, reverse=True)
        count = sum(len(days[d]) for d in ordered)
        return ordered, days, grand_total, count

    def _copy_selected_to_clipboard(self):
        if not self.selected_ids:
            self._flash_status("No hay ventas seleccionadas")
            return
        ordered, days, grand_total, total_count = self._collect_selected()
        if not days:
            self._flash_status("Las ventas seleccionadas no están cargadas")
            return

        lines = [f"*Ventas seleccionadas* ({total_count})", ""]
        for d in ordered:
            lines.append(f"📅 *{d}*")
            for time_str, sku, quantity, title, unit_price, line_total in days[d]:
                sku_part = f" [{sku}]" if sku else ""
                if quantity and quantity != 1:
                    price_part = f"{quantity} x {format_price(unit_price)} = {format_price(line_total)}"
                else:
                    price_part = format_price(unit_price)
                lines.append(f"• {time_str} — {title}{sku_part} — {price_part}")
            lines.append("")
        lines.append(f"*Total: {format_price(grand_total)}*")
        text = "\n".join(lines)

        self._set_clipboard(text)
        self._flash_status(f"Copiado al portapapeles ✓ ({total_count} ventas)")

    def export_excel(self):
        if not self.selected_ids:
            self._flash_status("No hay ventas seleccionadas")
            return
        ordered, days, grand_total, count = self._collect_selected()
        if not days:
            self._flash_status("Las ventas seleccionadas no están cargadas")
            return

        default_name = f"ventas_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        path = filedialog.asksaveasfilename(
            title="Exportar a Excel",
            defaultextension=".xlsx",
            initialfile=default_name,
            filetypes=[("Excel", "*.xlsx")],
        )
        if not path:
            return

        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
            from openpyxl.utils import get_column_letter
        except ImportError:
            messagebox.showerror(
                "Error", "Falta el módulo openpyxl. Instalalo con:\n\npip install openpyxl"
            )
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "Ventas"

        thin = Side(border_style="thin", color="888888")
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        header_fill = PatternFill("solid", fgColor="DCE6F0")
        day_fill = PatternFill("solid", fgColor="F0F4F8")
        base_font = Font(size=14)
        bold = Font(bold=True, size=15)
        center = Alignment(horizontal="center", vertical="center", indent=1)
        left = Alignment(horizontal="left", vertical="center", wrap_text=True, indent=1)
        right = Alignment(horizontal="right", vertical="center", indent=1)

        headers = ["Fecha", "Hora", "SKU", "Cant", "Producto", "Precio U.", "Subtotal", "Notas"]
        ws.append(headers)
        for col_idx, _ in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = bold
            cell.alignment = center
            cell.fill = header_fill
            cell.border = border

        ncols = len(headers)
        row_idx = 2
        for d in ordered:
            for time_str, sku, quantity, title, unit_price, line_total in days[d]:
                ws.cell(row=row_idx, column=1, value=d).alignment = center
                ws.cell(row=row_idx, column=2, value=time_str).alignment = center
                ws.cell(row=row_idx, column=3, value=sku).alignment = center
                ws.cell(row=row_idx, column=4, value=quantity).alignment = center
                ws.cell(row=row_idx, column=5, value=title).alignment = left
                price_cell = ws.cell(row=row_idx, column=6, value=unit_price)
                price_cell.number_format = '"$"#,##0.00'
                price_cell.alignment = right
                subtotal_cell = ws.cell(row=row_idx, column=7, value=line_total)
                subtotal_cell.number_format = '"$"#,##0.00'
                subtotal_cell.alignment = right
                ws.cell(row=row_idx, column=8, value="")  # campo notas vacío
                for c in range(1, ncols + 1):
                    cell = ws.cell(row=row_idx, column=c)
                    cell.border = border
                    cell.font = base_font
                ws.row_dimensions[row_idx].height = 48  # más alto para escribir notas
                row_idx += 1

        # Fila de total
        ws.cell(row=row_idx, column=1, value="TOTAL").font = bold
        ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=6)
        merged = ws.cell(row=row_idx, column=1)
        merged.alignment = right
        total_cell = ws.cell(row=row_idx, column=7, value=grand_total)
        total_cell.font = bold
        total_cell.number_format = '"$"#,##0.00'
        total_cell.alignment = right
        ws.cell(row=row_idx, column=8, value="")
        for c in range(1, ncols + 1):
            ws.cell(row=row_idx, column=c).border = border
            ws.cell(row=row_idx, column=c).fill = day_fill

        # Anchos de columna (más amplios para fuente más grande + padding)
        widths = {1: 16, 2: 11, 3: 18, 4: 9, 5: 55, 6: 16, 7: 18, 8: 32}
        for col, w in widths.items():
            ws.column_dimensions[get_column_letter(col)].width = w

        ws.row_dimensions[1].height = 30

        # Print setup: horizontal, ajustar a 1 página de ancho
        ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_options.horizontalCentered = True
        ws.page_margins.left = 0.4
        ws.page_margins.right = 0.4
        ws.page_margins.top = 0.5
        ws.page_margins.bottom = 0.5

        try:
            wb.save(path)
        except OSError as e:
            messagebox.showerror("Error", f"No se pudo guardar:\n{e}")
            return

        self._flash_status(f"Exportado: {os.path.basename(path)} ({count} ventas)")

    def _show_hint(self, modifier: str):
        if self._hint_active == modifier:
            return  # auto-repeat de KeyPress, ignorar
        if self._hint_active is None:
            self._status_before_hint = self.status_var.get()
        self._hint_active = modifier
        if modifier == "ctrl":
            self.status_var.set("⌨ Ctrl + click → abrir publicación en el browser")
        else:
            self.status_var.set("⌨ Shift + click → abrir detalle de la venta")

    def _hide_hint(self, modifier: str):
        if self._hint_active != modifier:
            return
        self._hint_active = None
        # Restaurar el status previo (o recalcular si era de selección).
        if self._status_before_hint:
            self.status_var.set(self._status_before_hint)
        else:
            self._update_status()

    def _flash_status(self, msg: str, ms: int = 2500):
        prev = self.status_var.get()
        self.status_var.set(msg)
        self.root.after(ms, lambda: self.status_var.set(prev) if self.status_var.get() == msg else None)

    def _on_click(self, event):
        # Si vienen modificadores Ctrl/Shift, los manejan los otros bindings.
        if event.state & 0x0004 or event.state & 0x0001:
            return
        # Cerrar el menú contextual si quedó abierto.
        try:
            self.context_menu.unpost()
        except tk.TclError:
            pass
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if not row or col != "#1":
            return
        if self.tree.parent(row) == "":
            # Click en la columna check de un día → toggle todo el día.
            self._toggle_day(row)
        else:
            self._toggle(row)

    def refresh(self):
        if self.loading:
            return
        self.tree.delete(*self.tree.get_children())
        self.row_to_order.clear()
        self.row_base.clear()
        self.leaf_to_item.clear()
        self.day_nodes.clear()
        self.day_count.clear()
        self.day_total.clear()
        self.offset = 0
        self.selected_ids = load_selections()
        self._cargar_productos_async()
        self._fetch_async(append=False)

    def load_more(self):
        if self.loading:
            return
        self.selected_ids = load_selections()
        # Re-aplicar marcas a las filas ya cargadas por si cambió el JSON externamente.
        for leaf_id, order_id in self.row_to_order.items():
            mark = CHECKED if order_id in self.selected_ids else UNCHECKED
            values = list(self.tree.item(leaf_id, "values"))
            values[0] = mark
            self.tree.item(leaf_id, values=values, tags=self._row_tags(leaf_id, order_id))
        for parent in self.tree.get_children(""):
            self._update_day_check(parent)
        self._fetch_async(append=True)

    def _fetch_async(self, append: bool):
        self._set_loading(True)
        offset = self.offset

        def worker():
            try:
                data = fetch_orders(self.auth, offset=offset, limit=PAGE_SIZE)
            except HTTPError as e:
                self.root.after(0, self._on_error, f"HTTP {e.code}: {e.reason}")
                return
            except URLError as e:
                self.root.after(0, self._on_error, f"Error de red: {e.reason}")
                return
            except Exception as e:
                self.root.after(0, self._on_error, str(e))
                return
            self.root.after(0, self._on_data, data, append)

        threading.Thread(target=worker, daemon=True).start()

    def _on_error(self, msg: str):
        self._set_loading(False)
        messagebox.showerror("Error", msg)

    def _on_data(self, data: dict, append: bool):
        results = data.get("results", []) or []
        paging = data.get("paging", {}) or {}
        self.total = paging.get("total", len(results))

        start_idx = len(self.row_to_order)
        touched_days = set()
        for i, order in enumerate(results):
            items = order.get("order_items") or []
            first = items[0] if items else {}
            item = first.get("item") or {}
            title = item.get("title", "(sin título)")
            sku = extract_sku(first)
            unit_price = first.get("unit_price")
            try:
                quantity = int(first.get("quantity") or 1)
            except (TypeError, ValueError):
                quantity = 1
            try:
                unit_price_num = float(unit_price or 0)
            except (TypeError, ValueError):
                unit_price_num = 0.0
            line_total = unit_price_num * quantity
            price_str = format_price(unit_price)
            subtotal_str = format_price(line_total)
            dt = parse_iso(order.get("date_created", ""))
            day_key = format_day(dt)
            time_str = format_time(dt)
            order_id = str(order.get("id", ""))

            parent_id = self._get_or_create_day(day_key)
            mark = CHECKED if order_id in self.selected_ids else UNCHECKED
            base = "odd" if (start_idx + i) % 2 else "even"
            tags = ("selected",) if order_id in self.selected_ids else (base,)
            leaf_id = self.tree.insert(
                parent_id,
                "end",
                values=(mark, time_str, sku, quantity, title, price_str, subtotal_str),
                tags=tags,
            )
            self.row_to_order[leaf_id] = order_id
            self.row_base[leaf_id] = base
            # Datos de pago / comisiones para el panel de detalle
            payments = order.get("payments") or []
            first_payment = payments[0] if payments else {}
            try:
                sale_fee_unit = float(first.get("sale_fee") or 0)
            except (TypeError, ValueError):
                sale_fee_unit = 0.0
            sale_fee_total = sale_fee_unit * quantity
            try:
                shipping_cost_num = float(order.get("shipping_cost") or 0)
            except (TypeError, ValueError):
                shipping_cost_num = 0.0
            taxes = order.get("taxes") or {}
            try:
                taxes_amount = float(taxes.get("amount") or 0)
            except (TypeError, ValueError):
                taxes_amount = 0.0
            try:
                coupon_amount = float(first_payment.get("coupon_amount") or 0)
            except (TypeError, ValueError):
                coupon_amount = 0.0
            try:
                total_amount = float(order.get("total_amount") or line_total)
            except (TypeError, ValueError):
                total_amount = line_total
            neto = total_amount - sale_fee_total - shipping_cost_num - taxes_amount - coupon_amount

            self.leaf_to_item[leaf_id] = {
                "item_id": item.get("id", ""),
                "variation_id": item.get("variation_id"),
                "sku": sku,
                "title": title,
                "quantity": quantity,
                "unit_price": unit_price_num,
                "line_total": line_total,
                "payment_id": first_payment.get("id"),
                "payment_method": first_payment.get("payment_method_id"),
                "total_amount": total_amount,
                "sale_fee": sale_fee_total,
                "shipping_cost": shipping_cost_num,
                "taxes_amount": taxes_amount,
                "coupon_amount": coupon_amount,
                "neto": neto,
            }

            self.day_count[day_key] = self.day_count.get(day_key, 0) + 1
            self.day_total[day_key] = self.day_total.get(day_key, 0.0) + line_total
            touched_days.add(day_key)

        for day_key in touched_days:
            self._refresh_day_header(day_key)

        self.offset += len(results)
        self._update_header_check()
        self._set_loading(False)

        # Disparar refresco de SKUs en background: el endpoint /orders devuelve
        # snapshot histórico del item, así que el SKU puede estar desactualizado.
        new_leaf_ids = list(self.row_to_order.keys())[start_idx:]
        if new_leaf_ids:
            threading.Thread(
                target=self._refresh_skus_batch,
                args=(new_leaf_ids,),
                daemon=True,
            ).start()

    def _refresh_skus_batch(self, leaf_ids: list):
        # Agrupar leaves por item_id (varias órdenes pueden compartir item).
        item_to_leaves: dict = {}
        for lid in leaf_ids:
            info = self.leaf_to_item.get(lid) or {}
            iid = info.get("item_id")
            if iid:
                item_to_leaves.setdefault(iid, []).append(lid)

        if not item_to_leaves:
            return

        unique_ids = list(item_to_leaves.keys())
        BATCH = 20
        item_data: dict = {}
        for i in range(0, len(unique_ids), BATCH):
            chunk = unique_ids[i : i + BATCH]
            url = (
                "https://api.mercadolibre.com/items?"
                + urlencode({
                    "ids": ",".join(chunk),
                    "attributes": "id,seller_sku,seller_custom_field,attributes,variations",
                })
            )
            try:
                req = Request(
                    url,
                    headers={"Authorization": f"Bearer {self.auth.access_token}"},
                )
                with urlopen(req, timeout=30) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except Exception:
                continue
            # multiget devuelve [{"code":200,"body":{...}}, ...]
            for entry in payload or []:
                if entry.get("code") != 200:
                    continue
                body = entry.get("body") or {}
                iid = body.get("id")
                if iid:
                    item_data[iid] = body

        # Calcular SKUs nuevos por leaf y aplicarlos en el main thread.
        updates: list = []
        for iid, leaves in item_to_leaves.items():
            body = item_data.get(iid)
            if not body:
                continue
            for lid in leaves:
                info = self.leaf_to_item.get(lid) or {}
                new_sku = self._extract_sku_from_item(body, info.get("variation_id"))
                if new_sku and new_sku != info.get("sku"):
                    updates.append((lid, new_sku))

        if updates:
            self.root.after(0, lambda: self._apply_sku_updates(updates))

    def _apply_sku_updates(self, updates: list):
        for lid, new_sku in updates:
            info = self.leaf_to_item.get(lid)
            if info is not None:
                info["sku"] = new_sku
            try:
                parent = self.tree.parent(lid)
            except tk.TclError:
                continue
            if lid in self.tree.get_children(parent):
                values = list(self.tree.item(lid, "values"))
                values[2] = new_sku
                self.tree.item(lid, values=values)
        self._flash_status(f"SKUs sincronizados ({len(updates)})")

    def _get_or_create_day(self, day_key: str) -> str:
        if day_key in self.day_nodes:
            return self.day_nodes[day_key]
        parent_id = self.tree.insert(
            "",
            "end",
            text=day_key,
            values=(UNCHECKED, "", "", "", ""),
            tags=("day",),
            open=True,
        )
        self.day_nodes[day_key] = parent_id
        return parent_id

    def _refresh_day_header(self, day_key: str):
        parent_id = self.day_nodes.get(day_key)
        if not parent_id:
            return
        n = self.day_count.get(day_key, 0)
        total = self.day_total.get(day_key, 0.0)
        label = f"{day_key}  —  {n} venta{'s' if n != 1 else ''}  —  {format_price(total)}"
        self.tree.item(parent_id, text=label)
        self._update_day_check(parent_id)


def main():
    root = tk.Tk(className="ventas-ml")
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ventas-ml-icon.png")
    if os.path.exists(icon_path):
        icon_img = Image.open(icon_path)
        icon_sizes = []
        for size in (16, 32, 48, 64, 128, 256):
            resized = icon_img.resize((size, size), Image.LANCZOS)
            icon_sizes.append(ImageTk.PhotoImage(resized))
        root.tk.call("wm", "iconphoto", root._w, "-default", *icon_sizes)
        root._icon_refs = icon_sizes  # evitar GC
    VentasApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
