"""Backward-compatible theme entry point.

The canonical token/QSS implementation lives in :mod:`aura_rift.ui.theme` so
UI integrations can import it without reaching into the package root.  The
launcher historically imported ``aura_rift.theme.stylesheet``; re-exporting
the public API here keeps that code and third-party plug-ins working.
"""

from __future__ import annotations

from aura_rift.ui.theme import (
    DARK,
    DARK_THEME,
    DARK_QSS,
    DARK_TOKENS,
    LIGHT,
    LIGHT_THEME,
    LIGHT_QSS,
    LIGHT_TOKENS,
    THEMES,
    THEME_TOKENS,
    TOKENS,
    ThemeTokens,
    apply_theme,
    dark_stylesheet,
    get_tokens,
    get_theme_tokens,
    light_stylesheet,
    normalize_theme,
    palette,
    stylesheet,
    theme_tokens,
    tokens,
)

# Private names existed in the original module.  Keep them available for
# integrations that imported them despite the leading underscore.
_DARK = dark_stylesheet()
_LIGHT = light_stylesheet()

__all__ = [
    "DARK",
    "DARK_THEME",
    "LIGHT",
    "LIGHT_THEME",
    "DARK_TOKENS",
    "DARK_QSS",
    "LIGHT_TOKENS",
    "LIGHT_QSS",
    "THEMES",
    "THEME_TOKENS",
    "TOKENS",
    "ThemeTokens",
    "apply_theme",
    "dark_stylesheet",
    "get_tokens",
    "get_theme_tokens",
    "light_stylesheet",
    "normalize_theme",
    "palette",
    "stylesheet",
    "theme_tokens",
    "tokens",
]
