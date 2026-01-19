import os
from typing import Literal

Lang = Literal["en", "es"]


def get_lang() -> Lang:
    """
    Language selection for help texts. Controlled by env var DDOGCTL_LANG.
    Defaults to 'es'.
    """
    val = (os.environ.get("DDOGCTL_LANG") or "").lower()
    if val.startswith("en"):
        return "en"
    if val.startswith("es"):
        return "es"
    return "es"


def t(es: str, en: str) -> str:
    """
    Returns the string in the currently selected language.
    """
    return es if get_lang() == "es" else en

