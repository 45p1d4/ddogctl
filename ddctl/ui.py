from __future__ import annotations

from typing import Dict, Optional

from rich.table import Table
from rich import box


def _format_meta_pair(key: str, value: Optional[str]) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    k = f"[dim]{key}[/dim]"
    v = f"[bold]{value}[/bold]"
    return f"{k}={v}"


def build_title(base: str, metadata: Dict[str, Optional[str]] | None = None) -> str:
    """
    Compose a consistent table title: 'Base  • key=value  • key=value'
    Keys dimmed, values bold. Empty/None entries are skipped.
    """
    if not metadata:
        return base
    parts = []
    for k in ["service", "env", "cluster", "from", "to", "date"]:
        if k in metadata:
            pair = _format_meta_pair(k, metadata.get(k))
            if pair:
                parts.append(pair)
    if not parts:
        return base
    sep = "  [dim]•[/dim]  "
    return f"{base}{sep}" + sep.join(parts)


def new_table(base_title: str, metadata: Dict[str, Optional[str]] | None = None) -> Table:
    """
    Create a Rich Table with consistent styling and full terminal width.
    """
    title = build_title(base_title, metadata or {})
    table = Table(
        title=title,
        show_lines=False,
        expand=True,
        box=box.SIMPLE_HEAD,
        pad_edge=False,
        show_header=True,
    )
    return table

