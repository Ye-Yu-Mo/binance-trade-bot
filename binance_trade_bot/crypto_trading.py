#!python3
import time

from .binance_api_manager import BinanceAPIManager
from .config import Config
from .database import Database
from .logger import Logger
from .scheduler import SafeScheduler
from .strategies import get_strategy


def main():
    logger = Logger()
    logger.info("Starting")

    config = Config()
    db = Database(logger, config)
    manager = BinanceAPIManager(config, db, logger, config.TESTNET)
    # check if we can access API feature that require valid config
    try:
        account = manager.get_account()
    except Exception as e:  # pylint: disable=broad-except
        logger.error("Couldn't access Binance API - API keys may be wrong or lack sufficient permissions")
        logger.error(e)
        return

    # Get account balance for confirmation
    try:
        bridge_balance = manager.get_currency_balance(config.BRIDGE.symbol)
        total_btc_value = 0
        total_usdt_value = 0

        # Calculate total account value
        for balance in account['balances']:
            asset = balance['asset']
            free = float(balance['free'])
            locked = float(balance['locked'])
            total = free + locked

            if total > 0:
                if asset == 'BTC':
                    total_btc_value += total
                elif asset == 'USDT':
                    total_usdt_value += total
                elif asset == config.BRIDGE.symbol:
                    bridge_balance = total
                else:
                    # Try to get BTC value
                    try:
                        price = manager.get_ticker_price(asset + 'BTC')
                        if price:
                            total_btc_value += total * price
                    except:
                        pass

        # Convert BTC to USDT for display
        try:
            btc_price = manager.get_ticker_price('BTCUSDT')
            if btc_price:
                total_usdt_value += total_btc_value * btc_price
        except:
            pass

        # Display account information
        print("\n" + "="*60)
        print("交易账户信息")
        print("="*60)
        print(f"桥接币种: {config.BRIDGE.symbol}")
        print(f"桥接币余额: {bridge_balance:.8f} {config.BRIDGE.symbol}")
        if total_usdt_value > 0:
            print(f"账户总价值(USDT): ~{total_usdt_value:.2f} USDT")
        print(f"交易策略: {config.STRATEGY}")
        print(f"侦察倍数: {config.SCOUT_MULTIPLIER}")
        print(f"支持币种: {', '.join(config.SUPPORTED_COIN_LIST[:5])}..." if len(config.SUPPORTED_COIN_LIST) > 5 else f"支持币种: {', '.join(config.SUPPORTED_COIN_LIST)}")
        print("="*60)

        # Confirmation prompt
        confirmation = input("\n⚠️  确认开始自动交易吗? (输入 'yes' 确认): ").strip().lower()
        if confirmation != 'yes':
            logger.info("用户取消启动")
            print("已取消启动")
            return

        print("\n✅ 确认完成，开始运行交易策略...\n")
        logger.info(f"User confirmed. Starting with {bridge_balance:.8f} {config.BRIDGE.symbol}")

    except Exception as e:
        logger.warning(f"Could not get account balance: {e}")
        logger.info("Proceeding without balance confirmation")

    strategy = get_strategy(config.STRATEGY)
    if strategy is None:
        logger.error("Invalid strategy name")
        return
    trader = strategy(manager, db, logger, config)
    logger.info(f"Chosen strategy: {config.STRATEGY}")

    logger.info("Creating database schema if it doesn't already exist")
    db.create_database()

    db.set_coins(config.SUPPORTED_COIN_LIST)
    db.migrate_old_state()

    trader.initialize()

    schedule = SafeScheduler(logger)
    schedule.every(config.SCOUT_SLEEP_TIME).seconds.do(trader.scout).tag("scouting")
    schedule.every(1).minutes.do(trader.update_values).tag("updating value history")
    schedule.every(1).minutes.do(db.prune_scout_history).tag("pruning scout history")
    schedule.every(1).hours.do(db.prune_value_history).tag("pruning value history")
    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    finally:
        manager.stream_manager.close()