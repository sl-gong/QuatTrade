import pandas as pd
import numpy as np

def test_extreme_market():
    print("========================================")
    print("🛡️ 稳健策略极限风控（防飞刀）逻辑离线测试")
    print("========================================\n")
    
    # 模拟基础价格
    base_price = 60000.0
    
    # 1. 模拟数据：初期稳健上涨产生多头排列
    # 5分钟线 (用于 EMA20, EMA50) - 每根涨 10 刀
    prices_5m = [base_price + i * 10 for i in range(100)]
    
    # 1分钟线 (用于 MA7) - 缓慢上涨
    prices_1m = [base_price + i * 2 for i in range(100)]
    
    def calc_indicators(p1m, p5m):
        """核心指标计算（提取自 core/strategy.py 逻辑）"""
        df_1m = pd.DataFrame([{"close": p} for p in p1m])
        df_5m = pd.DataFrame([{"close": p} for p in p5m])
        
        # 1分钟 MA(7)
        ma7_seq = df_1m['close'].rolling(7).mean().values
        ma7_cur = ma7_seq[-1]
        ma7_prev = ma7_seq[-2] if len(ma7_seq) > 1 else ma7_cur
        
        # 5分钟 EMA(20) & EMA(50)
        ema20 = df_5m['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df_5m['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        
        trend_ok = ema20 > ema50
        momentum_ok = ma7_cur >= ma7_prev
        return ma7_cur, ma7_prev, ema20, ema50, trend_ok, momentum_ok

    # ---------------- 场景一：正常上涨 ----------------
    print("📊【场景 1：稳健上涨行情】")
    ma7_c, ma7_p, e20, e50, t_ok, m_ok = calc_indicators(prices_1m, prices_5m)
    print(f"5分钟线: EMA20={e20:.2f} | EMA50={e50:.2f} => 大级别多头: {t_ok}")
    print(f"1分钟线: 当前MA7={ma7_c:.2f} | 前值MA7={ma7_p:.2f} => 短期动能向上: {m_ok}")
    if t_ok and m_ok:
        print("🟢 综合判定: 指标健康，策略正常计算均线偏移量并【挂买单】。\n")
    
    # ---------------- 场景二：突发急跌 ----------------
    print("📊【场景 2：突发连续暴跌（防飞刀测试）】")
    print("现象: 1分钟内连续砸出大阴线，瞬间跌去 600 点")
    # 插入 3 根急跌 K 线
    for i in range(3):
        prices_1m.append(prices_1m[-1] - 200)
        
    ma7_c, ma7_p, e20, e50, t_ok, m_ok = calc_indicators(prices_1m, prices_5m)
    print(f"5分钟线: EMA20={e20:.2f} | EMA50={e50:.2f} => 大级别多头: {t_ok}")
    print(f"1分钟线: 当前MA7={ma7_c:.2f} | 前值MA7={ma7_p:.2f} => 短期动能向上: {m_ok}")
    if not m_ok:
        print("🔴 综合判定: 短期均线拐头向下 (动能衰竭)，即使属于5分钟多头趋势，系统也会【撤销所有买单，拒绝接盘】。\n")
        
    # ---------------- 场景三：大级别破位 ----------------
    print("📊【场景 3：持续阴跌导致大级别破位】")
    print("现象: 震荡下行多时，5分钟级别结构破坏")
    # 插入连续下跌的 5m 线
    for i in range(40):
        prices_5m.append(prices_5m[-1] - 50)
        
    ma7_c, ma7_p, e20, e50, t_ok, m_ok = calc_indicators(prices_1m, prices_5m)
    print(f"5分钟线: EMA20={e20:.2f} | EMA50={e50:.2f} => 大级别多头: {t_ok}")
    if not t_ok:
        print("🔴 综合判定: EMA20 跌破 EMA50 形成死叉。策略判定不再属于多头，【禁止任何买入底仓挂单】。\n")

    # ---------------- 场景四：单边暴涨踏空防御 ----------------
    print("📊【场景 4：单边暴涨/挂单偏离过大测试】")
    current_price = 61000.0
    first_buy_order_price = 60000.0
    max_drift_cfg = 0.005 # 0.5% (即超过 300 刀的偏离)
    
    drift = abs(current_price - first_buy_order_price) / first_buy_order_price
    print(f"当前市价: {current_price:.2f}")
    print(f"原接单价: {first_buy_order_price:.2f}")
    print(f"价格偏离率: {drift*100:.2f}% (阈值: {max_drift_cfg*100:.2f}%)")
    
    if drift > max_drift_cfg:
        print(f"🟡 综合判定: 偏离度 {drift*100:.2f}% > {max_drift_cfg*100:.2f} %，说明行情已经飞走。系统会【撤回过低的买单并按新 MA7 重新挂单】，防止后续骤跌时在错误的高位接针。")

if __name__ == '__main__':
    test_extreme_market()
