#!/bin/bash
#SBATCH --job-name=prestudy
#SBATCH --output=logs/%x_%j.out
#SBATCH --error=logs/%x_%j.err
#SBATCH -n 32
#SBATCH --mem=64G
#SBATCH --time=06:00:00
#SBATCH --partition=compute
#
# Pre-study: how many cases and replications a simulation needs.
#
#     DATASET=production sbatch run_prestudy.sh
#     DATASET=datamining sbatch run_prestudy.sh
#
# One job, not an array: the pipeline sweeps all twelve case counts and five
# replications in a single call. About 15k simulations, estimated ~30 min.
#
# The 6 h limit is deliberate headroom -- the estimate comes from a 3000-case
# measurement and the sweep goes up to 7000.

set -euo pipefail

cd "$SLURM_SUBMIT_DIR/../../backend"
mkdir -p "$SLURM_SUBMIT_DIR/logs"

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export PYTHONIOENCODING=utf-8

source ~/miniforge3/etc/profile.d/conda.sh
conda activate bps

echo "host    : $(hostname)"
echo "job     : $SLURM_JOB_ID"
echo "cores   : ${SLURM_NTASKS:-unset}"
echo "started : $(date)"
echo

# CASES lets a partial sweep be submitted, e.g. to finish a case count that
# ran out of wall time:
#     DATASET=production CASES="7000" sbatch --time=03:00:00 run_prestudy.sh
if [ -n "${CASES:-}" ]; then
    python run_prestudy.py --dataset "${DATASET:-production}" --cases $CASES
else
    python run_prestudy.py --dataset "${DATASET:-production}"
fi

echo
echo "finished: $(date)"
