#!/bin/bash
#SBATCH --job-name=bps
#SBATCH --output=logs/%x_%A_%a.out
#SBATCH --error=logs/%x_%A_%a.err
#SBATCH -n 32
#SBATCH --mem=64G
#SBATCH --time=23:30:00
#SBATCH --partition=compute
#
# One Slurm array task per experiment run.
#
#     DATASET=production sbatch --array=0-11%2 run_array.sh    # Morris runs
#     DATASET=datamining sbatch --array=0-11%2 run_array.sh
#
# Run numbers come from:
#
#     python run_experiments.py --dataset production --list
#
# The pipeline calls joblib with n_jobs=-5 -- "every core on the machine minus
# four" -- counted from the node rather than from the allocation. COMA does not
# enforce CPU limits, so -n 32 is a courtesy signal rather than a cap. Keep the
# %N throttle low: two concurrent tasks on a shared cluster is polite, six is
# not.
#
# No --gres on purpose: Prosimos is a sequential discrete-event simulator and
# nothing here touches CUDA. Requesting a GPU leaves it idle and lengthens the
# queue for everyone.

set -euo pipefail

# logs/ must exist before submitting. Slurm opens the --output file before this
# script runs, and a missing directory makes the job fail with no log to say why.
cd "$SLURM_SUBMIT_DIR/../../backend"
mkdir -p "$SLURM_SUBMIT_DIR/logs"

export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export PYTHONIOENCODING=utf-8

source ~/miniforge3/etc/profile.d/conda.sh
conda activate bps

echo "host      : $(hostname)"
echo "array job : $SLURM_ARRAY_JOB_ID  task $SLURM_ARRAY_TASK_ID"
echo "dataset   : ${DATASET:?set DATASET=production or DATASET=datamining}"
echo "cores (-n): ${SLURM_NTASKS:-unset}"
echo "started   : $(date)"
echo

python run_experiments.py --dataset "$DATASET" --index "$SLURM_ARRAY_TASK_ID"

echo
echo "finished  : $(date)"
