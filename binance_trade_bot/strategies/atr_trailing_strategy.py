from datetime import datetime, timedelta
from typing import Optional, List, Tuple

from binance_trade_bot.auto_trader import AutoTrader
from binance_trade_bot.models import PositionState


class Strategy(AutoTrader):
    def initialize(self):
        super().initialize()

        # 验证 manager.datetime 支持时间运算
        if not hasattr(self.manager, 'datetime'):
            raise RuntimeError("manager 缺少 datetime 属性，ATR 策略无法运行")
        test_time = self.manager.datetime
        try:
            _ = test_time - timedelta(seconds=1)
        except (TypeError, AttributeError) as e:
            raise RuntimeError(
                f"manager.datetime 类型 {type(test_time).__name__} 不支持 timedelta 运算 ({e})，"
                "ATR 策略需要时间计算功能"
            )

        self.initialize_current_coin()

        # ATR 失败告警
        self.atr_failure_count = 0
        self.last_atr_alert_time = None
        self.atr_failure_threshold = 10  # 连续失败10次告警
        self.atr_alert_interval = timedelta(hours=1)  # 每小时最多告警一次

        # ATR 参数
        self.atr_timeframe = "1h"     # 你也可以改成 "4h"
        self.atr_period = 14
        self.atr_lookback = 20        # ATR(14) 只需要 ~15-20 根，不要浪费 API 配额

        # 风控系数（用 ATR 为单位）
        self.k_initial_stop = 2.0     # 初始止损：entry - 2*ATR
        self.k_be_trigger = 1.5       # 达到 1.5*ATR 盈利，提到保本
        self.k_trail_dist = 1.5       # 移动止损距离：highest - 1.5*ATR

        # 时间止损（可选）
        self.max_hold_hours = 24
        self.time_stop_grace_k = 0.5  # 持仓超过 max_hold_hours 且 pnl < 0.5*ATR（收益不足）就走人

        # 成本缓冲（你必须按自己账户改）
        # 简陋但比你现在“完全不算成本”强：保本止损至少覆盖手续费和滑点
        self.fee_buffer_pct = 0.2     # 双边手续费+滑点粗略缓冲 0.2%（按你真实情况调）

        # ATR 更新节奏：别每次 scout 都算一遍
        self.atr_update_interval = timedelta(minutes=30)

        self.logger.info(
            "ATR 风控策略启动："
            f"timeframe={self.atr_timeframe}, ATR({self.atr_period}), "
            f"stop={self.k_initial_stop}ATR, be_trigger={self.k_be_trigger}ATR, trail={self.k_trail_dist}ATR, "
            f"max_hold={self.max_hold_hours}h"
        )

    # ---------------------------
    # 工具函数
    # ---------------------------
    def make_pair(self, coin) -> str:
        """
        统一处理交易对拼接，不管 coin 是对象还是字符串
        """
        base = coin.symbol if hasattr(coin, "symbol") else str(coin)
        bridge = self.config.BRIDGE.symbol if hasattr(self.config.BRIDGE, "symbol") else str(self.config.BRIDGE)
        return base + bridge

    def extract_real_entry_info(self, order) -> Optional[Tuple[float, datetime]]:
        """
        从订单中提取真实成交均价和成交时间
        返回 (real_price, real_time) 或 None
        """
        if order is None:
            self.logger.warning("订单对象为 None，无法提取真实成交信息")
            return None

        try:
            # 检查是否有成交数量和成交金额
            if hasattr(order, 'cumulative_filled_qty') and hasattr(order, 'cumulative_quote_qty'):
                filled_qty = order.cumulative_filled_qty
                quote_qty = order.cumulative_quote_qty

                if filled_qty > 0:
                    real_price = quote_qty / filled_qty

                    # 提取并验证时间
                    real_time = order.time if hasattr(order, 'time') else self.manager.datetime
                    if not isinstance(real_time, datetime):
                        self.logger.warning(
                            f"订单时间类型错误: {type(real_time).__name__}，使用 manager.datetime"
                        )
                        real_time = self.manager.datetime

                    return (real_price, real_time)
                else:
                    self.logger.warning(f"订单成交数量为0 (filled_qty={filled_qty})，无法计算真实成交价")
            else:
                self.logger.warning(
                    f"订单对象缺少必要字段 "
                    f"(has cumulative_filled_qty: {hasattr(order, 'cumulative_filled_qty')}, "
                    f"has cumulative_quote_qty: {hasattr(order, 'cumulative_quote_qty')})"
                )
        except Exception as e:
            self.logger.warning(f"提取真实成交信息失败: {e}")

        return None

    # ---------------------------
    # 历史K线获取 - 直接用 Binance API，不猜测
    # ---------------------------
    def fetch_klines(self, coin_pair: str, interval: str, limit: int) -> Optional[List[Tuple[float, float, float]]]:
        """
        直接调用 Binance API 获取历史 K 线数据
        返回 [(high, low, close), ...] 或 None（失败时）
        """
        try:
            # Binance API 返回格式：
            # [
            #   [open_time, open, high, low, close, volume, close_time, ...],
            #   ...
            # ]
            klines = self.manager.binance_client.get_klines(
                symbol=coin_pair,
                interval=interval,
                limit=limit
            )

            if not klines or len(klines) < self.atr_period + 1:
                self.logger.error(
                    f"K线数据不足: {coin_pair} {interval}, "
                    f"需要 {self.atr_period + 1} 根，实际 {len(klines) if klines else 0} 根"
                )
                return None

            # 提取 (high, low, close)
            result = []
            for kline in klines:
                high = float(kline[2])
                low = float(kline[3])
                close = float(kline[4])
                result.append((high, low, close))

            return result

        except Exception as e:
            self.logger.error(f"获取 K线数据失败 ({coin_pair}, {interval}, {limit}): {e}")
            return None

    # ---------------------------
    # ATR 计算
    # ---------------------------
    def compute_atr(self, klines: List[Tuple[float, float, float]], period: int) -> float:
        """
        简单 ATR（Wilder 平滑也行，但这版够用且不复杂化自虐）
        """
        trs: List[float] = []
        prev_close = klines[0][2]
        for high, low, close in klines[1:]:
            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )
            trs.append(tr)
            prev_close = close

        if len(trs) < period:
            return 0.0

        # 用最近 period 根 TR 的均值
        recent = trs[-period:]
        return sum(recent) / period

    def get_atr_info(self, coin_pair: str, current_price: float) -> Tuple[Optional[float], Optional[float]]:
        """
        返回 (atr, atr_pct)，失败返回 (None, None)
        不要猜默认值，没数据就明确返回 None
        """
        klines = self.fetch_klines(coin_pair, self.atr_timeframe, self.atr_lookback)
        if not klines:
            self.logger.error(f"无法获取 {coin_pair} K线数据，ATR 计算失败")
            self._handle_atr_failure(coin_pair)
            return None, None

        atr = self.compute_atr(klines, self.atr_period)
        if atr <= 0:
            self.logger.error(f"{coin_pair} ATR 计算结果异常 (atr={atr})，拒绝使用")
            self._handle_atr_failure(coin_pair)
            return None, None

        # ATR 获取成功，重置失败计数器
        self.atr_failure_count = 0

        atr_pct = (atr / current_price * 100.0) if current_price > 0 else 0.0
        return atr, atr_pct

    def _handle_atr_failure(self, coin_pair: str):
        """处理 ATR 获取失败：计数并在达到阈值时告警"""
        self.atr_failure_count += 1

        if self.atr_failure_count >= self.atr_failure_threshold:
            # 检查是否需要发送告警（避免刷屏）
            should_alert = (
                self.last_atr_alert_time is None or
                self.manager.datetime - self.last_atr_alert_time >= self.atr_alert_interval
            )

            if should_alert:
                self.logger.error(
                    f"🚨 ATR 获取连续失败 {self.atr_failure_count} 次！"
                    f"最后失败币种: {coin_pair}。"
                    f"请检查：(1) 网络连接 (2) Binance API 状态 (3) API 限频问题。"
                    f"策略将拒绝新建仓位，直到 ATR 恢复正常。"
                )
                self.last_atr_alert_time = self.manager.datetime

    # ---------------------------
    # 仓位状态管理
    # ---------------------------
    def ensure_position_state(self, symbol: str, coin_pair: str, current_price: float) -> Optional[PositionState]:
        """
        获取或创建仓位状态（从数据库）
        返回 None 表示无法建仓（ATR 数据缺失或无效）
        """
        # 从数据库查询现有仓位
        st = self.db.get_position_state(symbol)

        if st is not None:
            # 定期更新 ATR（别每次 scout 都算）
            if self.manager.datetime - st.last_atr_update_time >= self.atr_update_interval:
                atr, atr_pct = self.get_atr_info(coin_pair, current_price)
                if atr is None:
                    self.logger.warning(f"{symbol} ATR 更新失败，保留旧值 ATR={st.atr:.8f}")
                else:
                    st.atr = atr
                    st.atr_pct = atr_pct
                    st.last_atr_update_time = self.manager.datetime
                    self.db.save_position_state(st)
                    self.logger.debug(f"{symbol} ATR 更新: {atr:.8f} ({atr_pct:.2f}%)")
            return st

        # 新建仓位状态：使用当前 ticker 价格
        # 注意：真实成交价应该在 transaction_through_bridge 时就已经建仓
        # 如果走到这里，说明是程序重启后首次 scout，使用 ticker fallback
        entry_price = current_price
        entry_time = self.manager.datetime

        self.logger.warning(
            f"⚠️  {symbol} 在数据库中没有仓位记录，使用 ticker 价格 {entry_price:.8f} 建仓 "
            f"(可能是程序重启后首次 scout，或切币逻辑未正确建仓)"
        )

        atr, atr_pct = self.get_atr_info(coin_pair, entry_price)
        if atr is None or atr <= 0:
            self.logger.error(f"{symbol} ATR 数据不可用，拒绝建仓")
            return None

        stop_price = entry_price - self.k_initial_stop * atr

        st = PositionState(
            symbol=symbol,
            entry_price=entry_price,
            entry_time=entry_time,
            highest_price=entry_price,
            stop_price=stop_price,
            trail_active=False,
            atr=atr,
            atr_pct=atr_pct,
            last_atr_update_time=self.manager.datetime,
        )
        self.db.save_position_state(st)

        self.logger.info(
            f"🧱 建仓 {symbol}: entry={entry_price:.8f}, ATR={atr:.8f} ({atr_pct:.2f}%), "
            f"初始止损={stop_price:.8f} (entry - {self.k_initial_stop}*ATR)"
        )
        return st

    def clear_position_state(self, symbol: str):
        """从数据库删除仓位状态"""
        self.db.delete_position_state(symbol)

    # ---------------------------
    # 核心：风控检查
    # ---------------------------
    def update_trailing_stop(self, st: PositionState, current_price: float):
        """
        更新移动止损状态（有副作用的函数）
        职责：更新 highest_price, stop_price, trail_active
        """
        state_changed = False

        # 更新最高价
        if current_price > st.highest_price:
            st.highest_price = current_price
            state_changed = True

        pnl = current_price - st.entry_price

        # 阶段1：盈利达到阈值 -> 激活保本止损
        be_trigger = self.k_be_trigger * st.atr
        if (not st.trail_active) and (pnl >= be_trigger):
            cost_buffer = st.entry_price * (self.fee_buffer_pct / 100.0)
            be_stop = st.entry_price + cost_buffer
            if be_stop > st.stop_price:
                st.stop_price = be_stop
            st.trail_active = True
            state_changed = True
            self.logger.info(
                f"🟦 保本止损激活 {st.symbol}: pnl={pnl:.8f} >= trigger={be_trigger:.8f} ({self.k_be_trigger}*ATR), "
                f"止损提至 {st.stop_price:.8f} (含成本缓冲 {self.fee_buffer_pct:.2f}%)"
            )

        # 阶段2：移动止损（只上移不下移）
        if st.trail_active:
            trail_stop = st.highest_price - self.k_trail_dist * st.atr
            if trail_stop > st.stop_price:
                st.stop_price = trail_stop
                state_changed = True
                self.logger.info(
                    f"🟩 移动止损上移 {st.symbol}: highest={st.highest_price:.8f}, "
                    f"止损 -> {st.stop_price:.8f}"
                )

        # 状态有变化，提交到数据库
        if state_changed:
            self.db.save_position_state(st)

    def should_exit(self, st: PositionState, current_price: float) -> Optional[str]:
        """
        纯函数：只检查是否应该退出，不修改状态
        返回退出原因字符串，否则 None
        """
        # 硬退出：触发止损
        if current_price <= st.stop_price:
            return f"STOP (price={current_price:.8f} <= stop={st.stop_price:.8f})"

        # 时间止损（可选）
        hold_time = self.manager.datetime - st.entry_time
        if hold_time >= timedelta(hours=self.max_hold_hours):
            pnl = current_price - st.entry_price
            if pnl < self.time_stop_grace_k * st.atr:
                return f"TIME (持仓{hold_time}，pnl={pnl:.8f} < {self.time_stop_grace_k}*ATR)"

        return None

    # ---------------------------
    # 交易主循环
    # ---------------------------
    def scout(self):
        current_coin = self.db.get_current_coin()
        coin_pair = self.make_pair(current_coin)
        current_price = self.manager.get_ticker_price(coin_pair)

        # 价格验证：None 或 <= 0 都拒绝
        if current_price is None or current_price <= 0:
            self.logger.warning(f"价格无效: {coin_pair} price={current_price}，跳过本轮")
            return

        st = self.ensure_position_state(
            current_coin.symbol if hasattr(current_coin, 'symbol') else str(current_coin),
            coin_pair,
            current_price
        )
        if st is None:
            coin_symbol = current_coin.symbol if hasattr(current_coin, 'symbol') else str(current_coin)
            self.logger.error(
                f"无法建立 {coin_symbol} 仓位状态（ATR 数据缺失），跳过本轮。"
                "如果持续出现，请检查 K线接口或网络连接"
            )
            return

        # 先更新移动止损状态
        self.update_trailing_stop(st, current_price)

        # 再检查是否应该退出
        reason = self.should_exit(st, current_price)
        if reason:
            coin_symbol = current_coin.symbol if hasattr(current_coin, 'symbol') else str(current_coin)
            self.logger.info(f"🧯 退出 {coin_symbol}: {reason}")
            result = self.manager.sell_alt(current_coin, self.config.BRIDGE)
            if result:
                self.clear_position_state(coin_symbol)
                self.logger.info("已卖出，回到桥接币，等待下次扫描")
            return

        # 没触发退出：照常跳到更优币
        self._jump_to_best_coin(current_coin, current_price)

    def transaction_through_bridge(self, pair):
        """
        切币时：清理旧仓位，立即创建并持久化新仓位
        """
        result = super().transaction_through_bridge(pair)

        if result is not None:
            # 清旧仓位
            from_symbol = pair.from_coin.symbol if hasattr(pair.from_coin, 'symbol') else str(pair.from_coin)
            self.clear_position_state(from_symbol)
            self.logger.info(f"已清理 {from_symbol} 仓位状态")

            # 立即创建新仓位并持久化
            to_symbol = pair.to_coin.symbol if hasattr(pair.to_coin, 'symbol') else str(pair.to_coin)
            to_coin_pair = self.make_pair(pair.to_coin)

            # 提取真实成交信息
            real_entry_info = self.extract_real_entry_info(result)

            if real_entry_info:
                real_price, real_time = real_entry_info
                ticker_price = self.manager.get_ticker_price(to_coin_pair)
                ticker_diff_pct = abs(real_price - ticker_price) / ticker_price * 100 if ticker_price > 0 else 0
                self.logger.info(
                    f"✅ 提取到 {to_symbol} 真实成交信息: price={real_price:.8f}, time={real_time.isoformat()}, "
                    f"ticker={ticker_price:.8f}, diff={ticker_diff_pct:.2f}%"
                )

                # 获取 ATR 并立即建仓
                atr, atr_pct = self.get_atr_info(to_coin_pair, real_price)
                if atr is None or atr <= 0:
                    self.logger.error(
                        f"{to_symbol} ATR 数据不可用，无法立即建仓。"
                        f"下轮 scout 将重新尝试（使用 ticker fallback）"
                    )
                else:
                    # 立即创建并持久化仓位
                    stop_price = real_price - self.k_initial_stop * atr
                    new_position = PositionState(
                        symbol=to_symbol,
                        entry_price=real_price,
                        entry_time=real_time,
                        highest_price=real_price,
                        stop_price=stop_price,
                        trail_active=False,
                        atr=atr,
                        atr_pct=atr_pct,
                        last_atr_update_time=self.manager.datetime,
                    )
                    self.db.save_position_state(new_position)
                    self.logger.info(
                        f"🧱 立即建仓 {to_symbol}: entry={real_price:.8f}, ATR={atr:.8f} ({atr_pct:.2f}%), "
                        f"初始止损={stop_price:.8f}"
                    )
            else:
                # 提取失败，下轮 scout 将使用 ticker fallback
                ticker_price = self.manager.get_ticker_price(to_coin_pair)
                self.logger.warning(
                    f"⚠️  无法从订单中提取 {to_symbol} 真实成交信息，"
                    f"下轮 scout 将使用 ticker={ticker_price:.8f} 建仓 "
                    f"(存在滑点风险，建议检查订单对象格式)"
                )

        return result

    def initialize_current_coin(self):
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

            if self.config.CURRENT_COIN_SYMBOL == "":
                current_coin = self.db.get_current_coin()
                self.logger.info(f"Purchasing {current_coin} to begin trading")
                self.manager.buy_alt(current_coin, self.config.BRIDGE)
                self.logger.info("Ready to start trading with ATR risk management")
