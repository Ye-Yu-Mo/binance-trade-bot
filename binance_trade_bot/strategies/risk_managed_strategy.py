"""
风险管理策略 - 带止损和止盈
基于 策略优化建议.md 的方案4
"""
from datetime import datetime
from binance_trade_bot.auto_trader import AutoTrader
from binance_trade_bot.models import PositionState


class Strategy(AutoTrader):
    def initialize(self):
        super().initialize()
        self.initialize_current_coin()
        # 移除内存字典，改用数据库 PositionState
        self.stop_loss_pct = 8.0  # 止损8%
        self.take_profit_pct = 12.0  # 止盈12%（降低盈亏平衡胜率从71%到40%）
        self.logger.info(f"风险管理策略已启动 - 止损:{self.stop_loss_pct}%, 止盈:{self.take_profit_pct}%")

    def scout(self):
        current_coin = self.db.get_current_coin()
        coin_pair = current_coin + self.config.BRIDGE
        current_price = self.manager.get_ticker_price(coin_pair)

        if current_price is None:
            self.logger.info(f"Skipping scouting... {coin_pair} price not found")
            return

        # 从数据库获取或创建仓位状态
        position = self.db.get_position_state(current_coin.symbol)

        if position is None:
            # 首次建仓（机器人重启或新币种）
            entry_price = current_price
            entry_time = datetime.now()

            position = PositionState(
                symbol=current_coin.symbol,
                entry_price=entry_price,
                entry_time=entry_time,
                highest_price=entry_price,
                stop_price=0.0,  # risk_managed 不用移动止损
                trail_active=False,
                atr=0.0,  # 不使用 ATR
                atr_pct=0.0,
                last_atr_update_time=entry_time,
            )
            self.db.save_position_state(position)
            self.logger.info(f"📝 建仓 {current_coin.symbol}: 入场价 {entry_price:.8f}")

        entry_price = position.entry_price

        # 计算盈亏百分比
        pnl_pct = (current_price - entry_price) / entry_price * 100

        # 输出当前状态
        self.logger.info(
            f"🔍 侦察 {current_coin.symbol}: 当前价 {current_price:.8f}, "
            f"入场价 {entry_price:.8f}, 盈亏 {pnl_pct:+.2f}%"
        )

        # 止损检查
        if pnl_pct <= -self.stop_loss_pct:
            self.logger.info(
                f"🛑 触发止损！{current_coin.symbol} 亏损 {pnl_pct:.2f}%, "
                f"入场价 {entry_price:.8f}, 当前价 {current_price:.8f}"
            )
            # 强制卖出，换回USDT
            result = self.manager.sell_alt(current_coin, self.config.BRIDGE)
            if result:
                self.db.delete_position_state(current_coin.symbol)
                self.db.set_current_coin(None)  # 清空 current_coin
                self.logger.info("止损后回到USDT，调用 bridge_scout 寻找新机会")
                # 调用 bridge_scout 重新买入
                self.bridge_scout()
            return

        # 止盈检查
        if pnl_pct >= self.take_profit_pct:
            self.logger.info(
                f"💰 触发止盈！{current_coin.symbol} 盈利 {pnl_pct:.2f}%, "
                f"入场价 {entry_price:.8f}, 当前价 {current_price:.8f}"
            )
            # 卖出回USDT，锁定利润
            result = self.manager.sell_alt(current_coin, self.config.BRIDGE)
            if result:
                self.db.delete_position_state(current_coin.symbol)
                self.db.set_current_coin(None)  # 清空 current_coin
                self.logger.info("止盈后回到USDT，调用 bridge_scout 寻找新机会")
                # 调用 bridge_scout 重新买入
                self.bridge_scout()
            return

        # 正常侦察（只有在未触发止损/止盈时才执行）
        self._jump_to_best_coin(current_coin, current_price)

    def transaction_through_bridge(self, pair):
        """
        重写交易方法，清理旧币并记录新币的入场价格
        """
        result = super().transaction_through_bridge(pair)

        if result is not None:
            # 删除旧币种的仓位状态（币种切换成功）
            self.db.delete_position_state(pair.from_coin.symbol)

            # 使用实际成交价格建仓（不是 ticker 价格！）
            entry_price = result.price  # 实际买入成交价
            entry_time = datetime.now()

            new_position = PositionState(
                symbol=pair.to_coin.symbol,
                entry_price=entry_price,
                entry_time=entry_time,
                highest_price=entry_price,
                stop_price=0.0,
                trail_active=False,
                atr=0.0,
                atr_pct=0.0,
                last_atr_update_time=entry_time,
            )
            self.db.save_position_state(new_position)
            self.logger.info(
                f"📝 建仓 {pair.to_coin.symbol}: 入场价 {entry_price:.8f} (实际成交价)"
            )

        return result

    def bridge_scout(self):
        """
        当持有 USDT 时，扫描并买入最优币种
        """
        bridge_balance = self.manager.get_currency_balance(self.config.BRIDGE.symbol)

        # 检查是否有足够的 USDT
        min_notional = 10.0  # Binance 最小交易额约 10 USDT
        if bridge_balance < min_notional:
            self.logger.warning(
                f"USDT 余额不足 ({bridge_balance:.2f} < {min_notional}), 无法买入新币种"
            )
            return None

        # 调用父类的 bridge_scout 找到最优币种
        new_coin = super().bridge_scout()

        if new_coin is not None:
            # 买入成功，建立仓位
            self.db.set_current_coin(new_coin)

            # 获取实际成交价并建仓
            # 注意：super().bridge_scout() 已经调用了 buy_alt，但没有返回 result
            # 我们需要用当前价格作为入场价（这是个妥协，但比没有好）
            coin_pair = new_coin + self.config.BRIDGE
            entry_price = self.manager.get_ticker_price(coin_pair)

            if entry_price:
                entry_time = datetime.now()
                new_position = PositionState(
                    symbol=new_coin.symbol,
                    entry_price=entry_price,
                    entry_time=entry_time,
                    highest_price=entry_price,
                    stop_price=0.0,
                    trail_active=False,
                    atr=0.0,
                    atr_pct=0.0,
                    last_atr_update_time=entry_time,
                )
                self.db.save_position_state(new_position)
                self.logger.info(
                    f"📝 bridge_scout 建仓 {new_coin.symbol}: 入场价 {entry_price:.8f}"
                )

        return new_coin

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

            # 检查是否真的持有这个币，如果没有就买入
            current_coin = self.db.get_current_coin()
            balance = self.manager.get_currency_balance(current_coin.symbol)

            if balance == 0 or balance is None:
                self.logger.info(f"账户中没有 {current_coin.symbol}，准备购买...")
                result = self.manager.buy_alt(current_coin, self.config.BRIDGE)
                if result:
                    self.logger.info(f"✅ 成功购买 {current_coin.symbol}，准备开始交易")
                else:
                    self.logger.error(f"❌ 购买 {current_coin.symbol} 失败！请检查余额和API权限")
            else:
                self.logger.info(f"账户中已有 {current_coin.symbol} ({balance:.8f})，跳过购买")
