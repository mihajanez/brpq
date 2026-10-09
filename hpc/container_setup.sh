#!/bin/bash
# Installs everything brpq needs into a fresh Ubuntu 24.04 container. Run by
# hpc/build_image.sbatch inside `srun --container-image=ubuntu:24.04
# --container-save=...`, so the result is an Enroot squashfs image:
#   /opt/gurobi1300   Gurobi (C++ headers and libraries; licence NOT included)
#   /opt/venv         Python with Ocean, Qiskit, Aer (+ CuPy in the gpu variant)
#   build tools and Boost.program_options to compile ./rbrp_ip
#
# usage: container_setup.sh <repo dir> [cpu|gpu]
#   GUROBI_VERSION=13.0.0   Gurobi release to install (matches the Makefile)
set -euo pipefail
REPO="${1:?usage: container_setup.sh <repo dir> [cpu|gpu]}"
VARIANT="${2:-cpu}"
GUROBI_VERSION="${GUROBI_VERSION:-13.0.0}"
GUROBI_MAJOR="${GUROBI_VERSION%.*}"                     # 13.0
GUROBI_DIR="/opt/gurobi${GUROBI_VERSION//./}"           # /opt/gurobi1300
GUROBI_LIB="gurobi${GUROBI_MAJOR//./}"                  # gurobi130

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-dev build-essential \
    libboost-program-options-dev ca-certificates curl util-linux procps
rm -rf /var/lib/apt/lists/*

if [ ! -d "$GUROBI_DIR" ]; then
  echo "downloading Gurobi $GUROBI_VERSION"
  curl -fsSL "https://packages.gurobi.com/${GUROBI_MAJOR}/gurobi${GUROBI_VERSION}_linux64.tar.gz" \
    | tar -xz -C /opt
fi
# the Makefile expects /opt/gurobi1300/linux64; keep that path valid for any version
[ -e /opt/gurobi1300 ] || ln -s "$GUROBI_DIR" /opt/gurobi1300
echo "$GUROBI_DIR/linux64/lib" > /etc/ld.so.conf.d/gurobi.conf
ldconfig

python3 -m venv /opt/venv
/opt/venv/bin/pip install --no-cache-dir --upgrade pip
/opt/venv/bin/pip install --no-cache-dir -r "$REPO/hpc/requirements.txt"
if [ "$VARIANT" = "gpu" ]; then
  # CuPy with the CUDA 12 runtime from pip; the driver comes from the host
  /opt/venv/bin/pip install --no-cache-dir "cupy-cuda12x[ctk]"
fi

cat > /opt/brpq-image.env <<ENV
# sourced by step.sh in every brpq job
export GUROBI_HOME=$GUROBI_DIR/linux64
export GUROBI_LIBS="-lgurobi_g++8.5 -l$GUROBI_LIB"
export PATH=/opt/venv/bin:\$GUROBI_HOME/bin:\$PATH
export LD_LIBRARY_PATH=\$GUROBI_HOME/lib:\${LD_LIBRARY_PATH:-}
export BRPQ_IMAGE_VARIANT=$VARIANT
ENV
{
  echo "brpq image ($VARIANT) built $(date -u '+%Y-%m-%d %H:%M UTC')"
  echo "gurobi $GUROBI_VERSION in $GUROBI_DIR"
  /opt/venv/bin/python -c 'import qiskit, qiskit_aer, dimod; print("qiskit", qiskit.__version__, "| aer", qiskit_aer.__version__, "| dimod", dimod.__version__)'
} | tee /opt/brpq-image.txt
