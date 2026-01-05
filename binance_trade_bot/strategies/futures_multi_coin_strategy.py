"""
多币种期货策略 - 基于现货 bridge_scout 逻辑的简化版

核心思想：
1. 扫描多个币种 (BTCUSDT, ETHUSDT, BNBUSDT, ...)
2. 计算每个币种的动量分数（相对强弱）
3. 选最强的N个做多，最弱的N个做空
4. 根据信号强度动态分配仓位
"""
from dataclasses import dataclass
from datetime import datetime
from typing import List, Literal, Optional, Dict
from binance_trade_bot.binance_futures_api_manager import BinanceFuturesAPIManager, FuturesPosition


@dataclass
class SignalStrength:
    """交易信号强度"""
    symbol: str
    momentum_score: float  # 动量分数 (-100 to +100)
    direction: Literal['LONG', 'SHORT', 'NONE']
    position_size_pct: float  # 仓位大小百分比 (0.05 到 0.3)


class Strategy:
    """
    多币种期货策略

    核心逻辑：
    1. 扫描多个币种，计算动量分数
    2. 选择信号最强的币种开仓
    3. 支持同时持有多个多/空仓
    4. 动态调整仓位大小
    """

    def __init__(self, manager: BinanceFuturesAPIManager, db, logger, config):
        """初始化策略"""
        self.manager = manager
        self.db = db
        self.logger = logger
        self.config = config

        # 交易标的 - 支持多币种
        self.symbols = [
            "BTCUSDT",
            "ETHUSDT",
            "BNBUSDT",
            "SOLUSDT",
            "ADAUSDT",
        ]

        # 风险参数
        self.stop_loss_pct = 5.0  # 止损 5%
        self.take_profit_pct = 15.0  # 止盈 15%
        self.leverage = 3  # 杠杆 3x

        # 仓位管理参数
        self.max_positions = 6  # 最多同时持有6个仓位
        self.min_signal_threshold = 0.5  # 最小信号阈值 0.5%
        self.base_position_size_pct = 0.15  # 基础仓位大小 15%（会根据信号强度调整）

        # 心跳计数器
        self._scout_count = 0

        self.logger.info(
            f"多币种期货策略已初始化 - "
            f"币种数:{len(self.symbols)}, 最大仓位:{self.max_positions}, "
            f"止损:{self.stop_loss_pct}%, 止盈:{self.take_profit_pct}%"
        )

    def scout(self):
        """
        主侦察循环

        流程：
        1. 检查现有仓位的止损/止盈
        2. 扫描所有币种，生成信号
        3. 选择最优机会开仓
        """
        try:
            # 心跳输出
            self._scout_count += 1
            if self._scout_count % 6 == 0:  # 每30秒一次（6次 × 5秒）
                balance = self.manager.get_usdt_balance()
                positions = self.manager.get_all_positions()
                self.logger.info(
                    f"💓 心跳 #{self._scout_count} - "
                    f"余额: ${balance:.2f}, 持仓数: {len(positions)}"
                )

            # 1. 获取所有仓位（只调用一次API）
            positions = self.manager.get_all_positions()

            # 2. 管理现有仓位
            for position in positions:
                self._check_position_risk(position)

            # 3. 如果仓位数未满，寻找新机会
            if len(positions) < self.max_positions:
                # 传入已有仓位，避免重复调用 API
                self._scan_and_open_positions(positions)

        except Exception as e:
            self.logger.error(f"侦察循环异常: {e}")

    def _check_position_risk(self, position: FuturesPosition):
        """
        检查仓位的止损/止盈
        （复用之前的逻辑）
        """
        if position.entry_price <= 0:
            return

        # 计算盈亏百分比
        if position.side == 'LONG':
            pnl_pct = (position.mark_price - position.entry_price) / position.entry_price * 100
        else:  # SHORT
            pnl_pct = (position.entry_price - position.mark_price) / position.entry_price * 100

        # 止损检查
        if pnl_pct <= -self.stop_loss_pct:
            self.logger.warning(
                f"🛑 {position.symbol} {position.side}仓触发止损！亏损 {pnl_pct:.2f}%"
            )
            self._close_position(position)
            return

        # 止盈检查
        if pnl_pct >= self.take_profit_pct:
            self.logger.info(
                f"💰 {position.symbol} {position.side}仓触发止盈！盈利 {pnl_pct:.2f}%"
            )
            self._close_position(position)
            return

    def _close_position(self, position: FuturesPosition):
        """平仓"""
        if position.side == 'LONG':
            result = self.manager.close_long(position.symbol, position.quantity)
        else:
            result = self.manager.close_short(position.symbol, position.quantity)

        if result:
            self.logger.info(f"✅ 成功平仓 {position.symbol} {position.side}仓")
        else:
            self.logger.error(f"❌ 平仓失败 {position.symbol} {position.side}仓")

    def _scan_and_open_positions(self, existing_positions: List[FuturesPosition]):
        """
        扫描所有币种，寻找交易机会并开仓

        Args:
            existing_positions: 已有仓位列表（避免重复查询API）
        """
        # 1. 扫描所有币种，计算信号强度
        signals = self._scan_all_symbols()

        # 2. 过滤掉已有仓位的币种
        existing_dict = {pos.symbol: pos.side for pos in existing_positions}
        signals = [s for s in signals if s.symbol not in existing_dict or existing_dict[s.symbol] != s.direction]

        # 3. 按信号强度排序，选择最强的机会
        signals = sorted(signals, key=lambda x: abs(x.momentum_score), reverse=True)

        # 4. 尝试开仓（最多开到max_positions）
        available_slots = self.max_positions - len(existing_positions)

        for signal in signals[:available_slots]:
            if signal.direction == 'NONE':
                continue

            self._execute_signal(signal)

    def _scan_all_symbols(self) -> List[SignalStrength]:
        """
        扫描所有交易对，计算信号强度

        算法：
        1. 获取每个币种的5分钟K线
        2. 计算动量分数 = (当前价 - 5分钟前价) / 5分钟前价 * 100
        3. 判断方向：正=LONG，负=SHORT
        4. 根据信号强度分配仓位大小
        """
        signals = []

        for symbol in self.symbols:
            try:
                # 获取5分钟K线（最近2根）
                klines = self.manager.binance_client.futures_klines(
                    symbol=symbol,
                    interval='5m',
                    limit=2
                )

                if len(klines) < 2:
                    continue

                # 计算动量
                price_before = float(klines[-2][4])  # 上一根K线收盘价
                price_now = float(klines[-1][4])     # 当前K线收盘价

                momentum_score = (price_now - price_before) / price_before * 100

                # 判断方向和仓位大小
                if momentum_score > self.min_signal_threshold:
                    direction = 'LONG'
                    # 信号越强，仓位越大
                    position_size_pct = self._calculate_position_size(momentum_score)
                elif momentum_score < -self.min_signal_threshold:
                    direction = 'SHORT'
                    position_size_pct = self._calculate_position_size(abs(momentum_score))
                else:
                    direction = 'NONE'
                    position_size_pct = 0

                signal = SignalStrength(
                    symbol=symbol,
                    momentum_score=momentum_score,
                    direction=direction,
                    position_size_pct=position_size_pct
                )
                signals.append(signal)

                self.logger.debug(
                    f"📊 {symbol}: 价格 {price_before:.2f} → {price_now:.2f}, "
                    f"动量 {momentum_score:+.2f}%, 方向 {direction}"
                )

            except Exception as e:
                self.logger.error(f"扫描 {symbol} 失败: {e}")
                continue

        return signals

    def _calculate_position_size(self, momentum_score: float) -> float:
        """
        根据信号强度动态计算仓位大小

        算法：
        - 基础仓位：15%
        - 信号越强，仓位越大（线性调整）
        - 范围：5% 到 30%

        momentum_score: 0.5% → 5%
        momentum_score: 1.0% → 15% (基础)
        momentum_score: 2.0% → 30% (最大)
        """
        abs_score = abs(momentum_score)

        if abs_score < 0.5:
            return 0.05
        elif abs_score < 1.0:
            # 0.5% to 1.0% → 5% to 15%
            return 0.05 + (abs_score - 0.5) * 0.2
        elif abs_score < 2.0:
            # 1.0% to 2.0% → 15% to 30%
            return 0.15 + (abs_score - 1.0) * 0.15
        else:
            return 0.30  # 最大 30%

    def _execute_signal(self, signal: SignalStrength):
        """
        执行交易信号

        Args:
            signal: 交易信号
        """
        balance = self.manager.get_usdt_balance()
        mark_price = self.manager.get_mark_price(signal.symbol)

        if not mark_price or mark_price <= 0:
            self.logger.error(f"无法获取 {signal.symbol} 价格")
            return

        # 计算仓位数量
        position_value = balance * signal.position_size_pct * self.leverage
        quantity = position_value / mark_price

        # 格式化数量
        quantity = self.manager._format_quantity(signal.symbol, quantity)

        self.logger.info(
            f"🎯 {signal.direction} {signal.symbol} - "
            f"动量:{signal.momentum_score:+.2f}%, 仓位:{signal.position_size_pct*100:.1f}%, "
            f"数量:{quantity:.6f}"
        )

        # 设置杠杆
        self.manager.set_leverage(signal.symbol, self.leverage)

        # 开仓
        if signal.direction == 'LONG':
            result = self.manager.open_long(signal.symbol, quantity)
        else:  # SHORT
            result = self.manager.open_short(signal.symbol, quantity)

        if result:
            self.logger.info(f"✅ 成功开仓 {signal.symbol} {signal.direction}仓")
        else:
            self.logger.error(f"❌ 开仓失败 {signal.symbol} {signal.direction}仓")

    def initialize(self):
        """初始化期货账户配置"""
        self.logger.info("🚀 多币种期货策略启动")

        # 设置双向持仓模式
        if not self.manager.setup_futures_mode(leverage=self.leverage):
            self.logger.error("Failed to setup futures mode!")
            return

        self.logger.info("✅ 期货账户初始化完成")
