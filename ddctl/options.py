from __future__ import annotations

from typing import Annotated

import typer

from .i18n import t

# Reusable debug option for all leaf commands.
# Important: Do not log secrets; this flag only controls payload/response verbosity.
DebugOption = Annotated[
    bool,
    typer.Option(
        "--debug",
        help=t(
            "Habilita salida de depuración (sin secretos)",
            "Enable debug output (no secrets)",
        ),
    ),
]

