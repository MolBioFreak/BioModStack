from __future__ import annotations

from collections.abc import Callable
from typing import Any


def build_toggle_row(
    adw_module: Any,
    gtk_module: Any,
    *,
    title: str,
    subtitle: str,
    active: bool,
    handler: Callable[..., Any],
):
    if hasattr(adw_module, "SwitchRow"):
        row = adw_module.SwitchRow()
        row.set_title(title)
        row.set_subtitle(subtitle)
        row.set_active(active)
        row.connect("notify::active", handler)
        return row, row

    row = adw_module.ActionRow()
    row.set_title(title)
    row.set_subtitle(subtitle)

    toggle = gtk_module.Switch()
    toggle.set_active(active)
    if hasattr(toggle, "set_valign") and hasattr(gtk_module, "Align"):
        toggle.set_valign(gtk_module.Align.CENTER)
    toggle.connect("notify::active", handler)

    row.add_suffix(toggle)
    if hasattr(row, "set_activatable_widget"):
        row.set_activatable_widget(toggle)
    return row, toggle
