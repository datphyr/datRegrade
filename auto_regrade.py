#!/usr/bin/env python3
"""datRegrade: prepare a colour-regrade project from two video sources.

Given an HDR source and a reference (target) video, this script generates
everything a regrade run needs -- AviSynth scripts, LUTs, and the command
batches that drive datMatcher, ffmpeg and DGIndexNV -- across a matrix of
tonemapping and LUT-match variants. It only *prepares* those files and prints
the commands; it never executes them. See README.md for the workflow.
"""

import argparse
import os
import sys
import subprocess
from pathlib import Path

import jinja2


#: Default location for datMatcher's executables, relative to this repo.
DATMATCHER_SUBDIR = Path("utils") / "datMatcher"


def _executable_names(name):
    """Return the filenames to look for, with and without the Windows suffix."""
    return (name, f"{name}.exe")


def _datmatcher_search_dirs():
    """Directories that may hold datMatcher's executables, most specific first.

    ``utils/`` is the intended drop-in location, so it is searched both as
    ``utils/datMatcher/`` and as ``utils/`` itself.
    """
    override = os.environ.get("DATMATCHER_DIR")
    if override:
        yield Path(override).expanduser().absolute()
    repo_root = Path(__file__).resolve().parent
    yield repo_root / DATMATCHER_SUBDIR
    yield repo_root / "utils"
    yield repo_root / "UTILS" / "datMatcher"
    yield repo_root / "UTILS"


def _find_executable(directory, names, max_depth=3):
    """Find any of ``names`` in ``directory``, then in its subdirectories.

    datMatcher builds into a subdirectory (``build/``, ``build/Release/``, ...),
    so searching a few levels down lets a whole datMatcher checkout be dropped
    into ``utils/`` as-is instead of extracting the two executables by hand.
    """
    if not directory.is_dir():
        return None
    for filename in names:
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    if max_depth <= 0:
        return None
    try:
        subdirs = sorted(p for p in directory.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return None
    for subdir in subdirs:
        if subdir.name in {".git", "__pycache__"}:
            continue
        found = _find_executable(subdir, names, max_depth - 1)
        if found is not None:
            return found
    return None


_warned_tools = set()
_resolved_tools: dict[str, str] = {}


def datmatcher_tool(name):
    """Return the path to a datMatcher executable (``extract_colors``/``match_colors``).

    Drop datMatcher's executables into ``utils/datMatcher/`` -- or anywhere
    else under ``utils/`` -- and they are found automatically. An explicit
    location wins over that, via ``--datmatcher-dir`` or ``$DATMATCHER_DIR``.

    If nothing is found, the bare name is returned so the generated command
    still shows what was missing, and a warning reports where it looked once
    per tool.
    """
    if name in _resolved_tools:
        return _resolved_tools[name]

    names = _executable_names(name)
    for directory in _datmatcher_search_dirs():
        found = _find_executable(directory, names)
        if found is not None:
            _resolved_tools[name] = str(found)
            return str(found)

    if name not in _warned_tools:
        _warned_tools.add(name)
        searched = ", ".join(str(d) for d in _datmatcher_search_dirs())
        print(f"[WARNING] {name} not found. Searched: {searched}")
        print(
            "[WARNING] Put datMatcher's executables in ./utils/datMatcher/ "
            "(or anywhere under ./utils/), or pass --datmatcher-dir."
        )
    _resolved_tools[name] = name
    return name


TEMPLATES = {
    "var_SOURCE.avs.j2": """\
SOURCE = DGSource("{{ dgi_path }}")
SOURCE = SOURCE.Crop({{ crop }})
""",
    "var_SOURCE_TRIM.avs.j2": """\
Import("var_SOURCE.avs")
SOURCE_TRIM = SOURCE.Trim({{ trim_start }}, {{ trim_end }})
""",
    "var_TARGET.avs.j2": """\
TARGET = DGSource("{{ dgi_path }}")
TARGET = TARGET.Crop({{ crop }})
""",
    "var_TARGET_TRIM.avs.j2": """\
Import("var_TARGET.avs")
TARGET_TRIM = TARGET.Trim({{ trim_start }}, {{ trim_end }})
""",
    "SOURCE_TRIM_method.avs.j2": """\
Import("var_SOURCE_TRIM.avs")
SOURCE_TRIM
{% if method_type == 'lut' %}
ConvertToPlanarRGB()
DGCube("{{ lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% elif method_type == 'tonemap' %}
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ tm }}")
ConvertToPlanarRGB()
{% elif method_type == 'plain' %}
ConvertToPlanarRGB()
{% endif %}
ConvertBits(10)
""",
    "SOURCE_method.avs.j2": """\
Import("var_SOURCE.avs")
SOURCE
{% if method_type == 'lut' %}
ConvertToPlanarRGB()
DGCube("{{ lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% elif method_type == 'tonemap' %}
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ tm }}")
{% elif method_type == 'plain' %}
{% endif %}
""",
    "TARGET_TRIM.avs.j2": """\
Import("var_TARGET_TRIM.avs")
TARGET_TRIM
ConvertToPlanarRGB()
ConvertBits(10)
""",
    "TARGET_TRIM_tm.avs.j2": """\
Import("var_TARGET_TRIM.avs")
TARGET_TRIM
libplacebo_Tonemap(src_max=100, src_csp=0, dst_csp=1)
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ tm }}")
ConvertToPlanarRGB()
ConvertBits(10)
""",
    "TARGET_TRIM_lut.avs.j2": """\
Import("var_TARGET_TRIM.avs")
TARGET_TRIM
libplacebo_Tonemap(src_max=100, src_csp=0, dst_csp=1)
ConvertToPlanarRGB()
DGCube("{{ lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
ConvertBits(10)
""",
    "TARGET_TRIM_hdr.avs.j2": """\
Import("var_TARGET_TRIM.avs")
TARGET_TRIM
libplacebo_Tonemap(src_max=100, src_csp=0, dst_csp=1)
ConvertToPlanarRGB()
ConvertBits(10)
""",
    "TARGET_plain.avs.j2": """\
Import("var_TARGET.avs")
TARGET
""",
    "TARGET_tm.avs.j2": """\
Import("var_TARGET.avs")
TARGET
libplacebo_Tonemap(src_max=100, src_csp=0, dst_csp=1)
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ tm }}")
""",
    "TARGET_lut.avs.j2": """\
Import("var_TARGET.avs")
TARGET
libplacebo_Tonemap(src_max=100, src_csp=0, dst_csp=1)
ConvertToPlanarRGB()
DGCube("{{ lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
""",
    "TARGET_hdr.avs.j2": """\
Import("var_TARGET.avs")
TARGET
libplacebo_Tonemap(src_max=100, src_csp=0, dst_csp=1)
""",
    "regrade.avs.j2": """\
Import("{{ base_path }}")
SOURCE
{% if source_type == 'lut' %}
ConvertToPlanarRGB()
DGCube("{{ applied_lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% elif source_type == 'tonemap' %}
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ source_tm }}")
ConvertToPlanarRGB()
DGCube("{{ applied_lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% elif source_type == 'plain' %}
ConvertToPlanarRGB()
DGCube("{{ applied_lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% endif %}
{% if post_tm %}
{% if source_type == 'plain' and target_name == 'hdr' %}
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ post_tm }}")
{% else %}
libplacebo_Tonemap(src_max=100, src_csp=0, dst_csp=1)
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ post_tm }}")
{% endif %}
{% endif %}
""",
    "regrade_trim.avs.j2": """\
Import("{{ base_path }}")
SOURCE_TRIM
{% if source_type == 'lut' %}
ConvertToPlanarRGB()
DGCube("{{ applied_lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% elif source_type == 'tonemap' %}
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ source_tm }}")
ConvertToPlanarRGB()
DGCube("{{ applied_lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% elif source_type == 'plain' %}
ConvertToPlanarRGB()
DGCube("{{ applied_lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% endif %}
{% if post_tm %}
{% if source_type == 'plain' and target_name == 'hdr' %}
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ post_tm }}")
{% else %}
libplacebo_Tonemap(src_max=100, src_csp=0, dst_csp=1)
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ post_tm }}")
{% endif %}
{% endif %}
""",
    "regrade_plain_hdr_post.avs.j2": """\
Import("{{ base_path }}")
SOURCE
{% if source_type == 'lut' %}
ConvertToPlanarRGB()
DGCube("{{ applied_lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% elif source_type == 'tonemap' %}
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ source_tm }}")
ConvertToPlanarRGB()
DGCube("{{ applied_lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% elif source_type == 'plain' %}
ConvertToPlanarRGB()
DGCube("{{ applied_lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% endif %}
{% if post_tm %}
libplacebo_Tonemap(src_max=100, src_csp=0, dst_csp=1)
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ post_tm }}")
{% endif %}
""",
    "regrade_trim_plain_hdr_post.avs.j2": """\
Import("{{ base_path }}")
SOURCE_TRIM
{% if source_type == 'lut' %}
ConvertToPlanarRGB()
DGCube("{{ applied_lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% elif source_type == 'tonemap' %}
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ source_tm }}")
ConvertToPlanarRGB()
DGCube("{{ applied_lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% elif source_type == 'plain' %}
ConvertToPlanarRGB()
DGCube("{{ applied_lut_path }}", in="full", out="full", lut="full", interp="tetrahedral")
{% endif %}
{% if post_tm %}
libplacebo_Tonemap(src_max=100, src_csp=0, dst_csp=1)
libplacebo_Tonemap(dst_max=100, tone_mapping_function="{{ post_tm }}")
{% endif %}
""",
    "capture.avs.j2": """\
Import("{{ input_script }}")
clip = BlankClip(last, length=0)
{% for frame in frames %}
clip = clip + last.Trim({{ frame }}, {{ frame }})
{% endfor %}
clip

new_width  = 3840
new_height = 2076
Spline36Resize(new_width, new_height)

ConvertToPlanarRGB()
Histogram(mode="Levels", bits=10)
"""
}

def render_template(template_name, output_path, context):
    env = jinja2.Environment()
    template = env.from_string(TEMPLATES[template_name])
    rendered = template.render(context)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(rendered)
    print(f"[RENDER] {output_path}")

def rel_path_from_to(from_file, to_file):
    from_dir = os.path.dirname(os.path.abspath(from_file))
    to_path = os.path.abspath(to_file)
    rel = os.path.relpath(to_path, from_dir)
    return rel.replace(os.sep, '/')

def path_for_avs_from_script(target_path, script_path):
    return rel_path_from_to(script_path, target_path)

def format_cmd(cmd_list):
    return subprocess.list2cmdline(cmd_list)

def _write_script_header(f):
    # Generated commands reference paths relative to the project directory
    # (e.g. `COLORS\...`, `..\..\utils\...`), so each script anchors itself
    # there rather than depending on where it was launched from.
    if os.name == 'nt':
        f.write("@echo off\n")
        f.write('cd /d "%~dp0"\n')
        f.write("echo Running generated commands...\n")
    else:
        f.write("#!/bin/bash\n")
        f.write("set -e\n")
        f.write('cd "$(dirname \"$0\")"\n')
        f.write('echo "Running generated commands..."\n')

def relativize_cmd_for_recording(cmd_list, base_dir, external_dirs):
    """Rewrite absolute tool paths as paths relative to ``base_dir``.

    Everything this script emits is anchored at the project directory, so a
    tool living at ``<repo>/utils/datMatcher/match_colors.exe`` is recorded as
    ``..\\..\\utils\\datMatcher\\match_colors.exe``. Tools resolved outside
    those roots -- a datMatcher checkout elsewhere, or ``ffmpeg`` on ``PATH``
    -- are left alone.
    """
    new_list = []
    base_dir_abs = os.path.abspath(base_dir)
    roots = [base_dir_abs] + [os.path.abspath(d) for d in external_dirs if d]

    for elem in cmd_list:
        if not isinstance(elem, str):
            new_list.append(elem)
            continue
        if elem == sys.executable:
            new_list.append(os.path.basename(sys.executable))
            continue
        if not os.path.isabs(elem):
            new_list.append(elem)
            continue
        abs_elem = os.path.abspath(elem)
        for root in roots:
            try:
                if os.path.commonpath([root, abs_elem]) == root:
                    new_list.append(os.path.relpath(abs_elem, base_dir_abs))
                    break
            except ValueError:
                # Different Windows drives cannot be made relative to each other.
                continue
        else:
            new_list.append(elem)
    return new_list

def split_comma_list(s):
    if not s:
        return []
    return [item.strip() for item in s.split(',') if item.strip()]

def get_match_lut_dir(luts_base, source_variant, target_variant):
    return luts_base / f"source_{source_variant}" / f"target_{target_variant}"

def get_match_lut_path(luts_base, source_variant, target_variant, method):
    return get_match_lut_dir(luts_base, source_variant, target_variant) / get_match_lut_filename(source_variant, target_variant, method)

def get_match_lut_filename(source_variant, target_variant, method):
    return f"source_{source_variant}_colors_target_{target_variant}_colors_{method}.cube"

def get_combined_lut_path(luts_base, source_variant, target_variant, method):
    return get_match_lut_dir(luts_base, source_variant, target_variant) / f"combined_source_{source_variant}_target_{target_variant}_via_{method}.cube"

def get_post_combined_lut_path(luts_base, post_lut_name, method):
    return luts_base / "source_plain" / "target_hdr" / f"method_{method}" / f"combined_{post_lut_name}.cube"

def get_regrade_script_dir(regrade_root, source_variant, target_variant, method, post):
    dir_path = regrade_root / f"source_{source_variant}" / f"target_{target_variant}" / f"method_{method}"
    if post:
        dir_path = dir_path / f"post_{post}"
    else:
        dir_path = dir_path / "no_post"
    return dir_path

def get_regrade_trim_script_dir(regrade_trim_root, source_variant, target_variant, method, post):
    dir_path = regrade_trim_root / f"source_{source_variant}" / f"target_{target_variant}" / f"method_{method}"
    if post:
        dir_path = dir_path / f"post_{post}"
    else:
        dir_path = dir_path / "no_post"
    return dir_path

def get_capture_regrade_script_dir(capture_regrade_root, source_variant, target_variant, method, post):
    dir_path = capture_regrade_root / f"source_{source_variant}" / f"target_{target_variant}" / f"method_{method}"
    if post:
        dir_path = dir_path / f"post_{post}"
    else:
        dir_path = dir_path / "no_post"
    return dir_path

def get_capture_source_script_dir(capture_source_root, source_variant):
    return capture_source_root / source_variant

def get_capture_target_script_dir(capture_target_root, target_variant):
    return capture_target_root / target_variant

def main():
    parser = argparse.ArgumentParser(description="Prepare all files and command scripts for regrade generation (no execution).")
    parser.add_argument("--source", required=True, help="Path to source HDR video")
    parser.add_argument("--target", required=True, help="Path to target SDR video")
    parser.add_argument("--frames", required=True, help="Comma-separated list of frame numbers or file with one per line")
    parser.add_argument("--source-crop", default="0,0,0,0", help="Crop for source video: left,top,right,bottom")
    parser.add_argument("--target-crop", default="0,0,0,0", help="Crop for target video")
    parser.add_argument("--source-trim", default="10000,100000", help="Trim range for source as 'start,end'")
    parser.add_argument("--target-trim", default="10000,100000", help="Trim range for target as 'start,end'")
    parser.add_argument("--output-dir", default="PROJECT", help="Project name under REGRADES/")
    parser.add_argument("--tonemapping", default="clip,st2094-40,st2094-10,bt2390,bt2446a,spline,reinhard,mobius,hable,gamma,linear,linearlight", help="Comma-separated tonemapping functions")
    parser.add_argument("--source-tonemapping", help="Override source tonemapping functions")
    parser.add_argument("--target-tonemapping", help="Override target tonemapping functions")
    parser.add_argument("--post-tonemapping", help="Override post-tonemapping functions")
    parser.add_argument("--luts", default="PQ_to_BT709_v1.cube,PQ_to_BT709_v2.cube", help="Comma-separated LUT filenames")
    parser.add_argument("--source-luts", help="Override source LUT filenames")
    parser.add_argument("--target-luts", help="Override target LUT filenames")
    parser.add_argument("--methods", default="rgb-1d,rgb-3d-joint,rgb-3d-idt,rgb-3d-emd,rgb-3d-sinkhorn,rgb-moments", help="Comma-separated matching methods")
    parser.add_argument("--datmatcher-dir", default=None, help="Directory holding datMatcher's extract_colors/match_colors executables (default: $DATMATCHER_DIR, then ./utils/datMatcher)")
    args = parser.parse_args()

    if args.datmatcher_dir:
        os.environ["DATMATCHER_DIR"] = args.datmatcher_dir

    default_tm_list = split_comma_list(args.tonemapping)
    source_tm_list = split_comma_list(args.source_tonemapping) if args.source_tonemapping is not None else default_tm_list
    target_tm_list = split_comma_list(args.target_tonemapping) if args.target_tonemapping is not None else default_tm_list
    post_tm_list = split_comma_list(args.post_tonemapping) if args.post_tonemapping is not None else default_tm_list

    default_luts = split_comma_list(args.luts)
    source_luts = split_comma_list(args.source_luts) if args.source_luts is not None else default_luts
    target_luts = split_comma_list(args.target_luts) if args.target_luts is not None else default_luts

    methods = split_comma_list(args.methods)
    if not methods:
        print("[ERROR] At least one matching algorithm must be specified.")
        sys.exit(1)

    repo_root = Path(__file__).resolve().parent
    base_dir = repo_root / "REGRADES"
    out_dir = (base_dir / args.output_dir).absolute()
    source_path = Path(args.source).absolute()
    target_path = Path(args.target).absolute()
    luts_base_dir = repo_root / "LUTS"
    # datRegrade composes LUTs with its own script instead of shelling out to
    # the third-party LUTify script it used to vendor; see utils/cube.py.
    cube_script = repo_root / "utils" / "cube.py"
    external_dirs = [repo_root] + list(_datmatcher_search_dirs())

    lut_map = {}
    all_lut_filenames = set(source_luts + target_luts)
    for fname in all_lut_filenames:
        lut_path = luts_base_dir / fname
        if not lut_path.exists():
            print(f"[WARNING] LUT file not found: {lut_path}")
        base = Path(fname).stem
        lut_map[base] = lut_path.absolute()

    index_dir = out_dir / "INDEX"
    scripts_dir = out_dir / "SCRIPTS"
    base_scripts_dir = scripts_dir / "base"
    regrade_root_dir = scripts_dir / "regrade"
    regrade_trim_root_dir = scripts_dir / "regrade_trim"
    capture_root_dir = scripts_dir / "capture"
    capture_regrade_dir = capture_root_dir / "regrade"
    capture_source_dir = capture_root_dir / "source"
    capture_target_dir = capture_root_dir / "target"
    luts_dir = out_dir / "LUTS"
    screenshots_dir = out_dir / "SCREENSHOTS"
    colors_dir = out_dir / "COLORS"

    for d in [index_dir, scripts_dir, base_scripts_dir, regrade_root_dir, regrade_trim_root_dir,
              capture_root_dir, capture_regrade_dir, capture_source_dir, capture_target_dir,
              luts_dir, screenshots_dir, colors_dir]:
        os.makedirs(d, exist_ok=True)
        print(f"[DIR] {d}")

    combined_commands = []
    commands_by_step = {
        'index': [],
        'color': [],
        'extract_source': [],
        'extract_target': [],
        'match_lut': [],
        'combine_lut': [],
        'capture': [],
        'capture_source': [],
        'capture_target': []
    }
    capture_commands_by_source = {}

    def record_cmd(cmd_list, step, desc=None):
        dry_cmd_list = relativize_cmd_for_recording(cmd_list, out_dir, external_dirs)
        cmd_str = format_cmd(dry_cmd_list)
        combined_commands.append(cmd_str)
        if step in commands_by_step:
            commands_by_step[step].append(cmd_str)
        else:
            commands_by_step[step] = [cmd_str]
        if desc:
            print(f"[RECORD][{step}] {desc}")
        else:
            print(f"[RECORD][{step}] {cmd_str}")

    source_dgi = index_dir / "source.dgi"
    target_dgi = index_dir / "target.dgi"

    record_cmd(["DGIndexNV", "-i", str(source_path), "-o", str(source_dgi), "-exit"], step='index', desc="Index source video")
    record_cmd(["DGIndexNV", "-i", str(target_path), "-o", str(target_dgi), "-exit"], step='index', desc="Index target video")

    script_path = base_scripts_dir / "var_SOURCE.avs"
    render_template("var_SOURCE.avs.j2", script_path, {
        "dgi_path": path_for_avs_from_script(source_dgi, script_path),
        "crop": args.source_crop
    })
    render_template("var_SOURCE_TRIM.avs.j2", base_scripts_dir / "var_SOURCE_TRIM.avs", {
        "trim_start": int(args.source_trim.split(',')[0]),
        "trim_end": int(args.source_trim.split(',')[1])
    })
    script_path = base_scripts_dir / "var_TARGET.avs"
    render_template("var_TARGET.avs.j2", script_path, {
        "dgi_path": path_for_avs_from_script(target_dgi, script_path),
        "crop": args.target_crop
    })
    render_template("var_TARGET_TRIM.avs.j2", base_scripts_dir / "var_TARGET_TRIM.avs", {
        "trim_start": int(args.target_trim.split(',')[0]),
        "trim_end": int(args.target_trim.split(',')[1])
    })

    source_variants = []
    source_variants.append({'name': 'plain', 'type': 'plain'})
    for fname in source_luts:
        base = Path(fname).stem
        source_variants.append({'name': base, 'type': 'lut', 'lut_path': lut_map[base]})
    for tm in source_tm_list:
        source_variants.append({'name': tm, 'type': 'tonemap', 'tm': tm})

    target_tonemap_variants = target_tm_list
    target_lut_variants = [Path(fname).stem for fname in target_luts]
    all_target_variants = ['plain'] + ['hdr'] + target_lut_variants + target_tonemap_variants

    render_template("TARGET_TRIM.avs.j2", base_scripts_dir / "TARGET_TRIM_plain.avs", {})
    for tm in target_tonemap_variants:
        out_name = f"TARGET_TRIM_{tm}.avs"
        render_template("TARGET_TRIM_tm.avs.j2", base_scripts_dir / out_name, {"tm": tm})
    for base in target_lut_variants:
        out_name = f"TARGET_TRIM_{base}.avs"
        script_path = base_scripts_dir / out_name
        render_template("TARGET_TRIM_lut.avs.j2", script_path, {"lut_path": path_for_avs_from_script(lut_map[base], script_path)})
    render_template("TARGET_TRIM_hdr.avs.j2", base_scripts_dir / "TARGET_TRIM_hdr.avs", {})

    source_color_scripts = {}
    for sv in source_variants:
        script_name = f"SOURCE_TRIM_{sv['name']}.avs"
        script_path = base_scripts_dir / script_name
        source_color_scripts[sv['name']] = script_path
        context = {'method_name': sv['name'], 'method_type': sv['type']}
        if sv['type'] == 'lut':
            context['lut_path'] = path_for_avs_from_script(sv['lut_path'], script_path)
        elif sv['type'] == 'tonemap':
            context['tm'] = sv['tm']
        render_template("SOURCE_TRIM_method.avs.j2", script_path, context)

    source_untrimmed_scripts = {}
    for sv in source_variants:
        script_name = f"SOURCE_{sv['name']}.avs"
        script_path = base_scripts_dir / script_name
        source_untrimmed_scripts[sv['name']] = script_path
        context = {'method_name': sv['name'], 'method_type': sv['type']}
        if sv['type'] == 'lut':
            context['lut_path'] = path_for_avs_from_script(sv['lut_path'], script_path)
        elif sv['type'] == 'tonemap':
            context['tm'] = sv['tm']
        render_template("SOURCE_method.avs.j2", script_path, context)

    target_untrimmed_scripts = {}
    script_path = base_scripts_dir / "TARGET_plain.avs"
    render_template("TARGET_plain.avs.j2", script_path, {})
    target_untrimmed_scripts['plain'] = script_path
    for tm in target_tonemap_variants:
        script_name = f"TARGET_{tm}.avs"
        script_path = base_scripts_dir / script_name
        render_template("TARGET_tm.avs.j2", script_path, {"tm": tm})
        target_untrimmed_scripts[tm] = script_path
    for base in target_lut_variants:
        script_name = f"TARGET_{base}.avs"
        script_path = base_scripts_dir / script_name
        render_template("TARGET_lut.avs.j2", script_path, {"lut_path": path_for_avs_from_script(lut_map[base], script_path)})
        target_untrimmed_scripts[base] = script_path
    script_path = base_scripts_dir / "TARGET_hdr.avs"
    render_template("TARGET_hdr.avs.j2", script_path, {})
    target_untrimmed_scripts['hdr'] = script_path

    frames_list = []
    frames_arg = args.frames
    if os.path.isfile(frames_arg):
        with open(frames_arg, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    frames_list.append(int(line))
    else:
        frames_list = [int(x.strip()) for x in frames_arg.split(',') if x.strip()]
    print(f"[INFO] Screenshot frames: {frames_list}")

    source_color_files = {}
    for sv in source_variants:
        color_name = f"source_{sv['name']}_colors.json"
        color_path = colors_dir / color_name
        source_color_files[sv['name']] = color_path
        step_name = f"color_source_{sv['name']}"
        cmd = [datmatcher_tool("extract_colors"), "--input", str(source_color_scripts[sv['name']].relative_to(out_dir)), "--output", str(color_path.relative_to(out_dir))]
        record_cmd(cmd, step=step_name, desc=f"Extract source colors ({sv['name']})")
        commands_by_step['color'].append(commands_by_step[step_name][-1])
        commands_by_step['extract_source'].append(commands_by_step[step_name][-1])

    target_color_files = {}
    for variant in all_target_variants:
        if variant == 'plain':
            script = base_scripts_dir / "TARGET_TRIM_plain.avs"
            color_name = f"target_plain_colors.json"
            step_name = "color_target_plain"
        elif variant == 'hdr':
            script = base_scripts_dir / "TARGET_TRIM_hdr.avs"
            color_name = f"target_hdr_colors.json"
            step_name = "color_target_hdr"
        elif variant in target_tonemap_variants:
            script = base_scripts_dir / f"TARGET_TRIM_{variant}.avs"
            color_name = f"target_{variant}_colors.json"
            step_name = f"color_target_{variant}"
        else:
            script = base_scripts_dir / f"TARGET_TRIM_{variant}.avs"
            color_name = f"target_{variant}_colors.json"
            step_name = f"color_target_{variant}"
        color_path = colors_dir / color_name
        target_color_files[variant] = color_path
        cmd = [datmatcher_tool("extract_colors"), "--input", str(script.relative_to(out_dir)), "--output", str(color_path.relative_to(out_dir))]
        record_cmd(cmd, step=step_name, desc=f"Extract target colors ({variant})")
        commands_by_step['color'].append(commands_by_step[step_name][-1])
        commands_by_step['extract_target'].append(commands_by_step[step_name][-1])

    match_lut_paths = {}
    combined_lut_paths = {}
    for sv in source_variants:
        for variant in all_target_variants:
            if sv['name'] == 'plain' and variant != 'hdr':
                continue
            if variant == 'hdr' and sv['name'] != 'plain':
                continue
            match_dir = get_match_lut_dir(luts_dir, sv['name'], variant)
            os.makedirs(match_dir, exist_ok=True)
            record_cmd([datmatcher_tool("match_colors"), "--source", str(source_color_files[sv['name']].relative_to(out_dir)), "--target", str(target_color_files[variant].relative_to(out_dir)), "--output", str(match_dir.relative_to(out_dir)), "--size", "65"], step='match_lut', desc=f"Match LUT: source {sv['name']} -> target {variant} (all algorithms)")
            for method in methods:
                match_lut_paths[(sv['name'], variant, method)] = get_match_lut_dir(luts_dir, sv['name'], variant) / get_match_lut_filename(sv['name'], variant, method)

    for sv in source_variants:
        if sv['type'] != 'lut':
            continue
        for variant in all_target_variants:
            if sv['name'] == 'plain' and variant != 'hdr':
                continue
            if variant == 'hdr' and sv['name'] != 'plain':
                continue
            for method in methods:
                match_lut = match_lut_paths[(sv['name'], variant, method)]
                combined_lut = get_combined_lut_path(luts_dir, sv['name'], variant, method)
                os.makedirs(combined_lut.parent, exist_ok=True)
                combined_lut_paths[(sv['name'], variant, method)] = combined_lut
                record_cmd([sys.executable, str(cube_script), "compose", "--preserve", "--input", str(sv['lut_path']), "--combine", str(match_lut.relative_to(out_dir)), "--output", str(combined_lut.relative_to(out_dir))], step='combine_lut', desc=f"Combine {sv['name']} LUT with match LUT ({method}) for target {variant}")

    post_combined_lut_paths = {}
    if any(sv['name'] == 'plain' for sv in source_variants) and 'hdr' in all_target_variants and source_luts:
        for method in methods:
            for post_lut in source_luts:
                post_lut_base = Path(post_lut).stem
                match_key = ('plain', 'hdr', method)
                if match_key not in match_lut_paths:
                    continue
                match_lut = match_lut_paths[match_key]
                combined_lut = get_post_combined_lut_path(luts_dir, post_lut_base, method)
                os.makedirs(combined_lut.parent, exist_ok=True)
                post_combined_lut_paths[(method, post_lut_base)] = combined_lut
                record_cmd(
                    [sys.executable, str(cube_script), "compose",
                     "--preserve", "--input", str(match_lut.relative_to(out_dir)),
                     "--combine", str(lut_map[post_lut_base]),
                     "--output", str(combined_lut.relative_to(out_dir))],
                    step='combine_lut',
                    desc=f"Combine match LUT (plain->hdr) with post LUT {post_lut_base} for method {method}"
                )

    post_tms = [''] + post_tm_list
    regrade_scripts_info = []
    for sv in source_variants:
        for variant in all_target_variants:
            if sv['name'] == 'plain' and variant != 'hdr':
                continue
            if variant == 'hdr' and sv['name'] != 'plain':
                continue
            for method in methods:
                if sv['type'] == 'lut':
                    applied_lut = combined_lut_paths[(sv['name'], variant, method)]
                    source_tm_arg = None
                else:
                    applied_lut = match_lut_paths[(sv['name'], variant, method)]
                    source_tm_arg = sv['tm'] if sv['type'] == 'tonemap' else None
                for post in post_tms:
                    script_dir = get_regrade_script_dir(regrade_root_dir, sv['name'], variant, method, post)
                    os.makedirs(script_dir, exist_ok=True)
                    script_path = script_dir / "regrade.avs"
                    base_import_path = rel_path_from_to(script_path, base_scripts_dir / "var_SOURCE.avs")
                    lut_rel = path_for_avs_from_script(applied_lut, script_path)
                    context = {'base_path': base_import_path, 'source_name': sv['name'], 'source_type': sv['type'], 'target_name': variant, 'applied_lut_path': lut_rel, 'post_tm': post}
                    if sv['type'] == 'tonemap':
                        context['source_tm'] = source_tm_arg
                    render_template("regrade.avs.j2", script_path, context)
                    regrade_scripts_info.append((sv['name'], script_path, "regrade.avs"))

    trimmed_regrade_scripts_info = []
    for sv in source_variants:
        for variant in all_target_variants:
            if sv['name'] == 'plain' and variant != 'hdr':
                continue
            if variant == 'hdr' and sv['name'] != 'plain':
                continue
            for method in methods:
                if sv['type'] == 'lut':
                    applied_lut = combined_lut_paths[(sv['name'], variant, method)]
                    source_tm_arg = None
                else:
                    applied_lut = match_lut_paths[(sv['name'], variant, method)]
                    source_tm_arg = sv['tm'] if sv['type'] == 'tonemap' else None
                for post in post_tms:
                    script_dir = get_regrade_trim_script_dir(regrade_trim_root_dir, sv['name'], variant, method, post)
                    os.makedirs(script_dir, exist_ok=True)
                    script_path = script_dir / "regrade.avs"
                    base_import_path = rel_path_from_to(script_path, base_scripts_dir / "var_SOURCE_TRIM.avs")
                    lut_rel = path_for_avs_from_script(applied_lut, script_path)
                    context = {'base_path': base_import_path, 'source_name': sv['name'], 'source_type': sv['type'], 'target_name': variant, 'applied_lut_path': lut_rel, 'post_tm': post}
                    if sv['type'] == 'tonemap':
                        context['source_tm'] = source_tm_arg
                    render_template("regrade_trim.avs.j2", script_path, context)
                    trimmed_regrade_scripts_info.append((sv['name'], script_path, "regrade.avs"))

    if any(sv['name'] == 'plain' for sv in source_variants) and 'hdr' in all_target_variants and source_luts:
        for method in methods:
            for post_lut in source_luts:
                post_lut_base = Path(post_lut).stem
                combined_key = (method, post_lut_base)
                if combined_key not in post_combined_lut_paths:
                    continue
                combined_lut = post_combined_lut_paths[combined_key]

                for post in [''] + post_tm_list:
                    if post:
                        post_folder = f"lut_{post_lut_base}_tm_{post}"
                    else:
                        post_folder = f"lut_{post_lut_base}"

                    script_dir_trim = regrade_trim_root_dir / "source_plain" / "target_hdr" / f"method_{method}" / post_folder
                    os.makedirs(script_dir_trim, exist_ok=True)
                    script_path_trim = script_dir_trim / "regrade.avs"
                    base_import_path_trim = rel_path_from_to(script_path_trim, base_scripts_dir / "var_SOURCE_TRIM.avs")
                    lut_rel_trim = path_for_avs_from_script(combined_lut, script_path_trim)
                    context_trim = {
                        'base_path': base_import_path_trim,
                        'source_name': 'plain',
                        'source_type': 'plain',
                        'target_name': 'hdr',
                        'applied_lut_path': lut_rel_trim,
                        'post_tm': post
                    }
                    if post:
                        render_template("regrade_trim_plain_hdr_post.avs.j2", script_path_trim, context_trim)
                    else:
                        render_template("regrade_trim.avs.j2", script_path_trim, context_trim)

                    script_dir = regrade_root_dir / "source_plain" / "target_hdr" / f"method_{method}" / post_folder
                    os.makedirs(script_dir, exist_ok=True)
                    script_path = script_dir / "regrade.avs"
                    base_import_path = rel_path_from_to(script_path, base_scripts_dir / "var_SOURCE.avs")
                    lut_rel = path_for_avs_from_script(combined_lut, script_path)
                    context = {
                        'base_path': base_import_path,
                        'source_name': 'plain',
                        'source_type': 'plain',
                        'target_name': 'hdr',
                        'applied_lut_path': lut_rel,
                        'post_tm': post
                    }
                    if post:
                        render_template("regrade_plain_hdr_post.avs.j2", script_path, context)
                    else:
                        render_template("regrade.avs.j2", script_path, context)

                    capture_dir = capture_regrade_dir / "source_plain" / "target_hdr" / f"method_{method}" / post_folder
                    os.makedirs(capture_dir, exist_ok=True)
                    capture_script_path = capture_dir / "capture.avs"
                    regrade_import_path = rel_path_from_to(capture_script_path, script_path_trim)
                    render_template("capture.avs.j2", capture_script_path,
                                    {"input_script": regrade_import_path, "frames": frames_list})

                    affix = f"source_plain_hdr_method_{method}_post_{post_folder}"
                    output_pattern = (screenshots_dir / f"%02d_{affix}.jpg").as_posix()
                    cmd = [
                        "ffmpeg",
                        "-i", capture_script_path.as_posix(),
                        "-fps_mode", "vfr",
                        "-frame_pts", "1",
                        "-f", "image2",
                        "-q:v", "2",
                        output_pattern
                    ]
                    record_cmd(cmd, step='capture',
                               desc=f"Capture regrade screenshots for plain->hdr method={method} lut={post_lut_base} post_tm={post or 'none'}")

                    if 'plain' not in capture_commands_by_source:
                        capture_commands_by_source['plain'] = []
                    capture_commands_by_source['plain'].append(commands_by_step['capture'][-1])

    capture_scripts_info = []
    for sv in source_variants:
        for variant in all_target_variants:
            if sv['name'] == 'plain' and variant != 'hdr':
                continue
            if variant == 'hdr' and sv['name'] != 'plain':
                continue
            for method in methods:
                for post in post_tms:
                    regrade_trim_script = get_regrade_trim_script_dir(regrade_trim_root_dir, sv['name'], variant, method, post) / "regrade.avs"
                    capture_dir = get_capture_regrade_script_dir(capture_regrade_dir, sv['name'], variant, method, post)
                    os.makedirs(capture_dir, exist_ok=True)
                    capture_script_path = capture_dir / "capture.avs"
                    regrade_import_path = rel_path_from_to(capture_script_path, regrade_trim_script)
                    render_template("capture.avs.j2", capture_script_path, {"input_script": regrade_import_path, "frames": frames_list})
                    capture_scripts_info.append((sv['name'], capture_script_path, "capture.avs"))

    for (src, capture_script_path, _) in capture_scripts_info:
        parts = capture_script_path.relative_to(capture_regrade_dir).parts
        if len(parts) >= 4:
            src_folder = parts[0]
            tgt_folder = parts[1]
            method_folder = parts[2]
            post_folder = parts[3]
            src_raw = src_folder.replace("source_", "", 1)
            tgt_raw = tgt_folder.replace("target_", "", 1)
            method_raw = method_folder.replace("method_", "", 1)
            post_raw = post_folder.replace("post_", "", 1) if post_folder != "no_post" else ""
        else:
            src_raw, tgt_raw, method_raw, post_raw = "unknown", "unknown", "unknown", "unknown"

        if post_raw:
            affix = f"source_{src_raw}_target_{tgt_raw}_method_{method_raw}_post_{post_raw}"
        else:
            affix = f"source_{src_raw}_target_{tgt_raw}_method_{method_raw}_nopost"
        output_pattern = (screenshots_dir / f"%02d_{affix}.jpg").as_posix()
        cmd = ["ffmpeg", "-i", capture_script_path.as_posix(), "-fps_mode", "vfr", "-frame_pts", "1", "-f", "image2", "-q:v", "2", output_pattern]
        record_cmd(cmd, step='capture', desc=f"Capture regrade screenshots for {src_raw}->{tgt_raw} {method_raw} post={post_raw or 'none'}")
        if src_raw not in capture_commands_by_source:
            capture_commands_by_source[src_raw] = []
        capture_commands_by_source[src_raw].append(commands_by_step['capture'][-1])

    for sv in source_variants:
        source_script = source_color_scripts[sv['name']]
        capture_dir = get_capture_source_script_dir(capture_source_dir, sv['name'])
        os.makedirs(capture_dir, exist_ok=True)
        capture_script_path = capture_dir / "capture.avs"
        source_import_path = rel_path_from_to(capture_script_path, source_script)
        render_template("capture.avs.j2", capture_script_path, {"input_script": source_import_path, "frames": frames_list})
        affix = f"source_{sv['name']}"
        output_pattern = (screenshots_dir / f"%02d_{affix}.jpg").as_posix()
        cmd = ["ffmpeg", "-i", capture_script_path.as_posix(), "-fps_mode", "vfr", "-frame_pts", "1", "-f", "image2", "-q:v", "2", output_pattern]
        record_cmd(cmd, step='capture_source', desc=f"Capture source after {sv['name']} (trimmed)")

    for variant in all_target_variants:
        if variant == 'plain':
            target_script = base_scripts_dir / "TARGET_TRIM_plain.avs"
        elif variant == 'hdr':
            target_script = base_scripts_dir / "TARGET_TRIM_hdr.avs"
        elif variant in target_tonemap_variants:
            target_script = base_scripts_dir / f"TARGET_TRIM_{variant}.avs"
        else:
            target_script = base_scripts_dir / f"TARGET_TRIM_{variant}.avs"
        capture_dir = get_capture_target_script_dir(capture_target_dir, variant)
        os.makedirs(capture_dir, exist_ok=True)
        capture_script_path = capture_dir / "capture.avs"
        target_import_path = rel_path_from_to(capture_script_path, target_script)
        render_template("capture.avs.j2", capture_script_path, {"input_script": target_import_path, "frames": frames_list})
        affix = f"target_{variant}"
        output_pattern = (screenshots_dir / f"%02d_{affix}.jpg").as_posix()
        cmd = ["ffmpeg", "-i", capture_script_path.as_posix(), "-fps_mode", "vfr", "-frame_pts", "1", "-f", "image2", "-q:v", "2", output_pattern]
        record_cmd(cmd, step='capture_target', desc=f"Capture target after {variant} (trimmed)")

    if combined_commands:
        if os.name == 'nt':
            commands_file = "commands.bat"
        else:
            commands_file = "commands.sh"
        combined_path = out_dir / commands_file
        with open(combined_path, 'w') as f:
            _write_script_header(f)
            for cmd in combined_commands:
                if os.name == 'nt':
                    cmd = cmd.replace('%', '%%')
                f.write(cmd + "\n")
        print(f"\n[INFO] Combined commands written to {combined_path}")

        step_order = [
            ('index', '01_index'),
            ('color', '02_extract'),
            ('extract_source', '02_extract_source'),
            ('extract_target', '02_extract_target'),
        ]
        for sv in source_variants:
            step_order.append((f'color_source_{sv["name"]}', f'02_extract_source_{sv["name"]}'))
        for variant in all_target_variants:
            step_order.append((f'color_target_{variant}', f'02_extract_target_{variant}'))
        step_order.extend([
            ('match_lut', '03_match_colors'),
            ('combine_lut', '04_combine_luts'),
            ('capture', '05_capture_regrade'),
            ('capture_source', '05_capture_source'),
            ('capture_target', '05_capture_target')
        ])

        for step_key, filename_base in step_order:
            cmds = commands_by_step.get(step_key, [])
            if not cmds:
                continue
            if os.name == 'nt':
                step_filename = out_dir / f"{filename_base}.bat"
            else:
                step_filename = out_dir / f"{filename_base}.sh"
            with open(step_filename, 'w') as f:
                _write_script_header(f)
                for cmd in cmds:
                    if os.name == 'nt':
                        cmd = cmd.replace('%', '%%')
                    f.write(cmd + "\n")
            if os.name != 'nt':
                os.chmod(step_filename, 0o755)
            print(f"[INFO] Step commands written to {step_filename}")

        for src, cmds in capture_commands_by_source.items():
            if not cmds:
                continue
            if os.name == 'nt':
                step_filename = out_dir / f"05_capture_regrade_{src}.bat"
            else:
                step_filename = out_dir / f"05_capture_regrade_{src}.sh"
            with open(step_filename, 'w') as f:
                _write_script_header(f)
                for cmd in cmds:
                    if os.name == 'nt':
                        cmd = cmd.replace('%', '%%')
                    f.write(cmd + "\n")
            if os.name != 'nt':
                os.chmod(step_filename, 0o755)
            print(f"[INFO] Step commands written to {step_filename}")

    print("\n" + "="*60)
    print("PREPARATION COMPLETE")
    print("All scripts and command files have been generated.")
    print(f"Generated files are in: {out_dir}")
    print("="*60)

if __name__ == "__main__":
    main()