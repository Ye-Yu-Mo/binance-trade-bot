"""
现货回测主脚本 - Risk Managed 策略

用法：
    python run_spot_backtest_risk.py
"""
from datetime import datetime
from binance.client import Client
from binance_trade_bot.config import Config
from binance_trade_bot.spot_backtest import SpotBacktestEngine
from binance_trade_bot.strategies.risk_managed_strategy import Strategy as RiskManagedStrategy


def main():
    # 1. 加载配置
    config = Config()

    # 确保配置正确
    print("配置信息:")
    print(f"  桥币: {config.BRIDGE.symbol}")
    print(f"  支持币种: {config.SUPPORTED_COIN_LIST}")
    print(f"  策略: risk_managed")
    print()

    # 2. 创建 Binance 客户端（用于下载历史数据）
    # 注意：如果已经有缓存数据，可以设置为 None
    binance_client = Client(config.BINANCE_API_KEY, config.BINANCE_API_SECRET_KEY)

    # 3. 创建回测引擎
    engine = SpotBacktestEngine(
        strategy_class=RiskManagedStrategy,
        config=config,
        binance_client=binance_client  # 如果有缓存数据，可以设为 None
    )

    # 4. 运行回测
    start_time = datetime.now()

    api_manager = engine.run(
        start_date='2023-01-01',
        end_date='2025-12-31',
        initial_balance=10000.0,  # 初始资金 10000 USDT
        interval='5m'             # 5分钟步进
    )

    end_time = datetime.now()
    elapsed = (end_time - start_time).total_seconds()

    print(f"\n⏱️  回测耗时: {elapsed:.2f} 秒")

    # 5. 输出最终持仓
    print("\n" + "=" * 60)
    print("最终账户状态")
    print("=" * 60)
    for symbol, balance in api_manager.balances.items():
        if balance > 0:
            print(f"{symbol}: {balance:.8f}")
    print("=" * 60)


if __name__ == '__main__':
    main()
