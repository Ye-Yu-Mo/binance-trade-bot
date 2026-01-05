#!/usr/bin/env python3
"""
期货策略回测脚本

使用方法：
    python run_backtest.py

配置说明：
    - 修改 start_date 和 end_date 设置回测时间范围
    - 修改 initial_balance 设置初始资金
    - 选择策略类（单币种或多币种）
"""
from binance.client import Client
from binance_trade_bot.config import Config
from binance_trade_bot.futures_backtest import BacktestEngine

# 策略导入
from binance_trade_bot.strategies.futures_risk_managed_strategy import Strategy as SingleCoinStrategy
from binance_trade_bot.strategies.futures_multi_coin_strategy import Strategy as MultiCoinStrategy


def main():
    print("=" * 60)
    print("📊 期货策略回测系统")
    print("=" * 60)

    # 1. 加载配置
    config = Config()

    # 2. 创建Binance客户端（用于下载历史数据）
    binance_client = Client(
        config.BINANCE_API_KEY,
        config.BINANCE_API_SECRET_KEY,
        tld=config.BINANCE_TLD,
        testnet=True,  # 使用testnet API（避免实盘API限制）
    )

    # 3. 选择策略
    print("\n请选择回测策略：")
    print("  1. 单币种策略 (BTCUSDT)")
    print("  2. 多币种策略 (BTC/ETH/BNB/SOL/ADA) [推荐]")
    choice = input("\n输入选择 (1 或 2，默认 2): ").strip() or "2"

    if choice == "1":
        strategy_class = SingleCoinStrategy
        print("\n✅ 选择了单币种策略")
    else:
        strategy_class = MultiCoinStrategy
        print("\n✅ 选择了多币种策略")

    # 4. 回测参数设置
    print("\n" + "-" * 60)
    print("回测参数配置")
    print("-" * 60)

    # 时间范围
    default_start = "2024-11-01"
    default_end = "2024-12-31"

    start_date = input(f"开始日期 (格式: YYYY-MM-DD，默认 {default_start}): ").strip() or default_start
    end_date = input(f"结束日期 (格式: YYYY-MM-DD，默认 {default_end}): ").strip() or default_end

    # 初始资金
    default_balance = "10000"
    initial_balance = float(input(f"初始资金 (USDT，默认 {default_balance}): ").strip() or default_balance)

    # 步进周期
    print("\n回测步进周期:")
    print("  1. 1小时 (快速，但可能错过短期机会)")
    print("  2. 15分钟 (推荐)")
    print("  3. 5分钟 (慢，但最接近实盘)")
    interval_choice = input("输入选择 (1/2/3，默认 2): ").strip() or "2"

    interval_map = {"1": "1h", "2": "15m", "3": "5m"}
    interval = interval_map.get(interval_choice, "15m")

    # 杠杆倍数
    default_leverage = "3"
    leverage = int(input(f"杠杆倍数 (1-10，默认 {default_leverage}): ").strip() or default_leverage)

    # 5. 确认
    print("\n" + "=" * 60)
    print("📋 回测配置确认")
    print("=" * 60)
    print(f"策略: {strategy_class.__name__}")
    print(f"时间范围: {start_date} ~ {end_date}")
    print(f"初始资金: ${initial_balance:.2f}")
    print(f"步进周期: {interval}")
    print(f"杠杆倍数: {leverage}x")
    print("=" * 60)

    confirm = input("\n确认开始回测? (输入 'yes' 确认): ").strip().lower()
    if confirm != 'yes':
        print("已取消回测")
        return

    # 6. 运行回测
    print("\n" + "=" * 60)
    print("🚀 开始回测...")
    print("=" * 60)
    print("⚠️  首次运行会下载历史数据，可能需要几分钟\n")

    engine = BacktestEngine(
        strategy_class=strategy_class,
        config=config,
        binance_client=binance_client
    )

    try:
        result = engine.run(
            start_date=start_date,
            end_date=end_date,
            initial_balance=initial_balance,
            interval=interval,
            leverage=leverage
        )

        print("\n✅ 回测完成！")
        print(f"历史数据已缓存到 ./backtest_data 目录")
        print(f"下次回测相同时间范围将直接使用缓存（秒级响应）")

    except KeyboardInterrupt:
        print("\n\n⚠️  回测被用户中断")
    except Exception as e:
        print(f"\n❌ 回测失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
