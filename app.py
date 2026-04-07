#!/usr/bin/env python3
"""Ventas PaliShopping - Lista ventas de MercadoLibre en vivo."""
import json
import os
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, font as tkfont
from tkinter import messagebox, ttk
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
        self.day_nodes: dict = {}  # "DD/MM/YYYY" -> parent row id
        self.day_count: dict = {}  # "DD/MM/YYYY" -> int
        self.day_total: dict = {}  # "DD/MM/YYYY" -> float

        self._build_ui()
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

        tree_frame = ttk.Frame(container)
        tree_frame.pack(fill="both", expand=True)

        columns = ("check", "fecha", "sku", "producto", "precio")
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
        self.tree.heading("producto", text="Producto")
        self.tree.heading("precio", text="Precio")
        self.tree.column("#0", width=200, anchor="w", stretch=False)
        self.tree.column("check", width=40, anchor="center", stretch=False)
        self.tree.column("fecha", width=60, anchor="w", stretch=False)
        self.tree.column("sku", width=90, anchor="w", stretch=False)
        self.tree.column("producto", width=380, anchor="w")
        self.tree.column("precio", width=110, anchor="e", stretch=False)
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Button-3>", self._on_right_click)

        self.context_menu = tk.Menu(self.tree, tearoff=0)
        self.context_menu.add_command(
            label="Copiar selección (WhatsApp)",
            command=self._copy_selected_to_clipboard,
        )
        self.context_menu.add_command(
            label="Copiar título", command=self._copy_clicked_title
        )
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
        # Habilitar/deshabilitar "Copiar título" según si hay una venta debajo del cursor.
        is_leaf = bool(row) and self.tree.parent(row) != ""
        self.context_menu.entryconfig(
            "Copiar título", state="normal" if is_leaf else "disabled"
        )
        self.context_menu.tk_popup(event.x_root, event.y_root)
        self.context_menu.focus_set()

    def _copy_clicked_title(self):
        row = self._right_clicked_row
        if not row or self.tree.parent(row) == "":
            return
        values = self.tree.item(row, "values")
        # values = (check, time, sku, title, price)
        if len(values) < 4:
            return
        title = values[3]
        self.root.clipboard_clear()
        self.root.clipboard_append(title)
        self.root.update()
        self._flash_status("Título copiado ✓")

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
                _, time_str, sku, title, price_str = values
                try:
                    n = float(price_str.replace("$", "").replace(".", "").replace(",", "."))
                except (ValueError, AttributeError):
                    n = 0.0
                grand_total += n
                days.setdefault(day_key, []).append((time_str, sku, title, price_str, n))

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
            for time_str, sku, title, price_str, _ in days[d]:
                sku_part = f" [{sku}]" if sku else ""
                lines.append(f"• {time_str} — {title}{sku_part} — {price_str}")
            lines.append("")
        lines.append(f"*Total: {format_price(grand_total)}*")
        text = "\n".join(lines)

        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()  # mantener en clipboard tras cerrar
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
        bold = Font(bold=True)
        center = Alignment(horizontal="center", vertical="center")
        left = Alignment(horizontal="left", vertical="center", wrap_text=True)
        right = Alignment(horizontal="right", vertical="center")

        headers = ["Fecha", "Hora", "SKU", "Producto", "Precio", "Notas"]
        ws.append(headers)
        for col_idx, _ in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=col_idx)
            cell.font = bold
            cell.alignment = center
            cell.fill = header_fill
            cell.border = border

        row_idx = 2
        for d in ordered:
            for time_str, sku, title, price_str, n in days[d]:
                ws.cell(row=row_idx, column=1, value=d).alignment = center
                ws.cell(row=row_idx, column=2, value=time_str).alignment = center
                ws.cell(row=row_idx, column=3, value=sku).alignment = center
                ws.cell(row=row_idx, column=4, value=title).alignment = left
                price_cell = ws.cell(row=row_idx, column=5, value=n)
                price_cell.number_format = '"$"#,##0.00'
                price_cell.alignment = right
                ws.cell(row=row_idx, column=6, value="")  # campo notas vacío
                for c in range(1, 7):
                    ws.cell(row=row_idx, column=c).border = border
                ws.row_dimensions[row_idx].height = 32  # más alto para escribir notas
                row_idx += 1

        # Fila de total
        ws.cell(row=row_idx, column=1, value="TOTAL").font = bold
        ws.merge_cells(start_row=row_idx, start_column=1, end_row=row_idx, end_column=4)
        merged = ws.cell(row=row_idx, column=1)
        merged.alignment = right
        total_cell = ws.cell(row=row_idx, column=5, value=grand_total)
        total_cell.font = bold
        total_cell.number_format = '"$"#,##0.00'
        total_cell.alignment = right
        ws.cell(row=row_idx, column=6, value="")
        for c in range(1, 7):
            ws.cell(row=row_idx, column=c).border = border
            ws.cell(row=row_idx, column=c).fill = day_fill

        # Anchos de columna
        widths = {1: 13, 2: 8, 3: 14, 4: 45, 5: 14, 6: 30}
        for col, w in widths.items():
            ws.column_dimensions[get_column_letter(col)].width = w

        ws.row_dimensions[1].height = 22

        # Print setup: vertical, ajustar a 1 página de ancho
        ws.page_setup.orientation = ws.ORIENTATION_PORTRAIT
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

    def _flash_status(self, msg: str, ms: int = 2500):
        prev = self.status_var.get()
        self.status_var.set(msg)
        self.root.after(ms, lambda: self.status_var.set(prev) if self.status_var.get() == msg else None)

    def _on_click(self, event):
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
        self.day_nodes.clear()
        self.day_count.clear()
        self.day_total.clear()
        self.offset = 0
        self.selected_ids = load_selections()
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
            price_str = format_price(unit_price)
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
                values=(mark, time_str, sku, title, price_str),
                tags=tags,
            )
            self.row_to_order[leaf_id] = order_id
            self.row_base[leaf_id] = base

            self.day_count[day_key] = self.day_count.get(day_key, 0) + 1
            try:
                self.day_total[day_key] = self.day_total.get(day_key, 0.0) + float(unit_price or 0)
            except (TypeError, ValueError):
                pass
            touched_days.add(day_key)

        for day_key in touched_days:
            self._refresh_day_header(day_key)

        self.offset += len(results)
        self._update_header_check()
        self._set_loading(False)

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
    root = tk.Tk()
    VentasApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
