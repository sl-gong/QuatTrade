# Binance 量化交易 Python 环境

## 1) 激活环境

```bash
conda activate binance-quant
```

## 2) 准备配置

```bash
cp .env.example .env
```

然后把 `.env` 里的 `BINANCE_API_KEY`、`BINANCE_API_SECRET` 改成你自己的。

> 建议先用 `BINANCE_BASE_URL=https://testnet.binance.vision` 在测试网验证策略。

## 3) 运行稳健版量化策略

我们提供了一个支持 WebSocket 和多交易对环境配置的稳健版做市策略，详见 `TradeRules_Gemini.md`。

```bash
python run_bot.py
```

### 策略说明
- **趋势过滤**：基于 `5m` 的 EMA20 和 EMA50，以及 `1m` 的 MA7。
- **自动挂单**：自动挂两档试探仓和防守仓。
- **动态止盈止损**：动态拉价均摊止盈，1% 强制市价止损，10分钟未成交自动调整为保本单。
- **日志分析**：所有交易判定过程会输出到 `logs/trade_bot.log` 中。

## 4) 复现环境（可选）

如果你在别的机器上，可以使用：

```bash
conda env create -f environment.yml
conda activate binance-quant
```
