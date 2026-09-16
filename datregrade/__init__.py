"""datRegrade support package.

Currently this holds the 3D LUT toolkit that replaced the third-party
``LUTify``/``mixLUT`` helper scripts datRegrade used to shell out to.

See :mod:`datregrade.cube`. Names are resolved lazily so that running the
module directly (``python -m datregrade.cube``) does not import it twice.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .cube import Cube, CubeError

__all__ = [
    "Cube",
    "CubeError",
    "blend",
    "compose",
    "identity",
    "read_cube",
    "resample",
    "write_cube",
]


def __getattr__(name: str) -> Any:
    """Resolve ``datregrade.cube`` attributes on first access (PEP 562)."""
    if name in __all__:
        from . import cube

        return getattr(cube, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
