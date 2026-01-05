"""
期货风险管理策略框架
设计原则：
1. 数据结构优先 - 仓位直接来自API，无需数据库
2. 消除特殊情况 - 统一的侦察循环处理所有仓位状态
3. 简洁 - 框架核心不超过150行
4. 实用 - 支持做多/做空，自动止损/止盈
"""

from datetime import datetime
from typing import Optional, Literal
import time

from binance_trade_bot.binance_futures_api_manager import BinanceFuturesAPIManager, FuturesPosition


class Strategy:
    """
    期货风险管理策略

    核心逻辑：
    1. 侦察循环：每秒检查仓位 → 检查风险 → 生成信号
    2. 仓位管理：LONG/SHORT/无仓位，统一处理
    3. 风险控制：止损5% / 止盈15% / 杠杆3x / 仓位30%
    """

    def __init__(self, manager: BinanceFuturesAPIManager, db, logger, config):
        """初始化策略"""
        self.manager = manager
        self.db = db
        self.logger = logger
        self.config = config

        # 配置参数
        self.symbol = "BTCUSDT"  # 可扩展到多个交易对
        self.stop_loss_pct = 5.0  # 止损5%
        self.take_profit_pct = 15.0  # 止盈15%
        self.leverage = 3  # 杠杆3x
        self.position_size_pct = 0.30  # 仓位大小：账户余额的30%

        # 心跳计数器 - 每10次scout输出一次状态
        self._scout_count = 0

        self.logger.info(
            f"期货风险管理策略已初始化 - "
            f"止损:{self.stop_loss_pct}%, 止盈:{self.take_profit_pct}%, "
            f"杠杆:{self.leverage}x, 仓位:{self.position_size_pct*100}%"
        )

    def scout(self):
        """
        主侦察循环 - 核心决策引擎

        流程：
        1. 查询所有仓位
        2. 对每个仓位检查止损/止盈
        3. 如果空仓，查询信号并决定开仓
        """
        try:
            # 心跳输出 - 每10秒显示一次状态
            self._scout_count += 1
            if self._scout_count % 10 == 0:
                balance = self.manager.get_usdt_balance()
                mark_price = self.manager.get_mark_price(self.symbol)
                self.logger.info(
                    f"💓 心跳 #{self._scout_count} - "
                    f"余额: ${balance:.2f}, {self.symbol}: ${mark_price:.2f}"
                )

            # 获取当前所有仓位
            positions = self.manager.get_all_positions()

            # 如果有仓位，检查风险
            if positions:
                for position in positions:
                    self.check_position_risk(position)
            else:
                # 空仓状态：尝试生成交易信号
                self._check_and_open_position()

        except Exception as e:
            self.logger.error(f"侦察循环异常: {e}")

    def check_position_risk(self, position: FuturesPosition) -> bool:
        """
        检查仓位的止损/止盈条件

        Args:
            position: 期货仓位对象

        Returns:
            True: 触发风险，已平仓
            False: 正常持仓
        """
        # 计算未实现盈亏百分比
        if position.entry_price <= 0:
            return False

        # LONG: 价格上涨盈利 = (mark - entry) / entry
        # SHORT: 价格下跌盈利 = (entry - mark) / entry
        if position.side == 'LONG':
            pnl_pct = (position.mark_price - position.entry_price) / position.entry_price * 100
        else:  # SHORT
            pnl_pct = (position.entry_price - position.mark_price) / position.entry_price * 100

        self.logger.info(
            f"🔍 {position.symbol} ({position.side}仓): "
            f"入场价 {position.entry_price:.2f}, 标记价 {position.mark_price:.2f}, "
            f"盈亏 {pnl_pct:+.2f}%"
        )

        # 止损检查
        if pnl_pct <= -self.stop_loss_pct:
            self.logger.warning(
                f"🛑 {position.symbol} 触发止损！{position.side}仓 亏损 {pnl_pct:.2f}%"
            )
            return self._close_position(position)

        # 止盈检查
        if pnl_pct >= self.take_profit_pct:
            self.logger.info(
                f"💰 {position.symbol} 触发止盈！{position.side}仓 盈利 {pnl_pct:.2f}%"
            )
            return self._close_position(position)

        return False

    def _close_position(self, position: FuturesPosition) -> bool:
        """
        平仓指定仓位

        Args:
            position: 要平仓的仓位

        Returns:
            True: 平仓成功
            False: 平仓失败
        """
        close_method = (
            self.manager.close_long
            if position.side == 'LONG'
            else self.manager.close_short
        )

        result = close_method(position.symbol, position.quantity)

        if result:
            self.logger.info(f"✅ 成功平仓 {position.symbol} {position.side}仓")
            return True
        else:
            self.logger.error(f"❌ 平仓失败 {position.symbol} {position.side}仓")
            return False

    def _check_and_open_position(self):
        """
        空仓时：尝试生成信号并开仓
        """
        signal = self.generate_signal(self.symbol)

        if signal == 'NONE':
            return

        position_size = self.calculate_position_size()
        if position_size <= 0:
            self.logger.warning(f"仓位计算失败或余额不足")
            return

        if signal == 'LONG':
            self.logger.info(f"📈 生成LONG信号，开多仓 {self.symbol}")
            self.manager.set_leverage(self.symbol, self.leverage)
            self.manager.open_long(self.symbol, position_size)

        elif signal == 'SHORT':
            self.logger.info(f"📉 生成SHORT信号，开空仓 {self.symbol}")
            self.manager.set_leverage(self.symbol, self.leverage)
            self.manager.open_short(self.symbol, position_size)

    def calculate_position_size(self) -> float:
        """
        计算仓位大小：账户余额 × 30% ÷ 当前价格

        Returns:
            仓位合约数量，失败返回0
        """
        usdt_balance = self.manager.get_usdt_balance()
        if usdt_balance <= 0:
            self.logger.error("USDT余额为0，无法开仓")
            return 0

        # 获取当前价格（标记价格最准确）
        mark_price = self.manager.get_mark_price(self.symbol)
        if not mark_price or mark_price <= 0:
            self.logger.error(f"无法获取 {self.symbol} 价格")
            return 0

        # 仓位大小 = (余额 × 30% × 杠杆) / 价格
        position_usdt = usdt_balance * self.position_size_pct * self.leverage
        quantity = position_usdt / mark_price

        self.logger.info(
            f"💰 仓位计算: 余额={usdt_balance:.2f}USDT, "
            f"价格={mark_price:.2f}, 数量={quantity:.6f}"
        )
        return quantity

    def generate_signal(self, symbol: str) -> Literal['LONG', 'SHORT', 'NONE']:
        """
        生成交易信号 - 基于价格动量策略

        逻辑：
        - 获取最近2根5分钟K线
        - 如果价格上涨 > 1% → LONG
        - 如果价格下跌 > 1% → SHORT
        - 否则 → NONE

        Args:
            symbol: 交易对，如 'BTCUSDT'

        Returns:
            'LONG': 买入信号
            'SHORT': 卖出信号
            'NONE': 无信号
        """
        try:
            # 获取最近2根5分钟K线
            klines = self.manager.binance_client.futures_klines(
                symbol=symbol,
                interval='5m',
                limit=2
            )

            if len(klines) < 2:
                return 'NONE'

            # 提取收盘价
            previous_close = float(klines[-2][4])  # 上一根K线收盘价
            current_close = float(klines[-1][4])   # 当前K线收盘价

            # 计算涨跌幅
            price_change_pct = (current_close - previous_close) / previous_close * 100

            self.logger.debug(
                f"📊 {symbol} 价格动量: "
                f"上根:{previous_close:.2f}, 当前:{current_close:.2f}, "
                f"变化:{price_change_pct:+.2f}%"
            )

            # 信号判断
            if price_change_pct > 1.0:
                self.logger.info(f"📈 {symbol} 上涨 {price_change_pct:.2f}% → LONG信号")
                return 'LONG'
            elif price_change_pct < -1.0:
                self.logger.info(f"📉 {symbol} 下跌 {price_change_pct:.2f}% → SHORT信号")
                return 'SHORT'
            else:
                return 'NONE'

        except Exception as e:
            self.logger.error(f"生成信号失败: {e}")
            return 'NONE'

    def initialize(self):
        """
        初始化策略 - 在启动前调用
        """
        # 设置期货账户为双向持仓模式
        self.manager.setup_futures_mode(leverage=self.leverage)
        self.logger.info("期货账户初始化完成")
