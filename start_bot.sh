#!/usr/bin/env bash
set -euo pipefail

ENV_PYTHON="/Users/gsl/opt/anaconda3/envs/binance-quant/bin/python"
PROJECT_DIR="/Users/gsl/work/AI/quatTrade"

cd "$PROJECT_DIR"
"$ENV_PYTHON" run_bot.py
