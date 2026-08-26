# Restricted BRP solver

## Requirements

* [Boost (Boost.program_options)](https://www.boost.org/)
* [Gurobi Optimizer](https://www.gurobi.com/)

## Usage

For CV instances:

    rbrp_ip -E 2 data3-3-1.dat (two additional empty tiers)
    rbrp_ip data3-3-1.dat (no height limit)

For ZQLZ instances:

    rbrp_ip -E 0 00001.txt

For other options, type "rbrp_ip -h".
