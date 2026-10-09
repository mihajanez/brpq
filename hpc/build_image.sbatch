#!/bin/bash
#SBATCH --job-name=brpq-image
#SBATCH --partition=dev
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --output=brpq-image-%j.out
# Build the Enroot (squashfs) image brpq jobs run in, on FRIDA's Pyxis/Enroot
# stack (FRIDA has no environment modules; everything runs in containers).
#
#   cd <base>/repo && sbatch hpc/build_image.sbatch cpu     # Aer on CPUs
#   cd <base>/repo && sbatch hpc/build_image.sbatch gpu     # + CuPy (CUDA 12)
#
# Writes <base>/images/brpq-<variant>.sqfs (override with BRPQ_IMAGES=dir).
# The GUI's HPC tab submits this for you ("Build CPU/GPU image").
set -euo pipefail
VARIANT="${1:-cpu}"
case "$VARIANT" in cpu|gpu) ;; *) echo "variant must be cpu or gpu" >&2; exit 2;; esac
REPO="${BRPQ_REPO:-$SLURM_SUBMIT_DIR}"
[ -f "$REPO/hpc/container_setup.sh" ] || { echo "run from the brpq repo directory" >&2; exit 2; }
IMAGES="${BRPQ_IMAGES:-$(dirname "$REPO")/images}"
BASE_IMAGE="${BRPQ_BASE_IMAGE:-ubuntu:24.04}"
mkdir -p "$IMAGES"
OUT="$IMAGES/brpq-$VARIANT.sqfs"
NEW="$IMAGES/.brpq-$VARIANT.$SLURM_JOB_ID.sqfs"
echo "building $OUT from $BASE_IMAGE on $(hostname)"

srun --container-image="$BASE_IMAGE" \
     --container-mounts="$REPO:/brpq-src:ro" \
     --container-save="$NEW" \
     env GUROBI_VERSION="${GUROBI_VERSION:-13.0.0}" \
     bash /brpq-src/hpc/container_setup.sh /brpq-src "$VARIANT"

mv -f "$NEW" "$OUT"            # replace the old image only after a good build
echo "image written: $OUT ($(du -h "$OUT" | cut -f1))"
