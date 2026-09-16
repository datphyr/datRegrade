# datRegrade

Prepare a colour-regrade project that matches an HDR source video to an SDR reference.

`datRegrade` is the orchestration layer around [datMatcher](https://github.com/datphyr/datMatcher).
Given an HDR source and a reference (target) video, it generates a complete,
self-contained regrade project: the AviSynth scripts, the LUTs, and the
step-by-step command batches that produce them.

It **prepares** a project and prints the commands; it never runs them itself.
Inspect the generated scripts, run the steps you want, and compare the results.

[![build](https://github.com/datphyr/datRegrade/actions/workflows/build.yml/badge.svg)](https://github.com/datphyr/datRegrade/actions/workflows/build.yml)

## Contents

- [How it works](#how-it-works)
- [What gets generated](#what-gets-generated)
- [Requirements](#requirements)
- [Installing](#installing)
- [Usage](#usage)
- [Options](#options)
- [The LUT toolkit](#the-lut-toolkit)
- [Project layout](#project-layout)
- [Testing](#testing)
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
| Source variants | 13 | `plain`, the PQ→BT709 LUTs, and each tonemapper applied to the source |
| Target variants | 13 | `plain`, `hdr`, the LUTs, and each tonemapper applied to the target |
| Matching methods | 6 | The algorithms handed to `match_colors` |

That default is roughly 13 × 13 × 6 combinations, which is why the work is
fanned out into per-step command files rather than done in one go.

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
its own directory first, and references the tools via paths relative to that
project directory, so the files stay valid wherever the run happened.

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

### Pointing datRegrade at datMatcher

datMatcher is a separate project and is deliberately **not** vendored here —
its FFmpeg-linked binaries are large and change independently. Build or
download it (see [datMatcher's README](https://github.com/datphyr/datMatcher)),
then tell datRegrade where the executables are, in order of precedence:

1. The `--datmatcher-dir` option.
2. The `DATMATCHER_DIR` environment variable.
3. `utils/datMatcher/` next to `auto_regrade.py`.
4. `UTILS/datMatcher/` (the legacy name), for older checkouts.

`extract_colors` and `match_colors` are looked up with and without a `.exe`
suffix, so the same layout works on either platform.

## Installing

Only the preparation step needs a Python environment:

```sh
python -m pip install -r requirements.txt
```

or, to get the LUT command-line tool installed as well:

```sh
python -m pip install .
```

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
| `--methods` | all 6 | Matching algorithms passed to `match_colors` |
| `--datmatcher-dir` | — | Directory holding datMatcher's executables |

Passing an empty string to an override selects *no* variants for that axis —
`--source-tonemapping ""` is the normal way to skip tonemapping on one side.

## The LUT toolkit

Composing two LUTs has to be done numerically — you cannot just concatenate
`.cube` files, because sampling the second LUT at the first LUT's output
requires interpolation. `datregrade/cube.py` does that in-tree, and replaces
the two helper scripts earlier versions of this project shelled out to.

```sh
# Apply a.cube and then b.cube (b(a(rgb))), keeping the larger LUT size
python -m datregrade.cube compose -i a.cube -c b.cube -o out.cube --preserve

# Inspect a LUT
python -m datregrade.cube info -i out.cube

# Resize a LUT
python -m datregrade.cube resize -i a.cube -o small.cube --size 33

# Linearly blend two equally sized LUTs
python -m datregrade.cube blend -i a.cube -c b.cube -o mix.cube --amount 0.5
```

It implements the standard tetrahedral interpolation (matching the
`interp="tetrahedral"` used by the AviSynth `DGCube` calls, so the LUTs behave
the same in and out of the pipeline), plus nearest-neighbour, and normalizes
`DOMAIN_MIN`/`DOMAIN_MAX` on read so callers always see `0..1`.

`compose` keeps the flag names of the script it replaced — `--combine`,
`--preserve`, `--mixer`, `--method` — so existing command lines keep working,
and its output has been verified cell-by-cell against that original.

## Project layout

```
auto_regrade.py          the project generator (CLI)
datregrade/
└── cube.py              the .cube LUT toolkit
LUTS/                    PQ->BT709 conversion LUTs used as source variants
tests/                   test suite + fixtures
docs/                    design notes
examples/                example command lines
REGRADES/                generated per-project output (gitignored)
```

## Testing

```sh
python -m pytest tests/ -v
```

The suite pins the LUT conventions rather than just internal consistency:
composing is checked against hand-computed algebra, against the identity LUT,
and against a reference `.cube` produced by the original LUTify script that
`datregrade/cube.py` replaced.

## Licensing note

The bundled `LUTS/*.cube` files carry a `TITLE` naming their original author.
If you are reusing them outside this project, check their provenance first.

This project has no `LICENSE` file, which means it is all rights reserved by
default — the same as [datMatcher](https://github.com/datphyr/datMatcher).
