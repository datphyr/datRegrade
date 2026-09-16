#!/usr/bin/env python3
"""Compose two 3D .cube LUTs into a single LUT.

Applying two LUTs one after the other means looking up the second at whatever
the first produced, and those values almost never land exactly on the second
LUT's grid. Everything in between has to be interpolated, which is why chaining
two LUTs has to be computed rather than concatenated -- and why the
interpolation scheme shows up in the result.

This composes with tetrahedral interpolation, the same scheme the generated
AviSynth scripts ask of ``DGCube(interp="tetrahedral")``. Matching the
renderer's scheme is the whole point: a LUT folded here should render
identically to the same two LUTs chained inside the script.

    python utils/cube.py compose FIRST SECOND -o OUT.cube

applies FIRST and then SECOND. The result keeps the finer of the two grids: the
coarser input is resampled up rather than the finer one being reduced, because
the match LUTs carry the detail, and shrinking them to a conversion LUT's grid
would hand back less than the pipeline asked for.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

__all__ = ["Cube", "CubeError", "compose", "read_cube", "write_cube"]

#: Decimal places used when writing LUT values.
#:
#: Six matches the rest of the pipeline -- both the bundled conversion LUTs and
#: datMatcher's generated match LUTs are written to six places -- so a composed
#: LUT is interchangeable with either. It is also far finer than the pipeline
#: can show: six places resolve to 1e-6, while the capture step renders at 10
#: bits, whose smallest step is roughly 1e-3, so the written precision is never
#: the limiting factor.
PRECISION = 6


class CubeError(ValueError):
    """Raised when a .cube file cannot be read, or a LUT cannot be composed."""


@dataclass
class Cube:
    """A 3D LUT, held as a function of an input colour.

    ``values`` is indexed ``[r, g, b]`` and holds the output colour produced at
    that grid point. That is the transpose of the file layout, which lists red
    fastest; :func:`read_cube` and :func:`write_cube` own that difference so
    nothing else has to think about it.
    """

    size: int
    values: np.ndarray
    title: str = ""

    def __post_init__(self) -> None:
        if self.size < 2:
            raise CubeError(f"LUT_3D_SIZE must be at least 2, got {self.size}")
        self.values = np.ascontiguousarray(self.values, dtype=np.float64)
        expected = (self.size, self.size, self.size, 3)
        if self.values.shape != expected:
            raise CubeError(f"expected a {expected} LUT, got shape {self.values.shape}")


def _numbers(fields: list[str], path: Path, lineno: int, line: str) -> list[float]:
    """Parse exactly three numbers from a data or domain line."""
    if len(fields) != 3:
        raise CubeError(f"{path}:{lineno}: expected 3 numbers, got {line!r}")
    try:
        return [float(field) for field in fields]
    except ValueError:
        raise CubeError(f"{path}:{lineno}: not a number: {line!r}") from None


def read_cube(path: str | Path) -> Cube:
    """Read a 3D .cube file.

    ``DOMAIN_MIN``/``DOMAIN_MAX`` are normalised away on read, so the returned
    LUT is always expressed over the full 0..1 input domain.
    """
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CubeError(f"cannot read {path}: {exc}") from exc

    title = ""
    size: int | None = None
    domain_min: np.ndarray | None = None
    domain_max: np.ndarray | None = None
    entries: list[list[float]] = []

    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.replace(",", " ").split()
        keyword = fields[0].upper()

        if keyword == "TITLE":
            title = line.split(None, 1)[1].strip().strip('"') if len(fields) > 1 else ""
        elif keyword == "LUT_1D_SIZE":
            raise CubeError(f"{path}:{lineno}: only 3D LUTs are supported")
        elif keyword == "LUT_3D_SIZE":
            try:
                size = int(fields[1])
            except (IndexError, ValueError):
                raise CubeError(f"{path}:{lineno}: bad LUT_3D_SIZE: {line!r}") from None
        elif keyword == "DOMAIN_MIN":
            domain_min = np.array(_numbers(fields[1:], path, lineno, line))
        elif keyword == "DOMAIN_MAX":
            domain_max = np.array(_numbers(fields[1:], path, lineno, line))
        else:
            entries.append(_numbers(fields, path, lineno, line))

    if size is None:
        raise CubeError(f"{path}: no LUT_3D_SIZE header")
    if len(entries) != size**3:
        raise CubeError(
            f"{path}: LUT_3D_SIZE {size} needs {size**3} entries, found {len(entries)}"
        )

    values = np.array(entries, dtype=np.float64)
    if not np.isfinite(values).all():
        raise CubeError(f"{path}: contains non-finite values")

    # File order runs red fastest, green next, blue last, so a plain reshape
    # comes out as [b, g, r]; transpose it into the [r, g, b] used everywhere
    # else here.
    values = values.reshape(size, size, size, 3).transpose(2, 1, 0, 3)

    if domain_min is not None or domain_max is not None:
        low = np.zeros(3) if domain_min is None else domain_min
        high = np.ones(3) if domain_max is None else domain_max
        span = np.where(high == low, 1.0, high - low)
        values = (values - low) / span

    return Cube(size=size, values=values, title=title)


def write_cube(path: str | Path, cube: Cube) -> None:
    """Write a LUT as a 3D .cube file over the standard 0..1 domain."""
    header = [f'TITLE "{cube.title}"' if cube.title else 'TITLE ""']
    header += ["DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1", f"LUT_3D_SIZE {cube.size}"]

    fmt = f"%.{PRECISION}f"
    # Back to the file's own order: blue slowest, red fastest.
    rows = cube.values.transpose(2, 1, 0, 3).reshape(-1, 3)
    body = "\n".join(" ".join(fmt % channel for channel in row) for row in rows)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(header) + "\n" + body + "\n", encoding="utf-8")


def _identity_grid(size: int) -> np.ndarray:
    """The grid of input colours a LUT of ``size`` is defined on."""
    axis = np.linspace(0.0, 1.0, size)
    r, g, b = np.meshgrid(axis, axis, axis, indexing="ij")
    return np.stack([r, g, b], axis=-1)


def _interpolate(values: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Sample ``values`` at ``points`` (shape ``(n, 3)``) tetrahedrally.

    Points are clamped into 0..1, so a colour outside the LUT's domain takes
    the nearest edge value instead of being extrapolated to something the
    inputs could never have produced.
    """
    size = values.shape[0]
    last = size - 1
    scaled = np.clip(points, 0.0, 1.0) * last

    # The cell each point falls in, and how far into it. Clamping the lower
    # corner to last-1 keeps a point sitting exactly on the far edge inside the
    # final cell, where its fraction becomes 1 and the weight lands wholly on
    # the upper corner -- which is the correct value for that edge.
    lower = np.clip(np.floor(scaled).astype(np.intp), 0, last - 1)
    frac = scaled - lower

    flat = values.reshape(-1, 3)
    rows = np.arange(points.shape[0])
    strides = np.array([size * size, size, 1])

    # Tetrahedral interpolation splits each cell into six tetrahedra along the
    # cube's main diagonal. Which one a point is in is decided by the order of
    # its three fractional parts, so the walk is: the lower corner, then a step
    # up the axis with the largest fraction, then up the middle one, then the
    # far corner. Weighting each stop by the gap between consecutive fractions
    # makes the weights sum to one, and an exact grid point falls entirely on
    # its own corner and is returned unchanged.
    order = np.argsort(frac, axis=1)[:, ::-1]
    f_high = frac[rows, order[:, 0]]
    f_mid = frac[rows, order[:, 1]]
    f_low = frac[rows, order[:, 2]]

    def corner_at(indices: np.ndarray) -> np.ndarray:
        return flat[indices @ strides]

    first = lower.copy()
    first[rows, order[:, 0]] += 1

    second = first.copy()
    second[rows, order[:, 1]] += 1

    return (
        (1.0 - f_high)[:, None] * corner_at(lower)
        + (f_high - f_mid)[:, None] * corner_at(first)
        + (f_mid - f_low)[:, None] * corner_at(second)
        + f_low[:, None] * corner_at(lower + 1)
    )


def _resample(values: np.ndarray, size: int) -> np.ndarray:
    """Express a LUT of any grid size on a ``size`` grid."""
    if values.shape[0] == size:
        return values
    return _interpolate(values, _identity_grid(size).reshape(-1, 3)).reshape(
        size, size, size, 3
    )


def compose(first: Cube, second: Cube) -> Cube:
    """Return the LUT that applies ``first`` and then ``second``.

    The result is defined on the finer of the two grids, so the composition is
    evaluated at as many points as the more detailed input provides.
    """
    size = max(first.size, second.size)
    start = _resample(first.values, size)
    finish = _resample(second.values, size)

    # ``start`` already holds one output colour per grid point, so it is
    # exactly the set of points at which the second LUT has to be sampled.
    composed = _interpolate(finish, start.reshape(-1, 3))

    title = f"{first.title or 'first'} then {second.title or 'second'}"
    return Cube(size=size, values=composed.reshape(size, size, size, 3), title=title)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cube.py",
        description="Compose two 3D .cube LUTs into a single LUT.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    compose_parser = commands.add_parser(
        "compose", help="apply FIRST and then SECOND, keeping the finer grid"
    )
    compose_parser.add_argument("first", help="the .cube applied first")
    compose_parser.add_argument("second", help="the .cube applied second")
    compose_parser.add_argument("-o", "--output", required=True, help="the .cube to write")

    args = parser.parse_args(argv)

    try:
        result = compose(read_cube(args.first), read_cube(args.second))
    except CubeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    write_cube(args.output, result)
    print(f"{args.output}: LUT_3D_SIZE {result.size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
