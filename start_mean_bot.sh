#!/bin/bash

# 获取当前脚本所在目录的绝对路径
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 确保我们在项目根目录
cd "$ROOT_DIR"

# 强制指定 conda 虚拟环境的 Python 解释器路径
PYTHON_M="/Users/gsl/opt/anaconda3/envs/binance-quant/bin/python"

# 检查解释器是否存在
if [ ! -f "$PYTHON_M" ]; then
    echo "错误: 找不到 conda 虚拟环境的 Python 解释器 ($PYTHON_M)"
    echo "请确认是否已创建名为 binance-quant 的 conda 环境。"
    exit 1
fi

echo "=================================================="
echo "启动 均值回归策略交易机器人 (Mean Reversion Bot) V3.0"
echo "使用 Python 解释器: $PYTHON_M"
echo "工作目录: $ROOT_DIR"
echo "=================================================="

# 运行机器人
$PYTHON_M -m core.strategy_mean
