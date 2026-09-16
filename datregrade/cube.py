#!/usr/bin/env python3
"""3D LUT (``.cube``) toolkit for datRegrade.

This module replaces the two third-party helper scripts datRegrade used to
shell out to:

* ``LUTify/LUTify.py`` -- michelerenzullo/LUTify, no license notice in the
  copy that was vendored here.
* ``mixLUT/mixLUT.py`` -- a small unreferenced local script.

datRegrade only ever used a narrow slice of LUTify::

    LUTify.py --preserve --input <a.cube> --combine <b.cube> --output <out.cube>

that is, "compose two 3D LUTs with tetrahedral interpolation, keeping the
larger of the two sizes". Everything else LUTify offers (HALD image <-> CUBE
conversion, arbitrary resizing, identity generation) is either unused here or
a few lines with numpy.

File format
-----------
A ``.cube`` file lists its entries with the red axis varying fastest, then
green, then blue::

    flat index = r + g*N + b*N*N        (N = LUT_3D_SIZE)

Internally this module stores a LUT as a ``(N, N, N, 3)`` float64 array
indexed ``[r, g, b]``, each entry a normalized ``(R, G, B)`` output triple.
That is the natural "function of an input colour" layout, and it is the
transpose of the raw file order.

Composition
-----------
``compose(a, b)`` returns the LUT equivalent to applying ``a`` first and ``b``
afterwards::

    compose(a, b)(rgb) = b(a(rgb))

which is what ``LUTify.py --combine`` does at its default ``--mixer 50``. The
other mixer values blend one operand toward identity before composing; that
behaviour is preserved here so existing datRegrade runs stay reproducible.

Command line
------------
The CLI mirrors LUTify's flag names so a caller can swap the program name and
keep the rest of its arguments::

    python -m datregrade.cube compose -i a.cube -c b.cube -o out.cube --preserve
    python -m datregrade.cube blend   -i a.cube -c b.cube -o out.cube --amount 0.5
    python -m datregrade.cube resize  -i a.cube -o out.cube --size 33
    python -m datregrade.cube info    -i a.cube
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

__all__ = [
    "Cube",
    "CubeError",
    "blend",
    "compose",
    "identity",
    "interpolate",
    "read_cube",
    "resample",
    "write_cube",
]

METHODS = ("tetrahedral", "nearest")

#: Decimal places used when writing ``.cube`` entries.
DEFAULT_PRECISION = 6

_TITLE_RE = re.compile(r'^TITLE\s+"?(?P<title>.*?)"?\s*$', re.IGNORECASE)
_SIZE3D_RE = re.compile(r"^LUT_3D_SIZE\s+(?P<size>\d+)\s*$", re.IGNORECASE)
_SIZE1D_RE = re.compile(r"^LUT_1D_SIZE\s+(?P<size>\d+)\s*$", re.IGNORECASE)
_NUMBER_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


class CubeError(ValueError):
    """Raised when a ``.cube`` file cannot be read or an operation is invalid."""


@dataclass
class Cube:
    """A 3D LUT.

    Attributes:
        size: ``LUT_3D_SIZE`` -- the number of grid points per axis.
        data: ``(size, size, size, 3)`` float64 array indexed ``[r, g, b]``
            holding normalized ``(R, G, B)`` output values.
        title: the ``TITLE`` header, if any.
        comments: comment lines preserved from the source file.
    """

    size: int
    data: np.ndarray
    title: str = ""
    comments: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.data = np.ascontiguousarray(self.data, dtype=np.float64)
        expected = (self.size, self.size, self.size, 3)
        if self.data.shape != expected:
            raise CubeError(f"expected LUT data of shape {expected}, got {self.data.shape}")


def _parse_triplet(line: str) -> list[float] | None:
    """Return three floats if ``line`` is a LUT data row, else ``None``."""
    parts = line.replace(",", " ").split()
    if len(parts) != 3 or not all(_NUMBER_RE.match(p) for p in parts):
        return None
    return [float(p) for p in parts]


def read_cube(path: str | Path) -> Cube:
    """Read a 3D ``.cube`` file.

    Values are normalized into ``0..1`` using ``DOMAIN_MIN``/``DOMAIN_MAX``
    when the file declares them, so callers never see the file's raw domain.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CubeError(f"cannot read {path}: {exc}") from exc

    size: int | None = None
    title = ""
    comments: list[str] = []
    domain_min: np.ndarray | None = None
    domain_max: np.ndarray | None = None
    values: list[list[float]] = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            comments.append(line)
            continue

        upper = line.upper()
        if upper.startswith("TITLE"):
            match = _TITLE_RE.match(line)
            if match:
                title = match.group("title")
            continue
        if upper.startswith("DOMAIN_MIN"):
            domain_min = np.array([float(x) for x in line.split()[1:4]], dtype=np.float64)
            continue
        if upper.startswith("DOMAIN_MAX"):
            domain_max = np.array([float(x) for x in line.split()[1:4]], dtype=np.float64)
            continue
        if _SIZE1D_RE.match(line):
            raise CubeError(
                f"{path}:{lineno}: 1D LUTs are not supported, only 3D (LUT_3D_SIZE)"
            )
        match = _SIZE3D_RE.match(line)
        if match:
            size = int(match.group("size"))
            continue

        triplet = _parse_triplet(line)
        if triplet is None:
            raise CubeError(f"{path}:{lineno}: cannot parse line: {line!r}")
        values.append(triplet)

    if size is None:
        raise CubeError(f"{path}: no LUT_3D_SIZE header found")
    if size < 2:
        raise CubeError(f"{path}: LUT_3D_SIZE must be at least 2, got {size}")

    data = np.asarray(values, dtype=np.float64)
    expected = size**3
    if data.shape[0] != expected:
        raise CubeError(
            f"{path}: LUT_3D_SIZE {size} needs {expected} entries, found {data.shape[0]}"
        )

    # File order is r fastest, so a plain reshape yields [b, g, r]; transpose
    # to the [r, g, b] layout used everywhere else in this module.
    data = data.reshape(size, size, size, 3).transpose(2, 1, 0, 3)

    if domain_min is not None or domain_max is not None:
        lo = np.zeros(3) if domain_min is None else domain_min
        hi = np.ones(3) if domain_max is None else domain_max
        span = np.where(hi - lo == 0, 1.0, hi - lo)
        data = (data - lo) / span

    return Cube(size=size, data=data, title=title, comments=comments)


def write_cube(path: str | Path, cube: Cube, precision: int = DEFAULT_PRECISION) -> None:
    """Write ``cube`` as a 3D ``.cube`` file in the standard ``0..1`` domain."""
    path = Path(path)
    header = []
    if cube.title:
        header.append(f'TITLE "{cube.title}"')
    header.extend(cube.comments)
    header.append("DOMAIN_MIN 0 0 0")
    header.append("DOMAIN_MAX 1 1 1")
    header.append(f"LUT_3D_SIZE {cube.size}")

    fmt = f"%.{precision}f"
    rows = cube.data.transpose(2, 1, 0, 3).reshape(-1, 3)
    body = "\n".join(" ".join(fmt % channel for channel in row) for row in rows)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(header) + "\n" + body + "\n", encoding="utf-8")


def identity(size: int) -> Cube:
    """Return the identity 3D LUT of ``size`` points per axis."""
    if size < 2:
        raise CubeError(f"LUT_3D_SIZE must be at least 2, got {size}")
    axis = np.arange(size, dtype=np.float64) / (size - 1)
    r, g, b = np.meshgrid(axis, axis, axis, indexing="ij")
    data = np.stack([r, g, b], axis=-1)
    return Cube(size=size, data=data, title="identity")


def interpolate(data: np.ndarray, coords: np.ndarray, method: str = "tetrahedral") -> np.ndarray:
    """Sample the LUT ``data`` at ``coords`` (``(3,)`` or ``(M, 3)`` in ``0..1``).

    ``coords`` is clamped to ``0..1``, so out-of-gamut inputs yield the nearest
    edge value rather than an extrapolation.
    """
    data = np.asarray(data, dtype=np.float64)
    coords = np.asarray(coords, dtype=np.float64)
    if data.ndim != 4 or data.shape[3] != 3 or data.shape[0] != data.shape[1] != data.shape[2]:
        raise CubeError(f"expected an (N, N, N, 3) LUT, got shape {data.shape}")
    if method not in METHODS:
        raise CubeError(f"unknown interpolation method {method!r}, expected one of {METHODS}")

    single = coords.ndim == 1
    if single:
        coords = coords[None, :]
    if coords.shape[1] != 3:
        raise CubeError(f"expected coordinates with 3 components, got shape {coords.shape}")

    size = data.shape[0]
    if size < 2:
        raise CubeError(f"LUT_3D_SIZE must be at least 2, got {size}")

    scaled = np.clip(coords, 0.0, 1.0) * (size - 1)
    flat = data.reshape(-1, 3)
    grid = (size, size, size)
    rows = np.arange(scaled.shape[0])

    if method == "nearest":
        # Round half *down*, matching LUTify's `array_resize`: it picks the
        # upper grid point only when the remaining fraction is strictly
        # greater than 0.5. numpy's rint rounds halves to even instead, which
        # differs on exactly-tied inputs (e.g. resampling size 3 -> 5).
        lower = np.floor(scaled)
        idx = np.where(scaled - lower > 0.5, lower + 1, lower).astype(np.int64)
        np.clip(idx, 0, size - 1, out=idx)
        result = flat[np.ravel_multi_index((idx[:, 0], idx[:, 1], idx[:, 2]), grid)]
    else:
        lower = np.clip(np.floor(scaled).astype(np.int64), 0, size - 2)
        upper = lower + 1
        frac = scaled - lower

        # Standard tetrahedral decomposition: sort the three fractional parts,
        # then walk the corners 000 -> +=axis(largest) -> +=axis(middle) -> 111,
        # weighting each vertex by the gap between successive fractions.
        order = np.argsort(frac, axis=1)
        sorted_frac = np.take_along_axis(frac, order, axis=1)
        f_low, f_mid, f_high = sorted_frac[:, 0], sorted_frac[:, 1], sorted_frac[:, 2]

        def sample(indices: np.ndarray) -> np.ndarray:
            return flat[
                np.ravel_multi_index(
                    (indices[:, 0], indices[:, 1], indices[:, 2]), grid
                )
            ]

        corner0 = sample(lower)

        # The tetrahedron's interior vertices advance along the axes with the
        # largest fraction first, then the middle one.
        step1 = lower.copy()
        step1[rows, order[:, 2]] = upper[rows, order[:, 2]]
        corner1 = sample(step1)

        step2 = step1.copy()
        step2[rows, order[:, 1]] = upper[rows, order[:, 1]]
        corner2 = sample(step2)

        corner3 = sample(upper)

        result = (
            (1.0 - f_high)[:, None] * corner0
            + (f_high - f_mid)[:, None] * corner1
            + (f_mid - f_low)[:, None] * corner2
            + f_low[:, None] * corner3
        )

    return result[0] if single else result


def resample(data: np.ndarray, new_size: int, method: str = "tetrahedral") -> np.ndarray:
    """Resample a LUT grid to ``new_size`` points per axis."""
    current = data.shape[0]
    if new_size < 2:
        raise CubeError(f"LUT_3D_SIZE must be at least 2, got {new_size}")
    if new_size == current:
        return np.array(data, dtype=np.float64, copy=True)

    axis = np.linspace(0.0, 1.0, new_size)
    r, g, b = np.meshgrid(axis, axis, axis, indexing="ij")
    coords = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    return interpolate(data, coords, method).reshape(new_size, new_size, new_size, 3)


def _blend_toward_identity(data: np.ndarray, alpha: float) -> np.ndarray:
    """``(1 - alpha) * data + alpha * identity`` for alpha in ``0..1``."""
    if alpha == 0.0:
        return data
    size = data.shape[0]
    axis = np.arange(size, dtype=np.float64) / (size - 1)
    r, g, b = np.meshgrid(axis, axis, axis, indexing="ij")
    ident = np.stack([r, g, b], axis=-1)
    return (1.0 - alpha) * data + alpha * ident


def _match_sizes(
    a: np.ndarray,
    b: np.ndarray,
    method: str,
    preserve: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Bring two LUT grids to a common size, mirroring LUTify's rules.

    ``preserve=True`` keeps the larger size; ``preserve=False`` keeps the
    smaller one.
    """
    size_a, size_b = a.shape[0], b.shape[0]
    if size_a == size_b:
        return a, b
    target = max(size_a, size_b) if preserve else min(size_a, size_b)
    if size_a != target:
        a = resample(a, target, method)
    if size_b != target:
        b = resample(b, target, method)
    return a, b


def compose(
    a: Cube,
    b: Cube,
    *,
    mixer: float = 50,
    method: str = "tetrahedral",
    preserve: bool = False,
    size: int | None = None,
) -> Cube:
    """Return the LUT that applies ``a`` and then ``b``.

    At ``mixer=50`` this is plain composition, ``b(a(rgb))``. Other values
    blend an operand toward identity first, matching ``LUTify.py --combine``:

    * ``mixer > 50`` blends ``a`` toward identity,
    * ``mixer < 50`` blends ``b`` toward identity.

    Args:
        a: the LUT applied first.
        b: the LUT applied second.
        mixer: 0..100, ``50`` means "compose; no blending".
        method: interpolation used when resampling to a common size.
        preserve: keep the larger of the two LUT sizes.
        size: resample the result to this ``LUT_3D_SIZE``.

    The composition itself always samples with tetrahedral interpolation.
    That matches ``LUTify.py``, whose ``--method`` flag reaches only the
    resizing helper and never the combine loop, and it avoids the large
    quantization error a nearest-neighbour resample of the composed grid
    would introduce. ``method`` therefore governs resampling alone.
    """
    if method not in METHODS:
        raise CubeError(f"unknown interpolation method {method!r}, expected one of {METHODS}")
    if not 0 <= mixer <= 100:
        raise CubeError(f"mixer must be between 0 and 100, got {mixer}")

    data_a, data_b = _match_sizes(a.data, b.data, method, preserve)

    # LUTify maps 0..100 onto -1..1, with 0 meaning "identity" and 1 meaning
    # "the LUT itself".
    alpha = (np.clip(mixer / 100.0, 0.0, 1.0) - 0.5) * 2.0
    if alpha > 0.0:
        data_a = _blend_toward_identity(data_a, alpha)
    elif alpha < 0.0:
        data_b = _blend_toward_identity(data_b, -alpha)

    composed = interpolate(data_b, data_a.reshape(-1, 3), "tetrahedral")
    composed = composed.reshape(data_a.shape)

    if size is not None and size != composed.shape[0]:
        composed = resample(composed, size, method)

    title = " and ".join(t for t in (a.title, b.title) if t)
    return Cube(
        size=composed.shape[0],
        data=composed,
        title=f"LUTs combined {title}" if title else "LUTs combined",
    )


def blend(a: Cube, b: Cube, amount: float = 0.5) -> Cube:
    """Linearly blend two equally sized LUTs: ``(1 - amount) * a + amount * b``.

    This reproduces the behaviour of the original ``mixLUT/mixLUT.py``.
    """
    if a.size != b.size:
        raise CubeError(f"LUT sizes must match to blend ({a.size} vs {b.size})")
    if not 0.0 <= amount <= 1.0:
        raise CubeError(f"amount must be between 0 and 1, got {amount}")
    data = (1.0 - amount) * a.data + amount * b.data
    title = " and ".join(t for t in (a.title, b.title) if t)
    return Cube(
        size=a.size,
        data=data,
        title=f"LUTs blended {title}" if title else "LUTs blended",
    )


def _load(path: str) -> Cube:
    return read_cube(path)


def _report(path: str | Path, cube: Cube) -> None:
    print(f"{path}: LUT_3D_SIZE {cube.size}, {cube.size**3} entries")
    if cube.title:
        print(f"  title: {cube.title}")
    print(f"  range: {cube.data.min():.6f} .. {cube.data.max():.6f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m datregrade.cube",
        description="Compose, blend, resample and inspect 3D .cube LUTs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_io(sub: argparse.ArgumentParser, *, second: bool) -> None:
        sub.add_argument("--input", "-i", required=True, help="input .cube file")
        if second:
            sub.add_argument("--combine", "-c", required=True, help="second .cube file")
        sub.add_argument("--output", "-o", required=True, help="output .cube file")
        sub.add_argument(
            "--method",
            "-m",
            choices=METHODS,
            default="tetrahedral",
            help="interpolation method (default: %(default)s)",
        )

    compose_parser = subparsers.add_parser(
        "compose", help="apply the input LUT and then the combine LUT (<combine> o <input>)"
    )
    add_io(compose_parser, second=True)
    compose_parser.add_argument(
        "--mixer", "-x", type=float, default=50,
        help="0..100; 50 composes with no blending (default: %(default)s)",
    )
    compose_parser.add_argument(
        "--preserve", "-p", action="store_true",
        help="keep the larger of the two LUT sizes (default: keep the smaller)",
    )
    compose_parser.add_argument(
        "--size", "-s", type=int, default=None,
        help="resample the result to this LUT_3D_SIZE",
    )

    blend_parser = subparsers.add_parser(
        "blend", help="linearly blend two equally sized LUTs"
    )
    add_io(blend_parser, second=True)
    blend_parser.add_argument(
        "--amount", type=float, default=0.5,
        help="0..1, how much of the combine LUT to mix in (default: %(default)s)",
    )

    resize_parser = subparsers.add_parser("resize", help="resample a LUT to a new size")
    add_io(resize_parser, second=False)
    resize_parser.add_argument("--size", "-s", type=int, required=True, help="target LUT_3D_SIZE")

    info_parser = subparsers.add_parser("info", help="print a summary of a LUT")
    info_parser.add_argument("--input", "-i", required=True, help="input .cube file")

    args = parser.parse_args(argv)

    try:
        if args.command == "info":
            _report(args.input, _load(args.input))
            return 0

        source = _load(args.input)

        if args.command == "compose":
            result = compose(
                source,
                _load(args.combine),
                mixer=args.mixer,
                method=args.method,
                preserve=args.preserve,
                size=args.size,
            )
        elif args.command == "blend":
            result = blend(source, _load(args.combine), args.amount)
        else:
            result = Cube(
                size=args.size,
                data=resample(source.data, args.size, args.method),
                title=source.title,
                comments=source.comments,
            )
    except CubeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    write_cube(args.output, result)
    _report(args.output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
