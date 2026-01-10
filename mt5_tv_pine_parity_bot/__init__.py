from __future__ import annotations

from pathlib import Path

__all__ = ["__version__"]
__version__ = "0.1.0"

# Allow running without installing by extending the package search path to /src.
_pkg_dir = Path(__file__).resolve().parent
_src_pkg = _pkg_dir.parent / "src" / "mt5_tv_pine_parity_bot"
if _src_pkg.is_dir():
    __path__.append(str(_src_pkg))
