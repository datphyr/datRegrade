#!/bin/bash
# Example: a fast, narrow datRegrade run.
#
# The full default matrix is ~1000 pipelines. This narrows it to two
# tonemappers and three matching methods so a first look at the results does
# not take all night. Generated files land in REGRADES/quick/.
#
# This only *prepares* the project; it does not execute anything.

set -e
cd "$(dirname "$0")/.."

python auto_regrade.py \
  --source "SRC/hdr-source.mkv" \
  --target "SRC/sdr-reference.mkv" \
  --frames "1340,11337,14451,16571" \
  --source-crop "0,42,0,-42" \
  --target-crop "0,20,-2,-22" \
  --tonemapping "bt2390,spline" \
  --methods "rgb-1d,rgb-3d-emd,rgb-3d-idt" \
  --output-dir "quick"

echo
echo "Now run the steps in order, e.g.:"
echo "  cd REGRADES/quick && ./01_index.sh && ./02_extract_source.sh"
