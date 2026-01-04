"""
风险管理策略 - 带止损和止盈
基于 策略优化建议.md 的方案4
"""
from datetime import datetime
from binance_trade_bot.auto_trader import AutoTrader


class Strategy(AutoTrader):
    def initialize(self):
        super().initialize()
        self.initialize_current_coin()
        self.entry_prices = {}  # 记录买入价格
        self.stop_loss_pct = 8.0  # 止损8%（加密货币波动大，设置宽松一点）
        self.take_profit_pct = 20.0  # 止盈20%
        self.logger.info(f"风险管理策略已启动 - 止损:{self.stop_loss_pct}%, 止盈:{self.take_profit_pct}%")

    def scout(self):
        current_coin = self.db.get_current_coin()
        coin_pair = current_coin + self.config.BRIDGE
        current_price = self.manager.get_ticker_price(coin_pair)

        if current_price is None:
            self.logger.info(f"Skipping scouting... {coin_pair} price not found")
            return

        # 记录入场价格（如果还没记录）
        if current_coin.symbol not in self.entry_prices:
            self.entry_prices[current_coin.symbol] = current_price
            self.logger.info(f"📝 记录 {current_coin.symbol} 入场价格: {current_price:.8f}")

        entry_price = self.entry_prices[current_coin.symbol]

        # 计算盈亏百分比
        pnl_pct = (current_price - entry_price) / entry_price * 100

        # 止损检查
        if pnl_pct <= -self.stop_loss_pct:
            self.logger.info(
                f"🛑 触发止损！{current_coin.symbol} 亏损 {pnl_pct:.2f}%, "
                f"入场价 {entry_price:.8f}, 当前价 {current_price:.8f}"
            )
            # 强制卖出，换回USDT，不跳转到其他币
            result = self.manager.sell_alt(current_coin, self.config.BRIDGE)
            if result:
                del self.entry_prices[current_coin.symbol]
                # 等待一段时间再重新买入（避免立即反向交易）
                self.logger.info("止损后暂停交易，等待下次扫描")
            return

        # 止盈检查
        if pnl_pct >= self.take_profit_pct:
            self.logger.info(
                f"💰 触发止盈！{current_coin.symbol} 盈利 {pnl_pct:.2f}%, "
                f"入场价 {entry_price:.8f}, 当前价 {current_price:.8f}"
            )
            # 止盈后执行正常的跳转逻辑，寻找更好的币种
            self._jump_to_best_coin(current_coin, current_price)
            # 删除入场价，重置风险管理基准（即使跳转失败，也避免无限循环）
            self.entry_prices.pop(current_coin.symbol, None)
            return

        # 正常侦察（只有在未触发止损/止盈时才执行）
        self._jump_to_best_coin(current_coin, current_price)

    def transaction_through_bridge(self, pair):
        """
        重写交易方法，清理旧币并记录新币的入场价格
        """
        result = super().transaction_through_bridge(pair)

        if result is not None:
            # 删除旧币种的入场价格（币种切换成功）
            self.entry_prices.pop(pair.from_coin.symbol, None)

            # 记录新币种的入场价格
            new_coin_pair = pair.to_coin + self.config.BRIDGE
            new_price = self.manager.get_ticker_price(new_coin_pair)
            if new_price:
                self.entry_prices[pair.to_coin.symbol] = new_price
                self.logger.info(
                    f"📝 记录 {pair.to_coin.symbol} 新入场价格: {new_price:.8f}"
                )

        return result

    def initialize_current_coin(self):
        """
        初始化当前币种
        """
        import random
        import sys

        if self.db.get_current_coin() is None:
            current_coin_symbol = self.config.CURRENT_COIN_SYMBOL
            if not current_coin_symbol:
                current_coin_symbol = random.choice(self.config.SUPPORTED_COIN_LIST)

            self.logger.info(f"Setting initial coin to {current_coin_symbol}")

            if current_coin_symbol not in self.config.SUPPORTED_COIN_LIST:
                sys.exit(
                    "***\nERROR!\nSince there is no backup file, "
                    "a proper coin name must be provided at init\n***"
                )
            self.db.set_current_coin(current_coin_symbol)

            # 如果没有配置初始币种，购买随机选择的币种
            if self.config.CURRENT_COIN_SYMBOL == "":
                current_coin = self.db.get_current_coin()
                self.logger.info(f"Purchasing {current_coin} to begin trading")
                self.manager.buy_alt(current_coin, self.config.BRIDGE)
                self.logger.info("Ready to start trading with risk management")
