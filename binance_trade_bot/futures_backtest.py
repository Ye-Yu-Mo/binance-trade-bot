"""
期货回测系统
设计原则：
1. 策略代码零修改 - 通过依赖注入实现
2. 数据结构优先 - 简洁的Position和Trade模型
3. 懒加载+缓存 - 自动管理历史数据
"""
import os
import pickle
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Literal
import time

from binance.client import Client
from binance.exceptions import BinanceAPIException


# ========================================
# 数据模型
# ========================================

@dataclass
class Candle:
    """K线数据"""
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int

    @classmethod
    def from_binance(cls, raw):
        """从Binance API原始数据转换"""
        return cls(
            open_time=raw[0],
            open=float(raw[1]),
            high=float(raw[2]),
            low=float(raw[3]),
            close=float(raw[4]),
            volume=float(raw[5]),
            close_time=raw[6],
        )


@dataclass
class Position:
    """仓位模型"""
    symbol: str
    side: Literal['LONG', 'SHORT']
    quantity: float
    entry_price: float
    entry_time: int
    total_margin: float = 0.0  # 累计保证金（修复保证金计算的关键）

    def unrealized_pnl(self, current_price: float) -> float:
        """计算未实现盈亏"""
        if self.side == 'LONG':
            return (current_price - self.entry_price) * self.quantity
        else:  # SHORT
            return (self.entry_price - current_price) * self.quantity

    def pnl_pct(self, current_price: float) -> float:
        """计算盈亏百分比"""
        if self.side == 'LONG':
            return (current_price - self.entry_price) / self.entry_price * 100
        else:  # SHORT
            return (self.entry_price - current_price) / self.entry_price * 100


@dataclass
class Trade:
    """交易记录"""
    timestamp: int
    symbol: str
    side: Literal['LONG', 'SHORT']
    action: Literal['OPEN', 'CLOSE']
    quantity: float
    price: float
    pnl: float = 0.0  # 只有CLOSE时有值


# ========================================
# 数据加载器
# ========================================

class DataLoader:
    """
    智能数据加载器
    策略：按固定块缓存（避免缓存爆炸）
    """

    def __init__(self, cache_dir='./backtest_data', binance_client=None):
        self.cache_dir = cache_dir
        self.binance_client = binance_client
        self.memory_cache = {}  # 内存缓存：{symbol_interval_month: List[Candle]}

        # 创建缓存目录
        os.makedirs(cache_dir, exist_ok=True)

    def get_klines(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> List[Candle]:
        """
        获取K线数据
        Args:
            symbol: 交易对
            interval: K线周期（'1m', '5m', '1h'等）
            start_ms: 开始时间戳（毫秒）
            end_ms: 结束时间戳（毫秒）
        """
        # 按月加载数据块，然后在内存中切片
        from datetime import datetime

        start_date = datetime.fromtimestamp(start_ms / 1000)
        end_date = datetime.fromtimestamp(end_ms / 1000)

        # 收集需要的所有月份数据
        all_klines = []
        current_year_month = (start_date.year, start_date.month)
        end_year_month = (end_date.year, end_date.month)

        while current_year_month <= end_year_month:
            year, month = current_year_month
            month_klines = self._get_month_klines(symbol, interval, year, month)
            all_klines.extend(month_klines)

            # 下一个月
            if month == 12:
                current_year_month = (year + 1, 1)
            else:
                current_year_month = (year, month + 1)

        # 过滤到精确的时间范围
        result = [k for k in all_klines if start_ms <= k.open_time <= end_ms]
        return result

    def _get_month_klines(self, symbol: str, interval: str, year: int, month: int) -> List[Candle]:
        """
        获取某月的完整K线数据（带缓存）
        """
        # 缓存Key：symbol_interval_YYYY-MM
        cache_key = f"{symbol}_{interval}_{year:04d}-{month:02d}"

        # 1. 检查内存缓存
        if cache_key in self.memory_cache:
            return self.memory_cache[cache_key]

        # 2. 检查本地文件缓存
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.pkl")
        if os.path.exists(cache_file):
            with open(cache_file, 'rb') as f:
                klines = pickle.load(f)
                self.memory_cache[cache_key] = klines
                return klines

        # 3. 从API下载
        if self.binance_client:
            # 计算该月的起止时间戳
            from datetime import datetime
            start_date = datetime(year, month, 1)
            if month == 12:
                end_date = datetime(year + 1, 1, 1)
            else:
                end_date = datetime(year, month + 1, 1)

            start_ms = int(start_date.timestamp() * 1000)
            end_ms = int(end_date.timestamp() * 1000)

            klines = self._download_from_binance(symbol, interval, start_ms, end_ms)

            # 保存到文件缓存
            with open(cache_file, 'wb') as f:
                pickle.dump(klines, f)

            self.memory_cache[cache_key] = klines
            return klines

        # 如果既没有缓存也没有API，返回空
        return []

    def _download_from_binance(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> List[Candle]:
        """从Binance API下载数据"""
        from datetime import datetime
        start_str = datetime.fromtimestamp(start_ms / 1000).strftime('%Y-%m-%d')
        end_str = datetime.fromtimestamp(end_ms / 1000).strftime('%Y-%m-%d')
        print(f"📥 Downloading {symbol} {interval} data: {start_str} ~ {end_str}")

        all_klines = []
        current_start = start_ms

        # Binance API限制每次最多1000根K线
        while current_start < end_ms:
            try:
                raw_klines = self.binance_client.futures_klines(
                    symbol=symbol,
                    interval=interval,
                    startTime=current_start,
                    endTime=end_ms,
                    limit=1000
                )

                if not raw_klines:
                    break

                klines = [Candle.from_binance(k) for k in raw_klines]
                all_klines.extend(klines)

                # 更新起始时间到最后一根K线之后
                current_start = klines[-1].close_time + 1

                # 避免API限流
                time.sleep(0.5)

            except BinanceAPIException as e:
                print(f"❌ Failed to download data: {e}")
                break

        print(f"✅ Downloaded {len(all_klines)} candles")
        return all_klines


# ========================================
# 回测API管理器
# ========================================

class BacktestAPIManager:
    """
    模拟 BinanceFuturesAPIManager 的接口
    策略代码无法区分这是回测还是实盘
    """

    def __init__(self, data_loader: DataLoader, initial_balance: float = 10000, leverage: int = 3):
        self.data_loader = data_loader
        self.current_time = None  # 回测引擎会设置这个虚拟时间（毫秒）
        self.balance = initial_balance
        self.positions: Dict[str, Position] = {}
        self.trades: List[Trade] = []
        self.leverage = leverage

        # 模拟 binance_client（策略代码会调用）
        self.binance_client = self

    def futures_klines(self, symbol: str, interval: str, limit: int = 100, **kwargs):
        """
        模拟 binance_client.futures_klines()
        策略代码会调用这个方法获取K线数据

        关键：只返回已完成的K线，避免数据泄露
        例如：当前时间 10:00，只能看到 09:00 之前完成的K线
        """
        if self.current_time is None:
            raise RuntimeError("current_time not set by backtest engine")

        # 计算时间范围
        interval_ms = self._interval_to_ms(interval)

        # 关键修复：只获取"已完成"的K线
        # 当前时间向下取整到上一根K线的结束时间
        current_interval_start = (self.current_time // interval_ms) * interval_ms
        end_time = current_interval_start  # 只到上一根K线结束
        start_time = end_time - (limit * interval_ms)

        # 从DataLoader获取数据
        klines = self.data_loader.get_klines(symbol, interval, start_time, end_time)

        # 过滤：只返回 close_time < current_time 的K线
        klines = [k for k in klines if k.close_time < self.current_time]

        # 只返回最后 limit 根
        klines = klines[-limit:]

        if len(klines) < limit:
            # 数据不足警告（但不抛异常，让策略自己决定如何处理）
            print(f"⚠️  数据不足：期望{limit}根，实际{len(klines)}根 @ {self._ts_to_str(self.current_time)}")

        # 转换为Binance API格式（策略代码期望的格式）
        return [
            [
                k.open_time,
                str(k.open),
                str(k.high),
                str(k.low),
                str(k.close),
                str(k.volume),
                k.close_time,
            ]
            for k in klines
        ]

    def _ts_to_str(self, ts: int) -> str:
        """时间戳转字符串（辅助方法）"""
        from datetime import datetime
        return datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M')

    def get_mark_price(self, symbol: str) -> Optional[float]:
        """获取当前标记价格"""
        try:
            # 获取当前时间点的1分钟K线
            klines = self.futures_klines(symbol, '1m', limit=1)
            if klines:
                return float(klines[-1][4])  # 收盘价
            return None
        except Exception as e:
            print(f"Failed to get mark price for {symbol}: {e}")
            return None

    def get_all_positions(self) -> list:
        """
        返回所有持仓（模拟API格式）
        策略代码期望返回 List[Dict]
        """
        result = []
        for symbol, pos in self.positions.items():
            if pos.quantity > 0:
                current_price = self.get_mark_price(pos.symbol)
                if current_price:
                    result.append({
                        'symbol': symbol,
                        'positionSide': pos.side,
                        'positionAmt': str(pos.quantity) if pos.side == 'LONG' else str(-pos.quantity),
                        'entryPrice': str(pos.entry_price),
                        'markPrice': str(current_price),
                        'unRealizedProfit': str(pos.unrealized_pnl(current_price)),
                        'liquidationPrice': '0',
                    })
        return result

    def get_usdt_balance(self) -> float:
        """返回USDT余额"""
        return self.balance

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """设置杠杆（回测中不做实际操作）"""
        return True

    def setup_futures_mode(self, leverage: int = 3) -> bool:
        """设置期货模式（回测中不做实际操作）"""
        self.leverage = leverage
        return True

    def open_long(self, symbol: str, quantity: float) -> Optional[Dict]:
        """开多仓"""
        return self._execute_trade('LONG', 'OPEN', symbol, quantity)

    def open_short(self, symbol: str, quantity: float) -> Optional[Dict]:
        """开空仓"""
        return self._execute_trade('SHORT', 'OPEN', symbol, quantity)

    def close_long(self, symbol: str, quantity: float) -> Optional[Dict]:
        """平多仓"""
        return self._execute_trade('LONG', 'CLOSE', symbol, quantity)

    def close_short(self, symbol: str, quantity: float) -> Optional[Dict]:
        """平空仓"""
        return self._execute_trade('SHORT', 'CLOSE', symbol, quantity)

    def _execute_trade(self, side: Literal['LONG', 'SHORT'], action: Literal['OPEN', 'CLOSE'],
                       symbol: str, quantity: float) -> Optional[Dict]:
        """统一的交易执行逻辑"""
        price = self.get_mark_price(symbol)
        if not price:
            return None

        position_key = f"{symbol}_{side}"

        if action == 'OPEN':
            # 开仓
            cost = price * quantity / self.leverage  # 保证金 = 名义价值 / 杠杆

            if cost > self.balance:
                print(f"⚠️  余额不足：需要 {cost:.2f}，但只有 {self.balance:.2f}")
                return None

            self.balance -= cost

            if position_key in self.positions:
                # 加仓：更新平均价格和累计保证金
                old_pos = self.positions[position_key]
                total_quantity = old_pos.quantity + quantity
                avg_price = (old_pos.entry_price * old_pos.quantity + price * quantity) / total_quantity
                old_pos.quantity = total_quantity
                old_pos.entry_price = avg_price
                old_pos.total_margin += cost  # 累加保证金
            else:
                # 新建仓位
                self.positions[position_key] = Position(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    entry_price=price,
                    entry_time=self.current_time,
                    total_margin=cost,  # 记录保证金
                )

            self.trades.append(Trade(
                timestamp=self.current_time,
                symbol=symbol,
                side=side,
                action='OPEN',
                quantity=quantity,
                price=price,
            ))

            return {'orderId': f"BT_{self.current_time}_{symbol}"}

        else:  # CLOSE
            if position_key not in self.positions:
                print(f"⚠️  没有 {symbol} 的 {side} 仓位")
                return None

            pos = self.positions[position_key]

            if quantity > pos.quantity:
                print(f"⚠️  平仓数量 {quantity} 大于持仓 {pos.quantity}")
                quantity = pos.quantity

            # 计算盈亏
            pnl = pos.unrealized_pnl(price) * (quantity / pos.quantity)

            # 归还保证金（按比例）- 修复：用 total_margin 而不是重新计算
            returned_margin = pos.total_margin * (quantity / pos.quantity)
            self.balance += returned_margin + pnl

            # 更新仓位
            pos.quantity -= quantity
            pos.total_margin -= returned_margin  # 减少保证金

            if pos.quantity <= 0:
                del self.positions[position_key]

            self.trades.append(Trade(
                timestamp=self.current_time,
                symbol=symbol,
                side=side,
                action='CLOSE',
                quantity=quantity,
                price=price,
                pnl=pnl,
            ))

            return {'orderId': f"BT_{self.current_time}_{symbol}_CLOSE"}

    def _interval_to_ms(self, interval: str) -> int:
        """K线周期转毫秒"""
        mapping = {
            '1m': 60 * 1000,
            '3m': 3 * 60 * 1000,
            '5m': 5 * 60 * 1000,
            '15m': 15 * 60 * 1000,
            '30m': 30 * 60 * 1000,
            '1h': 60 * 60 * 1000,
            '2h': 2 * 60 * 60 * 1000,
            '4h': 4 * 60 * 60 * 1000,
            '1d': 24 * 60 * 60 * 1000,
        }
        return mapping.get(interval, 60 * 1000)

    def _format_quantity(self, symbol: str, quantity: float) -> float:
        """格式化数量（回测中简化处理）"""
        return round(quantity, 6)


# ========================================
# 回测引擎
# ========================================

class BacktestEngine:
    """
    回测主循环
    职责：
    1. 生成时间序列
    2. 注入虚拟时间到BacktestAPIManager
    3. 调用策略的scout()方法
    4. 记录和统计结果
    """

    def __init__(self, strategy_class, config, binance_client=None):
        self.strategy_class = strategy_class
        self.config = config
        self.data_loader = DataLoader(binance_client=binance_client)
        self.equity_curve = []  # 权益曲线

    def run(self, start_date: str, end_date: str, initial_balance: float = 10000,
            interval: str = '1h', leverage: int = 3):
        """
        运行回测
        Args:
            start_date: 开始日期 '2024-01-01'
            end_date: 结束日期 '2024-12-31'
            initial_balance: 初始资金
            interval: 回测步进周期（默认1小时）
            leverage: 杠杆倍数
        """
        print("=" * 60)
        print(f"🚀 期货回测启动")
        print("=" * 60)
        print(f"时间范围: {start_date} ~ {end_date}")
        print(f"初始资金: ${initial_balance:.2f}")
        print(f"步进周期: {interval}")
        print(f"杠杆倍数: {leverage}x")
        print("=" * 60)

        # 1. 创建回测API管理器
        backtest_api = BacktestAPIManager(
            data_loader=self.data_loader,
            initial_balance=initial_balance,
            leverage=leverage
        )

        # 2. 创建策略实例（依赖注入）
        from binance_trade_bot.database import Database
        from binance_trade_bot.logger import Logger

        logger = Logger(logging_service='backtest')
        db = Database(logger, self.config)

        # 创建策略（注入回测管理器）
        strategy = self.strategy_class(
            manager=backtest_api,
            db=db,
            logger=logger,
            config=self.config
        )

        strategy.initialize()

        # 3. 生成时间序列
        timestamps = self._generate_timestamps(start_date, end_date, interval)
        print(f"\n📊 回测周期数: {len(timestamps)}")

        if len(timestamps) == 0:
            raise ValueError("时间范围无效，无法生成时间戳序列")

        # 4. 数据预热：预先下载需要的历史数据
        print("\n📦 数据预热中...")
        self._warmup_data(backtest_api, timestamps, strategy)

        # 5. 时间循环
        print("\n🔄 开始回测循环...\n")

        error_count = 0
        max_errors = max(10, len(timestamps) // 10)  # 最多允许10%的周期失败

        for i, ts in enumerate(timestamps):
            # 设置当前虚拟时间
            backtest_api.current_time = ts

            # 调用策略
            try:
                strategy.scout()
            except Exception as e:
                error_count += 1
                print(f"❌ Strategy error at {self._ts_to_str(ts)}: {e}")

                if error_count > max_errors:
                    raise RuntimeError(
                        f"策略失败率过高：{error_count}/{i+1} ({error_count/(i+1)*100:.1f}%)\n"
                        f"回测中止，请检查策略代码或数据完整性"
                    )
                continue

            # 记录权益
            total_equity = backtest_api.balance
            for pos_key, pos in backtest_api.positions.items():
                current_price = backtest_api.get_mark_price(pos.symbol)
                if current_price:
                    total_equity += pos.unrealized_pnl(current_price)

            self.equity_curve.append({
                'timestamp': ts,
                'equity': total_equity,
                'balance': backtest_api.balance,
                'positions': len(backtest_api.positions),
            })

            # 进度显示（每10%或至少每100个周期）
            progress_step = max(1, len(timestamps) // 10)
            if (i + 1) % progress_step == 0:
                progress = (i + 1) / len(timestamps) * 100
                print(f"⏳ 进度: {progress:.0f}% | 权益: ${total_equity:.2f} | "
                      f"持仓数: {len(backtest_api.positions)}")

        # 6. 输出统计
        if error_count > 0:
            print(f"\n⚠️  警告：回测过程中发生 {error_count} 次错误")

        self._print_summary(backtest_api, initial_balance)

        return backtest_api

    def _warmup_data(self, api: 'BacktestAPIManager', timestamps: List[int], strategy):
        """
        数据预热：提前下载策略需要的历史数据
        """
        # 检测策略需要的symbol列表
        if hasattr(strategy, 'symbols'):
            symbols = strategy.symbols
        elif hasattr(strategy, 'symbol'):
            symbols = [strategy.symbol]
        else:
            print("⚠️  无法检测策略的交易对，跳过数据预热")
            return

        # 预热第一个时间点的数据（确保有足够的历史K线）
        first_timestamp = timestamps[0]
        api.current_time = first_timestamp

        print(f"预热交易对: {', '.join(symbols)}")

        for symbol in symbols:
            try:
                # 尝试获取策略需要的K线数据（假设最多需要100根）
                klines = api.futures_klines(symbol, '5m', limit=100)
                if len(klines) < 10:
                    print(f"⚠️  {symbol} 历史数据不足（仅{len(klines)}根K线）")
            except Exception as e:
                print(f"❌ 预热 {symbol} 数据失败: {e}")

        print("✅ 数据预热完成\n")

    def _generate_timestamps(self, start_date: str, end_date: str, interval: str) -> List[int]:
        """生成时间戳序列"""
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')

        interval_ms = self._interval_to_ms(interval)
        interval_seconds = interval_ms / 1000

        timestamps = []
        current = start

        while current <= end:
            timestamps.append(int(current.timestamp() * 1000))
            current += timedelta(seconds=interval_seconds)

        return timestamps

    def _interval_to_ms(self, interval: str) -> int:
        """K线周期转毫秒"""
        mapping = {
            '1m': 60 * 1000,
            '5m': 5 * 60 * 1000,
            '15m': 15 * 60 * 1000,
            '1h': 60 * 60 * 1000,
            '4h': 4 * 60 * 60 * 1000,
            '1d': 24 * 60 * 60 * 1000,
        }
        return mapping.get(interval, 60 * 60 * 1000)

    def _ts_to_str(self, ts: int) -> str:
        """时间戳转字符串"""
        return datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M')

    def _print_summary(self, api: BacktestAPIManager, initial_balance: float):
        """输出回测统计"""
        print("\n" + "=" * 60)
        print("📈 回测结果")
        print("=" * 60)

        # 基本统计
        final_equity = self.equity_curve[-1]['equity'] if self.equity_curve else initial_balance
        total_return = (final_equity - initial_balance) / initial_balance * 100

        print(f"初始资金: ${initial_balance:.2f}")
        print(f"最终权益: ${final_equity:.2f}")
        print(f"总收益率: {total_return:+.2f}%")

        # 交易统计
        trades = api.trades
        closed_trades = [t for t in trades if t.action == 'CLOSE']

        if closed_trades:
            winning_trades = [t for t in closed_trades if t.pnl > 0]
            losing_trades = [t for t in closed_trades if t.pnl < 0]

            win_rate = len(winning_trades) / len(closed_trades) * 100
            avg_win = sum(t.pnl for t in winning_trades) / len(winning_trades) if winning_trades else 0
            avg_loss = sum(t.pnl for t in losing_trades) / len(losing_trades) if losing_trades else 0

            print(f"\n交易总数: {len(closed_trades)}")
            print(f"胜率: {win_rate:.1f}% ({len(winning_trades)}胜 / {len(losing_trades)}负)")
            print(f"平均盈利: ${avg_win:.2f}")
            print(f"平均亏损: ${avg_loss:.2f}")

            # 最大回撤
            max_drawdown = self._calculate_max_drawdown()
            print(f"最大回撤: {max_drawdown:.2f}%")

        print("=" * 60)

    def _calculate_max_drawdown(self) -> float:
        """计算最大回撤"""
        if not self.equity_curve:
            return 0.0

        peak = self.equity_curve[0]['equity']
        max_dd = 0.0

        for point in self.equity_curve:
            equity = point['equity']
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)

        return max_dd
