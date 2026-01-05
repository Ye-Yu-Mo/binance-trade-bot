"""
现货回测系统

设计原则：
1. 策略代码零修改 - 完全兼容 AutoTrader
2. 用 yfinance 下载数据 - 简单、免费、稳定
3. 模拟现货交易 - buy_alt/sell_alt API

关键设计：
- 用 yfinance 一次性下载所有数据（不分批）
- 数据缓存到本地（pickle）
- 模拟 Binance 现货交易 API
"""
import os
import pickle
from datetime import datetime, timedelta
from typing import Dict, Optional
import pandas as pd
import yfinance as yf

from binance_trade_bot.models import Coin, Pair
from binance_trade_bot.database import Database
from binance_trade_bot.logger import Logger
from binance_trade_bot.config import Config

# ========================================
# 数据加载器（用 yfinance）
# ========================================

class DataLoader:
    """
    用 yfinance 下载历史数据
    简单、稳定、免费
    """

    def __init__(self, cache_dir='./backtest_data'):
        self.cache_dir = cache_dir
        self.data_cache = {}  # {symbol: DataFrame}
        os.makedirs(cache_dir, exist_ok=True)

    def get_price(self, symbol: str, timestamp_ms: int) -> Optional[float]:
        """
        获取指定时间点的价格

        Args:
            symbol: 交易对，如 'BNBUSDT'
            timestamp_ms: 时间戳（毫秒）

        Returns:
            价格（close价格），如果找不到返回 None
        """
        # 确保数据已加载
        if symbol not in self.data_cache:
            return None

        df = self.data_cache[symbol]

        # 转换时间戳为 datetime
        target_time = pd.to_datetime(timestamp_ms, unit='ms')

        # 找到最接近的价格（向前查找，不能用未来数据）
        # 使用 asof 方法：找到 <= target_time 的最近一条数据
        try:
            price = df['Close'].asof(target_time)
            return float(price) if pd.notna(price) else None
        except Exception:
            return None

    def load_data(self, symbols: list, start_date: str, end_date: str):
        """
        批量下载并缓存数据

        Args:
            symbols: 交易对列表，如 ['BNBUSDT', 'SOLUSDT']
            start_date: 开始日期 '2023-01-01'
            end_date: 结束日期 '2025-12-31'
        """
        print(f"开始下载数据...")
        print(f"时间范围: {start_date} ~ {end_date}")
        print(f"币种数量: {len(symbols)}")
        print()

        for symbol in symbols:
            # 转换 Binance 格式到 Yahoo Finance 格式
            # BNBUSDT -> BNB-USD
            yf_symbol = self._convert_symbol(symbol)

            cache_file = os.path.join(self.cache_dir, f"{symbol}_5m.pkl")

            # 检查缓存
            if os.path.exists(cache_file):
                print(f"✓ {symbol}: 使用缓存")
                with open(cache_file, 'rb') as f:
                    self.data_cache[symbol] = pickle.load(f)
                continue

            # 下载数据
            print(f"↓ {symbol}: 下载中...", end='', flush=True)
            try:
                df = yf.download(
                    yf_symbol,
                    start=start_date,
                    end=end_date,
                    interval='5m',
                    progress=False
                )

                if df.empty:
                    print(f" 失败（无数据）")
                    continue

                # 保存缓存
                with open(cache_file, 'wb') as f:
                    pickle.dump(df, f)

                self.data_cache[symbol] = df
                print(f" 成功（{len(df)} 条K线）")

            except Exception as e:
                print(f" 失败: {e}")

        print(f"\n数据加载完成！成功: {len(self.data_cache)}/{len(symbols)}")

    def _convert_symbol(self, binance_symbol: str) -> str:
        """
        转换 Binance 符号到 Yahoo Finance 符号
        BNBUSDT -> BNB-USD
        """
        if binance_symbol.endswith('USDT'):
            base = binance_symbol[:-4]
            return f"{base}-USD"
        return binance_symbol


# ========================================
# 现货回测API管理器
# ========================================

class SpotBacktestAPIManager:
    """
    模拟 BinanceAPIManager 的现货交易接口
    策略代码无法区分这是回测还是实盘
    """

    def __init__(self, data_loader: DataLoader, initial_balance: float = 10000, bridge_symbol: str = 'USDT'):
        self.data_loader = data_loader
        self.current_time = None  # 回测引擎会设置这个虚拟时间（毫秒）
        self.balances = {bridge_symbol: initial_balance}  # 账户余额 {symbol: amount}
        self.bridge_symbol = bridge_symbol
        self.fee_rate = 0.00075  # Binance 现货手续费 0.075%

        # 模拟 binance_client（策略代码会调用）
        self.binance_client = self

    def get_historical_klines(self, symbol: str, interval: str, start_str: str, end_str: str, limit: int = 1000):
        """
        模拟 binance_client.get_historical_klines()
        策略代码会调用这个方法获取K线数据

        关键：只返回已完成的K线，避免数据泄露
        """
        if self.current_time is None:
            raise RuntimeError("current_time not set by backtest engine")

        # 转换时间字符串为毫秒时间戳
        if isinstance(start_str, str):
            start_time = int(datetime.strptime(start_str, "%d %b %Y %H:%M:%S").timestamp() * 1000)
        else:
            start_time = int(start_str)

        if isinstance(end_str, str):
            end_time = int(datetime.strptime(end_str, "%d %b %Y %H:%M:%S").timestamp() * 1000)
        else:
            end_time = int(end_str)

        # 只返回已完成的K线（close_time < current_time）
        klines = self.data_loader.get_klines(symbol, interval, start_time, min(end_time, self.current_time))

        # 过滤：只返回 close_time < current_time 的K线
        klines = [k for k in klines if k.close_time < self.current_time]

        # 只返回最后 limit 根
        klines = klines[-limit:]

        # 转换为Binance API格式
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

    def get_ticker_price(self, ticker_symbol: str) -> Optional[float]:
        """
        获取当前标记价格
        ticker_symbol 格式：'BNBUSDT'
        """
        try:
            # 获取当前时间点的最近1根K线的收盘价
            interval_ms = 5 * 60 * 1000  # 5分钟
            current_interval_start = (self.current_time // interval_ms) * interval_ms
            end_time = current_interval_start

            klines = self.data_loader.get_klines(ticker_symbol, '5m', end_time - interval_ms, end_time)

            if klines and klines[-1].close_time < self.current_time:
                return klines[-1].close

            return None
        except Exception as e:
            print(f"Failed to get ticker price for {ticker_symbol}: {e}")
            return None

    def get_currency_balance(self, currency_symbol: str, force=False) -> float:
        """返回指定币种的余额"""
        return self.balances.get(currency_symbol, 0.0)

    def get_fee(self, origin_coin: Coin, target_coin: Coin, selling: bool) -> float:
        """返回交易手续费率"""
        return self.fee_rate

    def get_min_notional(self, origin_symbol: str, target_symbol: str) -> float:
        """返回最小交易金额（简化为固定值）"""
        return 10.0  # Binance 最小交易额约 10 USDT

    def buy_alt(self, origin_coin: Coin, target_coin: Coin):
        """
        买入 origin_coin，用 target_coin 支付
        通常：buy_alt(BNB, USDT) = 用 USDT 买 BNB
        """
        origin_symbol = origin_coin.symbol
        target_symbol = target_coin.symbol

        # 获取当前价格
        ticker_symbol = origin_symbol + target_symbol
        price = self.get_ticker_price(ticker_symbol)

        if price is None:
            print(f"⚠️  无法获取 {ticker_symbol} 价格")
            return None

        # 获取 target_coin 余额
        target_balance = self.get_currency_balance(target_symbol)

        if target_balance <= 0:
            print(f"⚠️  {target_symbol} 余额不足: {target_balance}")
            return None

        # 计算能买多少 origin_coin（扣除手续费）
        fee = self.get_fee(origin_coin, target_coin, False)
        origin_quantity = (target_balance / price) * (1 - fee)

        # 更新余额
        self.balances[target_symbol] = 0.0
        self.balances[origin_symbol] = self.balances.get(origin_symbol, 0.0) + origin_quantity

        print(f"✅ 买入: {origin_quantity:.8f} {origin_symbol} @ {price:.8f} (花费 {target_balance:.2f} {target_symbol})")

        # 返回模拟的订单对象
        class MockOrder:
            def __init__(self, price):
                self.price = price

        return MockOrder(price)

    def sell_alt(self, origin_coin: Coin, target_coin: Coin):
        """
        卖出 origin_coin，换回 target_coin
        通常：sell_alt(BNB, USDT) = 卖出 BNB 换回 USDT
        """
        origin_symbol = origin_coin.symbol
        target_symbol = target_coin.symbol

        # 获取当前价格
        ticker_symbol = origin_symbol + target_symbol
        price = self.get_ticker_price(ticker_symbol)

        if price is None:
            print(f"⚠️  无法获取 {ticker_symbol} 价格")
            return None

        # 获取 origin_coin 余额
        origin_balance = self.get_currency_balance(origin_symbol)

        if origin_balance <= 0:
            print(f"⚠️  {origin_symbol} 余额不足: {origin_balance}")
            return None

        # 计算能换回多少 target_coin（扣除手续费）
        fee = self.get_fee(origin_coin, target_coin, True)
        target_quantity = (origin_balance * price) * (1 - fee)

        # 更新余额
        self.balances[origin_symbol] = 0.0
        self.balances[target_symbol] = self.balances.get(target_symbol, 0.0) + target_quantity

        print(f"✅ 卖出: {origin_balance:.8f} {origin_symbol} @ {price:.8f} (换回 {target_quantity:.2f} {target_symbol})")

        return {"price": price}

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


# ========================================
# Mock Database（禁用 SocketIO）
# ========================================

class MockDatabase(Database):
    """回测用的数据库，禁用 SocketIO 通知"""

    def __init__(self, logger: Logger, config: Config):
        super().__init__(logger, config, "sqlite:///")

    def send_update(self, model):
        """禁用 SocketIO 通知（回测环境中不需要）"""
        pass

    def socketio_connect(self):
        """禁用 SocketIO 连接（回测环境中不需要）"""
        return False

    def log_scout(self, pair: Pair, target_ratio: float, current_coin_price: float, other_coin_price: float):
        """简化日志（回测环境中不需要详细记录）"""
        pass


# ========================================
# 现货回测引擎
# ========================================

class SpotBacktestEngine:
    """
    现货回测主循环
    职责：
    1. 生成时间序列
    2. 注入虚拟时间到SpotBacktestAPIManager
    3. 调用策略的scout()方法
    4. 记录和统计结果
    """

    def __init__(self, strategy_class, config, binance_client=None):
        self.strategy_class = strategy_class
        self.config = config
        self.data_loader = DataLoader(binance_client=binance_client)
        self.balance_history = []  # 资金曲线

    def run(self, start_date: str, end_date: str, initial_balance: float = 10000,
            interval: str = '5m'):
        """
        运行回测
        Args:
            start_date: 开始日期 '2023-01-01'
            end_date: 结束日期 '2025-12-31'
            initial_balance: 初始资金（USDT）
            interval: 回测步进周期（默认5分钟）
        """
        print("=" * 60)
        print(f"🚀 现货回测启动")
        print("=" * 60)
        print(f"时间范围: {start_date} ~ {end_date}")
        print(f"初始资金: ${initial_balance:.2f}")
        print(f"步进周期: {interval}")
        print(f"桥币: {self.config.BRIDGE.symbol}")
        print("=" * 60)

        # 1. 创建回测API管理器
        spot_api = SpotBacktestAPIManager(
            data_loader=self.data_loader,
            initial_balance=initial_balance,
            bridge_symbol=self.config.BRIDGE.symbol
        )

        # 2. 创建策略实例（依赖注入）
        logger = Logger(logging_service='spot_backtest', enable_notifications=False)
        db = MockDatabase(logger, self.config)  # 回测用数据库（禁用 SocketIO）

        # 初始化数据库
        db.create_database()
        db.set_coins(self.config.SUPPORTED_COIN_LIST)

        # 3. 生成时间序列
        timestamps = self._generate_timestamps(start_date, end_date, interval)
        print(f"\n📊 回测周期数: {len(timestamps)}")

        if len(timestamps) == 0:
            raise ValueError("时间范围无效，无法生成时间戳序列")

        # 4. 设置初始时间（策略初始化需要）
        spot_api.current_time = timestamps[0]

        # 5. 创建策略（注入回测管理器）
        strategy = self.strategy_class(
            binance_manager=spot_api,
            database=db,
            logger=logger,
            config=self.config
        )

        strategy.initialize()

        # 6. 时间循环
        print("\n🔄 开始回测循环...\n")

        error_count = 0
        max_errors = max(10, len(timestamps) // 10)  # 最多允许10%的周期失败

        for i, ts in enumerate(timestamps):
            # 设置当前虚拟时间
            spot_api.current_time = ts

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

            # 记录资金曲线
            total_value = self._calculate_total_value(spot_api)

            self.balance_history.append({
                'timestamp': ts,
                'total_value': total_value,
                'balances': dict(spot_api.balances),
            })

            # 进度显示（每10%或至少每100个周期）
            progress_step = max(1, len(timestamps) // 10)
            if (i + 1) % progress_step == 0:
                progress = (i + 1) / len(timestamps) * 100
                print(f"⏳ 进度: {progress:.0f}% | 总资产: ${total_value:.2f}")

        # 5. 输出统计
        if error_count > 0:
            print(f"\n⚠️  警告：回测过程中发生 {error_count} 次错误")

        self._print_summary(spot_api, initial_balance)

        return spot_api

    def _calculate_total_value(self, api: SpotBacktestAPIManager) -> float:
        """计算总资产价值（折算为USDT）"""
        total = 0.0

        for symbol, balance in api.balances.items():
            if balance == 0:
                continue

            if symbol == api.bridge_symbol:
                total += balance
            else:
                # 获取价格并折算
                ticker_symbol = symbol + api.bridge_symbol
                price = api.get_ticker_price(ticker_symbol)
                if price:
                    total += balance * price

        return total

    def _generate_timestamps(self, start_date: str, end_date: str, interval: str) -> List[int]:
        """生成时间戳序列"""
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')

        interval_mapping = {
            '1m': timedelta(minutes=1),
            '5m': timedelta(minutes=5),
            '15m': timedelta(minutes=15),
            '1h': timedelta(hours=1),
            '4h': timedelta(hours=4),
            '1d': timedelta(days=1),
        }

        interval_delta = interval_mapping.get(interval, timedelta(hours=1))

        timestamps = []
        current = start

        while current <= end:
            timestamps.append(int(current.timestamp() * 1000))
            current += interval_delta

        return timestamps

    def _ts_to_str(self, ts: int) -> str:
        """时间戳转字符串"""
        return datetime.fromtimestamp(ts / 1000).strftime('%Y-%m-%d %H:%M')

    def _print_summary(self, api: SpotBacktestAPIManager, initial_balance: float):
        """输出回测统计"""
        print("\n" + "=" * 60)
        print("📈 回测结果")
        print("=" * 60)

        # 基本统计
        final_value = self.balance_history[-1]['total_value'] if self.balance_history else initial_balance
        total_return = (final_value - initial_balance) / initial_balance * 100

        print(f"初始资金: ${initial_balance:.2f}")
        print(f"最终资产: ${final_value:.2f}")
        print(f"总收益率: {total_return:+.2f}%")

        # 最大回撤
        max_drawdown = self._calculate_max_drawdown()
        print(f"最大回撤: {max_drawdown:.2f}%")

        # 最终持仓
        print(f"\n最终持仓:")
        for symbol, balance in api.balances.items():
            if balance > 0:
                print(f"  {symbol}: {balance:.8f}")

        print("=" * 60)

    def _calculate_max_drawdown(self) -> float:
        """计算最大回撤"""
        if not self.balance_history:
            return 0.0

        peak = self.balance_history[0]['total_value']
        max_dd = 0.0

        for point in self.balance_history:
            value = point['total_value']
            if value > peak:
                peak = value
            dd = (peak - value) / peak * 100
            max_dd = max(max_dd, dd)

        return max_dd
