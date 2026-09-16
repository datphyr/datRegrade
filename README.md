# datRegrade

Prepare a colour-regrade project that matches an HDR source video to an SDR reference.

`datRegrade` is the orchestration layer around [datMatcher](https://github.com/datphyr/datMatcher).
Given an HDR source and a reference (target) video, it generates a complete,
self-contained regrade project: the AviSynth scripts, the LUTs, and the
step-by-step command batches that produce them.

It **prepares** a project and prints the commands; it never runs them itself.
Inspect the generated scripts, run the steps you want, and compare the results.

## Contents

- [How it works](#how-it-works)
- [What gets generated](#what-gets-generated)
- [Requirements](#requirements)
- [Installing](#installing)
- [Usage](#usage)
- [Options](#options)
- [Matching methods](#matching-methods)
- [Tonemapping and LUTs](#tonemapping-and-luts)
- [The LUT toolkit](#the-lut-toolkit)
- [Design notes](#design-notes)
- [Project layout](#project-layout)
- [Licensing note](#licensing-note)

## How it works

Matching HDR to SDR is not a single conversion — it is a search over *how* you
make that conversion. Different tonemappers and different LUTs produce
noticeably different results, and which one looks right is a judgement call
that depends on the film.

So rather than pick one pipeline, `datRegrade` builds a whole matrix of them
and lets you compare:

1. **Index** — `DGIndexNV` indexes the source and target videos.
2. **Extract** — each variant is rendered into an AviSynth script and sampled
   by datMatcher's `extract_colors`, which writes a JSON report of the video's
   colour distribution.
3. **Match** — `match_colors` compares a source report against a target report
   and writes one `.cube` LUT per matching algorithm.
4. **Combine** — where a variant is itself a LUT (e.g. a PQ→BT709 transform),
   that LUT is composed with the match LUT into a single combined LUT.
5. **Capture** — frames are pulled at your chosen frame numbers through each
   pipeline, so you can look at the results side by side.

The variants come from three independent axes:

| Axis | Default | Meaning |
| --- | --- | --- |
| Source variants | 15 | `plain`, the 2 PQ→BT709 LUTs, and each of the 12 tonemappers |
| Target variants | 16 | The same, plus `hdr` (target left unconverted) |
| Matching methods | 6 | The colour-matching algorithms; see [Matching methods](#matching-methods) |

Those are all different answers to the same question — *how do you get from HDR
to SDR?* — and they are not interchangeable. See
[Tonemapping and LUTs](#tonemapping-and-luts).

At the defaults that works out to 211 `match_colors` runs and on the order of
16,000 capture pipelines, which is why the work is fanned out into per-step
command files rather than done in one go.

## What gets generated

Everything lands under `REGRADES/<output-dir>/`:

```
REGRADES/MyFilm/
├── 01_index.bat              # DGIndexNV indexing
├── 02_extract_source_*.bat   # colour extraction per source variant
├── 02_extract_target_*.bat   # colour extraction per target variant
├── 03_match_colors.bat       # datMatcher match_colors runs
├── 04_combine_luts.bat       # LUT composition
├── 05_capture_*.bat          # screenshot capture per pipeline
├── commands.bat              # everything above, in order
├── INDEX/                    # .dgi index files
├── SCRIPTS/                  # generated AviSynth (.avs) scripts
├── COLORS/                   # datMatcher colour reports (.json)
├── LUTS/                     # generated .cube LUTs
└── SCREENSHOTS/              # captured frames
```

On Windows the steps are `.bat`, elsewhere `.sh`. Each step file changes into
its own directory before running and references the tools relative to that
directory, so a `REGRADES/MyFilm/` project stays valid if the repository moves
— which matters when a run takes hours and the paths are long. Commands are
recorded as absolute paths and relativized at generation time; tools that
cannot be made relative to the project directory (datMatcher on another drive,
or `ffmpeg` found on `PATH`) are left untouched.

## Requirements

To **prepare** a project:

- Python 3.9+
- `jinja2` and `numpy` (see [Installing](#installing))

To **run** the generated project you additionally need, outside of pip:

- **[datMatcher](https://github.com/datphyr/datMatcher)** — provides
  `extract_colors` and `match_colors`. Not vendored here; see below.
- **ffmpeg** — for frame capture.
- **DGIndexNV** — for video indexing.
- **AviSynth+** with `DGDecodeNV` and a `libplacebo` tonemapping `Tonemap`
  function, since the generated `.avs` scripts depend on both.

### Getting datMatcher

datMatcher is a separate project and its binaries are not stored here — they
are large and change independently. Build or download it (see
[datMatcher's README](https://github.com/datphyr/datMatcher)) and drop the
result into `utils/datMatcher/`. That is the whole setup; datRegrade finds the
executables on its own.

```
utils/datMatcher/
├── extract_colors.exe
└── match_colors.exe
```

`extract_colors` links FFmpeg, so its binaries are big and are rebuilt on
datMatcher's own schedule. An earlier revision of datRegrade kept several
copies of them under `UTILS/datMatcher/` — including `v1`, `v2`, `v3` snapshots —
which is how that directory passed 500 MB and why the repository shipped stale
builds of a project that had moved on. Keeping them out is the reason a fresh
clone is about 2 MB instead.

Dropping in a whole **datMatcher checkout** works too — the executables are
looked for a few directories deep, so a `build/` or `build/Release/` layout is
found automatically. Loose executables directly in `utils/` also work.

To keep datMatcher somewhere else entirely, either of these takes precedence:

1. The `--datmatcher-dir` option.
2. The `DATMATCHER_DIR` environment variable.

The legacy uppercase `UTILS/datMatcher/` is still searched, for older
checkouts. `extract_colors` and `match_colors` are looked up with and without a
`.exe` suffix, so the same layout works on either platform. If nothing is
found, datRegrade says so and names every directory it searched, rather than
failing later with a confusing error.

## Installing

Only the preparation step needs a Python environment:

```sh
python -m pip install -r requirements.txt
```

That is all — there is nothing to build. `auto_regrade.py` is run from the
repository root and `utils/cube.py` is invoked by path, so neither needs to be
installed. The dependencies are also declared in `pyproject.toml` for editors
and tooling.

## Usage

```sh
python auto_regrade.py \
  --source "SRC/TERMINATOR [40TH ANNIVERSARY REMASTER] (1984).mkv" \
  --target "SRC/Terminator.1984.BDRemux.1080p.mpeg2.mkv" \
  --source-crop "0,42,0,-42" \
  --target-crop "0,20,-2,-22" \
  --source-trim "5727,148502" \
  --target-trim "5535,148310" \
  --frames "1340,11337,14451,16571,16906" \
  --output-dir "T1"
```

This writes the project under `REGRADES/T1/` and prints every command it
recorded. Then run the steps in order, e.g. on Windows:

```sh
cd REGRADES/T1
01_index.bat
02_extract_source.bat
03_match_colors.bat
04_combine_luts.bat
05_capture_regrade.bat
```

or all of them at once via `commands.bat`.

The full default matrix is a lot of work. To iterate quickly, narrow it:

```sh
python auto_regrade.py \
  --source "SRC/hdr.mkv" --target "SRC/ref.mkv" \
  --frames "1340,11337" --output-dir "quick" \
  --tonemapping "bt2390,spline" \
  --methods "rgb-1d,rgb-3d-emd"
```

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--source` | *required* | Path to the source HDR video |
| `--target` | *required* | Path to the target SDR reference |
| `--frames` | *required* | Frame numbers to capture, comma-separated or a file with one per line |
| `--source-crop` | `0,0,0,0` | Source crop as `left,top,right,bottom` |
| `--target-crop` | `0,0,0,0` | Target crop as `left,top,right,bottom` |
| `--source-trim` | `10000,100000` | Source trim range as `start,end` |
| `--target-trim` | `10000,100000` | Target trim range as `start,end` |
| `--output-dir` | `PROJECT` | Project name under `REGRADES/` |
| `--tonemapping` | all 12 | Tonemapping functions for both sides |
| `--source-tonemapping` | — | Override the tonemappers applied to the source |
| `--target-tonemapping` | — | Override the tonemappers applied to the target |
| `--post-tonemapping` | — | Override the post-tonemapping functions |
| `--luts` | `PQ_to_BT709_v1.cube,PQ_to_BT709_v2.cube` | LUT filenames, resolved against `LUTS/` |
| `--source-luts` | — | Override the LUTs applied to the source |
| `--target-luts` | — | Override the LUTs applied to the target |
| `--methods` | all 6 | Colour-matching methods to request from `match_colors`; see [Matching methods](#matching-methods) |
| `--datmatcher-dir` | — | Directory holding datMatcher's executables |

Passing an empty string to an override selects *no* variants for that axis —
`--source-tonemapping ""` is the normal way to skip tonemapping on one side.

## Matching methods

`--methods` selects which colour-matching algorithms the generated pipelines
use. All six are implemented by datMatcher's `match_colors`; the table below
summarises the approach taken by each.

| Method | Approach | Cost |
| --- | --- | --- |
| `rgb-moments` | Rescales each channel linearly so its mean and standard deviation match the target's — a gain + offset correction that ignores histogram shape entirely. | Cheapest |
| `rgb-1d` | Matches each channel's cumulative histogram independently. No cross-channel awareness, so it cannot move a colour that is only wrong relative to the others. | Cheapest |
| `rgb-3d-joint` | Matches the joint 3D distribution through a flattened index — a cheap rank match, cruder than IDT. | Cheap |
| `rgb-3d-idt` | Joint 3D matching by iterative axis-cycling. | Moderate |
| `rgb-3d-emd` | Approximates full 3D optimal transport by slicing: both distributions are projected onto many random 1D directions, each solved exactly by CDF matching, and the resulting corrections averaged. More projections get closer to true 3D transport. | Expensive |
| `rgb-3d-sinkhorn` | Entropic-regularized optimal transport, solved by Sinkhorn-Knopp iteration. | Expensive |

The costs are relative, not measured: the two 1D methods are the cheap end,
joint is nearly as cheap, and the optimal-transport pair is the expensive end.
datMatcher's own source calls both `rgb-moments` and `rgb-1d` its fastest, so
treat the ordering within each end as approximate.

The three 3D methods are the interesting ones, because they can move colours
*relative to each other* — the thing a per-channel match cannot do. Sinkhorn
and EMD are the most principled and the most expensive; IDT is the middle
ground; joint is the cheap approximation of the same idea. `rgb-moments`
ignores distribution shape altogether, so it is best as a fast preview, or as a
sanity check that the two videos are already close.

The implementations, their tuning flags (`--idt-iterations`,
`--emd-projections`, `--sinkhorn-epsilon`, `--sinkhorn-iterations`) and their
exact behaviour all belong to datMatcher — see
[its README](https://github.com/datphyr/datMatcher) for those.

### Narrowing a run

`--methods` is passed straight through to `match_colors`, so limiting it does
reduce the matching work: each source/target pair writes one LUT per requested
method instead of all six. Names are validated before anything is generated,
with the valid list printed, rather than failing hours later inside a batch
file.

The other levers are the variant axes — `--tonemapping`, `--luts` and their
per-side overrides, which decide how many `match_colors` runs happen at all —
and a shorter `--frames` list, which sets the capture count.

## Tonemapping and LUTs

Getting from HDR to SDR is the actual problem this project is about, and there
is no single right answer. datRegrade treats it as a search, so the ways of
doing it are inputs you vary rather than something baked in.

### The tonemapping functions

These are [`libplacebo_Tonemap`](https://github.com/ildar-shaimordanov/avs_libplacebo)
functions; the names and descriptions below are libplacebo's own.

| Function | Description |
| --- | --- |
| `clip` | No tone mapping — everything above the target is clipped. |
| `st2094-40` | SMPTE ST 2094-40 Annex B (HDR10+ dynamic metadata). |
| `st2094-10` | SMPTE ST 2094-10 Annex B.2. |
| `bt2390` | ITU-R BT.2390 EETF — the reference HDR→SDR curve. |
| `bt2446a` | ITU-R BT.2446 Method A. |
| `spline` | Single-pivot polynomial spline. |
| `reinhard` | Reinhard. |
| `mobius` | Möbius. |
| `hable` | Filmic tone-mapping (Hable). |
| `gamma` | Gamma function with knee. |
| `linear` | Perceptually linear stretch. |
| `linearlight` | Linear light stretch. |

They fall into rough families. `bt2390` and the ST 2094 pair are the broadcast
derived curves, with the ST ones using dynamic metadata where the source
carries it. `bt2446a` is its own ITU method. `reinhard`, `mobius` and `hable`
are the classic photographic curves — `hable` in particular is the filmic look
familiar from games. `clip` is the do-nothing baseline worth including as a
control. The `linear`/`linearlight` and `gamma` entries are simple stretches
rather than perceptual curves.

They are used in three different places, which is worth keeping straight:

| Applied as | What it means |
| --- | --- |
| Source variant | The HDR source is tone-mapped to SDR with that function, giving a different starting point to match *from*. |
| Target variant | The reference is put through the same function, asking "what if the reference had been mastered this way?" |
| Post-tonemapping | Applied *after* the LUT, to see how the matched result responds to a different final curve. |

### The LUTs

The bundled `LUTS/*.cube` files are fixed PQ→BT709 conversions — a hand-built
answer to the same HDR→SDR question, rather than a parametric curve. They are
treated as variants on equal footing with the tonemappers, which is why
`--luts` sits alongside `--tonemapping`.

Where a source variant is itself a LUT, the match LUT is *composed* with it
(`utils/cube.py`) so the pipeline stays a single LUT for the capture step. That
composition is the reason the LUT toolkit exists at all.

The `--source-tonemapping` / `--target-tonemapping` / `--luts` overrides exist
so the two sides can be varied independently — the useful question is usually
what happens when the source and target are treated *differently*.

## The LUT toolkit

Composing two LUTs has to be done numerically — you cannot just concatenate
`.cube` files, because sampling the second LUT at the first LUT's output
requires interpolation. `utils/cube.py` does that in-tree, and replaces
the two helper scripts earlier versions of this project shelled out to.

```sh
# Apply a.cube and then b.cube (b(a(rgb))), keeping the larger LUT size
python utils/cube.py compose -i a.cube -c b.cube -o out.cube --preserve

# Inspect a LUT
python utils/cube.py info -i out.cube

# Resize a LUT
python utils/cube.py resize -i a.cube -o small.cube --size 33

# Linearly blend two equally sized LUTs
python utils/cube.py blend -i a.cube -c b.cube -o mix.cube --amount 0.5
```

It implements the standard tetrahedral interpolation (matching the
`interp="tetrahedral"` used by the AviSynth `DGCube` calls, so the LUTs behave
the same in and out of the pipeline), plus nearest-neighbour, and normalizes
`DOMAIN_MIN`/`DOMAIN_MAX` on read so callers always see `0..1`.

`compose` keeps the flag names of the script it replaced — `--combine`,
`--preserve`, `--mixer`, `--method` — so existing command lines keep working,
and its output has been verified cell-by-cell against that original.

## Design notes

The background behind some of the structure above.

### Preparation is deliberately separated from execution

`auto_regrade.py` only ever prepares a project: it renders AviSynth scripts and
writes command files, and never invokes DGIndexNV, datMatcher or ffmpeg.

That split exists because the hard part of a regrade is a judgement call. The
full default matrix is on the order of a thousand pipelines, and no automated
metric tells you which one looks like the film you are matching — you have to
look at frames. The generator gets you to the point where you can start
looking; from there you drive it yourself and re-run only what you changed.

It also means the generator is cheap, safe to re-run, and works without any of
the media tooling installed — you can prepare a project before datMatcher or
AviSynth+ are even set up.

### The variant matrix

Source and target each get a set of variants, and the useful comparison is
usually the cross product rather than either side alone.

`plain` ↔ `hdr` is a special pairing — an HDR source with no conversion,
against an HDR target — and is skipped against the other variants. Where a
source variant is itself a LUT, the match LUT is composed with it so that the
pipeline stays a single LUT for the capture step. That composition is what
`utils/cube.py` exists for.

### Three LUT conventions worth knowing

These are load-bearing: a reimplementation can be perfectly self-consistent and
still disagree with the original tool on any of them.

1. **File ordering.** `.cube` entries run red axis fastest, then green, then
   blue, so a naive reshape yields `[b, g, r]`. `utils/cube.py` transposes
   immediately and uses `[r, g, b]` everywhere else.
2. **Interpolation.** Composition *always* samples tetrahedrally, matching the
   `interp="tetrahedral"` the generated AviSynth scripts pass to `DGCube`, so
   composing here and applying there agree. The `--method` flag only reaches
   the resampling helper; using nearest-neighbour for the composition itself
   introduces visible quantization error.
3. **Tie-breaking.** Nearest-neighbour resampling rounds exact `.5` fractions
   *down*. `numpy.rint` rounds halves to even instead, which differs on
   exactly-tied inputs — resampling 3 → 5 hits them, and so does the real
   65-point LUT.

None of these were reasoned out; they were established by comparing against the
original LUTify script that `utils/cube.py` replaced, cell by cell, across sizes,
mixer values and both interpolation methods. Each one was something an earlier
draft got wrong, which is why they are written down rather than left implicit.

## Project layout

```
auto_regrade.py          the project generator (CLI)
utils/
├── cube.py              the .cube LUT toolkit
└── datMatcher/          drop datMatcher's executables here (gitignored)
LUTS/                    PQ->BT709 conversion LUTs used as source variants
examples/                example command lines
REGRADES/                generated per-project output (gitignored)
```

## Licensing note

The bundled `LUTS/*.cube` files carry a `TITLE` naming their original author.
If you are reusing them outside this project, check their provenance first.

This project has no `LICENSE` file, which means it is all rights reserved by
default — the same as [datMatcher](https://github.com/datphyr/datMatcher).
