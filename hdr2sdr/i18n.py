"""Tiny i18n: strings live in locales/<lang>.json; English is the base/fallback."""
from __future__ import annotations

import json
import os

LANGS = {"en": "English", "ru": "Русский", "de": "Deutsch", "es": "Español", "fr": "Français"}
DEFAULT = "en"
_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")

_current = DEFAULT
_base: dict[str, str] = {}
_strings: dict[str, str] = {}


def _load(lang: str) -> dict[str, str]:
    try:
        with open(os.path.join(_DIR, f"{lang}.json"), "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def set_language(lang: str) -> None:
    global _current, _base, _strings
    if not _base:
        _base = _load(DEFAULT)
    _current = lang if lang in LANGS else DEFAULT
    _strings = dict(_base)
    if _current != DEFAULT:
        _strings.update(_load(_current))


def get_language() -> str:
    return _current


def tr(key: str, **kw) -> str:
    if not _strings:
        set_language(_current)
    text = _strings.get(key) or key
    try:
        return text.format(**kw) if kw else text
    except (KeyError, IndexError, ValueError):
        return text
