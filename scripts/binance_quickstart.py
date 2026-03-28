import os
from dotenv import load_dotenv
import pandas as pd
from binance.spot import Spot


def create_client() -> Spot:
    load_dotenv()
    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")
    base_url = os.getenv("BINANCE_BASE_URL", "https://testnet.binance.vision")

    if api_key and api_secret:
        return Spot(api_key=api_key, api_secret=api_secret, base_url=base_url)
    return Spot(base_url=base_url)


def fetch_klines(client: Spot, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    raw = client.klines(symbol=symbol, interval=interval, limit=limit)
    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_asset_volume",
        "num_trades",
        "taker_buy_base",
        "taker_buy_quote",
        "ignore",
    ]
    df = pd.DataFrame(raw, columns=columns)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def main() -> None:
    symbol = os.getenv("SYMBOL", "BTCUSDC")
    interval = os.getenv("INTERVAL", "1m")

    client = create_client()

    server_time = client.time()
    print("Server time:", server_time)

    df = fetch_klines(client, symbol=symbol, interval=interval, limit=200)
    df["sma_fast"] = df["close"].rolling(20).mean()
    df["sma_slow"] = df["close"].rolling(50).mean()

    last = df.iloc[-1]
    signal = "HOLD"
    if pd.notna(last["sma_fast"]) and pd.notna(last["sma_slow"]):
        if last["sma_fast"] > last["sma_slow"]:
            signal = "LONG"
        elif last["sma_fast"] < last["sma_slow"]:
            signal = "SHORT"

    print(f"Latest close: {last['close']:.2f}")
    print(f"SMA20: {last['sma_fast']:.2f}, SMA50: {last['sma_slow']:.2f}")
    print(f"Signal: {signal}")


if __name__ == "__main__":
    main()
