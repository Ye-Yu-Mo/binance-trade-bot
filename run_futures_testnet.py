#!/usr/bin/env python3
"""
Binance 期货模拟盘测试启动脚本

使用方法：
    python run_futures_testnet.py

注意：
    - 需要先在 .env 或 config 中配置 Binance Testnet API 密钥
    - Testnet 地址: https://testnet.binancefuture.com
"""
import time
from binance_trade_bot.binance_futures_api_manager import BinanceFuturesAPIManager
from binance_trade_bot.config import Config
from binance_trade_bot.database import Database
from binance_trade_bot.logger import Logger
from binance_trade_bot.scheduler import SafeScheduler

# 策略导入
from binance_trade_bot.strategies.futures_risk_managed_strategy import Strategy as SingleCoinStrategy
from binance_trade_bot.strategies.futures_multi_coin_strategy import Strategy as MultiCoinStrategy


def main():
    # 使用独立的日志文件：logs/futures_trading.log
    logger = Logger(logging_service='futures_trading')
    logger.Logger.setLevel('DEBUG')  # 显示 DEBUG 日志
    logger.info("🚀 期货模拟盘启动中...")

    # 初始化配置和数据库
    config = Config()
    db = Database(logger, config)

    # 初始化期货API管理器（testnet=True）
    manager = BinanceFuturesAPIManager(config, db, logger, testnet=True)

    # 测试API连接
    try:
        balance = manager.get_usdt_balance()
        logger.info(f"✅ API连接成功 - USDT余额: {balance:.2f}")
    except Exception as e:
        logger.error(f"❌ 无法连接到Binance Futures Testnet API")
        logger.error(f"错误: {e}")
        logger.error("请检查:")
        logger.error("  1. API密钥是否正确（需要期货权限）")
        logger.error("  2. 是否使用的是 Testnet 密钥")
        logger.error("  3. 网络连接是否正常")
        return

    # 显示账户信息
    print("\n" + "="*60)
    print("📊 期货模拟盘账户信息")
    print("="*60)
    print(f"环境: Testnet (模拟盘)")
    print(f"USDT 余额: {balance:.2f}")

    # 查询当前持仓
    positions = manager.get_all_positions()
    if positions:
        print(f"\n当前持仓:")
        for pos in positions:
            print(f"  - {pos.symbol} {pos.side}: {pos.quantity:.6f} @ {pos.entry_price:.2f}")
            print(f"    未实现盈亏: ${pos.unrealized_profit:+.2f}")
    else:
        print(f"\n当前持仓: 空仓")

    print("="*60)

    # 确认启动
    confirmation = input("\n⚠️  确认开始期货自动交易吗? (输入 'yes' 确认): ").strip().lower()
    if confirmation != 'yes':
        logger.info("用户取消启动")
        print("已取消启动")
        return

    print("\n✅ 确认完成，开始运行期货策略...\n")
    logger.info(f"用户确认启动 - 余额: {balance:.2f} USDT")

    # 选择策略
    print("请选择策略：")
    print("  1. 单币种策略 (BTCUSDT, 基础动量)")
    print("  2. 多币种策略 (BTC/ETH/BNB/SOL/ADA, 智能扫描) [推荐]")
    strategy_choice = input("\n输入选择 (1 或 2，默认 2): ").strip() or "2"

    if strategy_choice == "1":
        strategy = SingleCoinStrategy(manager, db, logger, config)
        logger.info("✅ 使用单币种策略")
        print("\n✅ 单币种策略 - 交易 BTCUSDT\n")
    else:
        strategy = MultiCoinStrategy(manager, db, logger, config)
        logger.info("✅ 使用多币种策略")
        print("\n✅ 多币种策略 - 扫描 BTC/ETH/BNB/SOL/ADA\n")

    strategy.initialize()

    # 设置侦察任务
    scheduler = SafeScheduler(logger)
    # 期货策略改为5秒一次，避免API限流
    scheduler.every(5).seconds.do(strategy.scout).tag("scout")

    logger.info("✅ 期货策略已启动")
    print("✅ 期货策略运行中... (按 Ctrl+C 停止)")
    print(f"📈 止损: 5% | 止盈: 15% | 杠杆: 3x\n")

    # 运行循环
    try:
        while True:
            scheduler.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("收到停止信号，正在关闭...")
        print("\n\n👋 策略已停止")


if __name__ == "__main__":
    main()
