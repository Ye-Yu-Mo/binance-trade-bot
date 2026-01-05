"""
Binance 期货 API 管理器
独立于现货API的期货交易模块
"""
from dataclasses import dataclass
from typing import Optional, Dict, Literal
import time
import math

from binance.client import Client
from binance.exceptions import BinanceAPIException
from cachetools import TTLCache, cached

from .config import Config
from .database import Database
from .logger import Logger


@dataclass
class FuturesPosition:
    """
    期货仓位数据结构 - 只包含仓位本身，不包含策略细节
    """
    symbol: str
    side: Literal['LONG', 'SHORT']
    quantity: float
    entry_price: float
    mark_price: float
    unrealized_profit: float
    liquidation_price: float


class BinanceFuturesAPIManager:
    """
    Binance 期货 API 管理器
    设计原则：
    1. 数据结构优先 - FuturesPosition 只关注仓位
    2. 消除分支 - 用字典替代 if/else
    3. 实用主义 - 只处理真实的 API 异常
    """

    # 操作映射表：消除 4 个方法变成字典查询
    _OPERATION_MAP = {
        ('open', 'long'):   {'side': 'BUY',  'positionSide': 'LONG'},
        ('open', 'short'):  {'side': 'SELL', 'positionSide': 'SHORT'},
        ('close', 'long'):  {'side': 'SELL', 'positionSide': 'LONG'},
        ('close', 'short'): {'side': 'BUY',  'positionSide': 'SHORT'},
    }

    def __init__(self, config: Config, db: Database, logger: Logger, testnet: bool = True):
        """初始化期货客户端"""
        requests_params = {}
        if config.PROXY:
            requests_params['proxies'] = {
                'http': config.PROXY,
                'https': config.PROXY,
            }
            logger.info(f"Using proxy: {config.PROXY}")

        self.binance_client = Client(
            config.BINANCE_API_KEY,
            config.BINANCE_API_SECRET_KEY,
            tld=config.BINANCE_TLD,
            testnet=testnet,
            requests_params=requests_params if requests_params else None,
        )
        self.db = db
        self.logger = logger
        self.config = config
        self.testnet = testnet

    def setup_futures_mode(self, leverage: int = 3) -> bool:
        """
        设置期货模式：双向持仓 + 杠杆
        必须在交易前调用一次
        """
        try:
            # 设置双向持仓模式
            try:
                self.binance_client.futures_change_position_mode(dualSidePosition=True)
                self.logger.info("✅ Enabled hedge mode (dual position mode)")
            except BinanceAPIException as e:
                # 如果已经是双向持仓模式，会返回错误，忽略
                if 'No need to change position side' in str(e) or 'Position side cannot be changed' in str(e):
                    self.logger.info("✅ Already in hedge mode (or has open positions)")
                else:
                    raise

            self.logger.info(f"Leverage will be set to {leverage}x per symbol")
            return True

        except BinanceAPIException as e:
            self.logger.error(f"❌ Failed to setup futures mode: {e}")
            return False

    def set_leverage(self, symbol: str, leverage: int) -> bool:
        """为指定交易对设置杠杆"""
        try:
            self.binance_client.futures_change_leverage(symbol=symbol, leverage=leverage)
            self.logger.info(f"✅ Set leverage to {leverage}x for {symbol}")
            return True
        except BinanceAPIException as e:
            self.logger.error(f"❌ Failed to set leverage for {symbol}: {e}")
            return False

    @cached(cache=TTLCache(maxsize=100, ttl=43200))
    def get_symbol_precision(self, symbol: str) -> Dict[str, int]:
        """
        获取交易对的精度信息（缓存12小时）
        Returns: {'quantity_precision': 3, 'price_precision': 2}
        """
        try:
            info = self.binance_client.futures_exchange_info()
            for s in info['symbols']:
                if s['symbol'] == symbol:
                    return {
                        'quantity_precision': s['quantityPrecision'],
                        'price_precision': s['pricePrecision'],
                    }
            return {'quantity_precision': 3, 'price_precision': 2}  # 默认值
        except BinanceAPIException as e:
            self.logger.error(f"Failed to get precision for {symbol}: {e}")
            return {'quantity_precision': 3, 'price_precision': 2}

    def _format_quantity(self, symbol: str, quantity: float) -> float:
        """格式化数量到正确的精度"""
        precision = self.get_symbol_precision(symbol)['quantity_precision']
        return math.floor(quantity * 10**precision) / 10**precision

    def _execute_operation(self, operation: str, side: str, symbol: str, quantity: float) -> Optional[Dict]:
        """
        统一的开仓/平仓执行逻辑
        Args:
            operation: 'open' 或 'close'
            side: 'long' 或 'short'
            symbol: 交易对，如 'BTCUSDT'
            quantity: 数量
        """
        params = self._OPERATION_MAP.get((operation, side))
        if not params:
            self.logger.error(f"Invalid operation: {operation} {side}")
            return None

        # 格式化数量
        formatted_qty = self._format_quantity(symbol, quantity)

        return self._create_order(
            symbol=symbol,
            side=params['side'],
            positionSide=params['positionSide'],
            quantity=formatted_qty
        )

    def open_long(self, symbol: str, quantity: float) -> Optional[Dict]:
        """开多仓"""
        return self._execute_operation('open', 'long', symbol, quantity)

    def open_short(self, symbol: str, quantity: float) -> Optional[Dict]:
        """开空仓"""
        return self._execute_operation('open', 'short', symbol, quantity)

    def close_long(self, symbol: str, quantity: float) -> Optional[Dict]:
        """平多仓"""
        return self._execute_operation('close', 'long', symbol, quantity)

    def close_short(self, symbol: str, quantity: float) -> Optional[Dict]:
        """平空仓"""
        return self._execute_operation('close', 'short', symbol, quantity)

    def _create_order(self, symbol: str, side: str, positionSide: str, quantity: float,
                      price: Optional[float] = None, retry_count: int = 3) -> Optional[Dict]:
        """
        统一的订单创建逻辑（市价单）

        只处理真实的异常：
        - BinanceAPIException: API返回的错误
        - 网络超时会自动重试
        """
        for attempt in range(retry_count):
            try:
                # 修正：使用真实订单，不是测试订单
                order = self.binance_client.futures_create_order(
                    symbol=symbol,
                    side=side,
                    positionSide=positionSide,
                    type='MARKET',
                    quantity=quantity,
                )

                self.logger.info(
                    f"📊 Order created: {side} {quantity} {symbol} "
                    f"({positionSide}) - OrderID: {order.get('orderId', 'N/A')}"
                )
                return order

            except BinanceAPIException as e:
                self.logger.warning(f"API Error (attempt {attempt+1}/{retry_count}): {e}")
                if attempt == retry_count - 1:
                    self.logger.error(f"❌ Failed to create order after {retry_count} attempts")
                    return None
                time.sleep(1)

            except Exception as e:
                self.logger.warning(f"Unexpected error (attempt {attempt+1}/{retry_count}): {e}")
                if attempt == retry_count - 1:
                    return None
                time.sleep(1)

    def get_position(self, symbol: str, side: Literal['LONG', 'SHORT']) -> Optional[FuturesPosition]:
        """
        查询指定方向的仓位
        Returns: FuturesPosition 对象或 None（无仓位时）
        """
        try:
            positions = self.binance_client.futures_position_information(symbol=symbol)

            for pos in positions:
                # 双向持仓模式下会返回两个仓位：LONG 和 SHORT
                if pos['positionSide'] == side:
                    pos_amt = float(pos['positionAmt'])
                    # 仓位数量可能是负数（SHORT），取绝对值
                    if abs(pos_amt) > 0:
                        return FuturesPosition(
                            symbol=symbol,
                            side=side,
                            quantity=abs(pos_amt),
                            entry_price=float(pos['entryPrice']),
                            mark_price=float(pos['markPrice']),
                            unrealized_profit=float(pos['unRealizedProfit']),
                            liquidation_price=float(pos['liquidationPrice']) if pos['liquidationPrice'] else 0.0,
                        )

            return None  # 无仓位

        except BinanceAPIException as e:
            self.logger.error(f"Failed to get position for {symbol}: {e}")
            return None

    def get_all_positions(self) -> list[FuturesPosition]:
        """
        查询所有非零仓位
        """
        try:
            positions = self.binance_client.futures_position_information()
            result = []

            for pos in positions:
                pos_amt = float(pos['positionAmt'])
                if abs(pos_amt) > 0:
                    result.append(FuturesPosition(
                        symbol=pos['symbol'],
                        side=pos['positionSide'],
                        quantity=abs(pos_amt),
                        entry_price=float(pos['entryPrice']),
                        mark_price=float(pos['markPrice']),
                        unrealized_profit=float(pos['unRealizedProfit']),
                        liquidation_price=float(pos['liquidationPrice']) if pos['liquidationPrice'] else 0.0,
                    ))

            return result

        except BinanceAPIException as e:
            self.logger.error(f"Failed to get all positions: {e}")
            return []

    def get_usdt_balance(self) -> float:
        """查询 USDT 可用余额（期货账户）"""
        try:
            account = self.binance_client.futures_account()

            for asset in account['assets']:
                if asset['asset'] == 'USDT':
                    # availableBalance 是可用余额
                    return float(asset['availableBalance'])

            return 0.0

        except BinanceAPIException as e:
            self.logger.error(f"Failed to get USDT balance: {e}")
            return 0.0

    def get_mark_price(self, symbol: str) -> Optional[float]:
        """获取标记价格（用于计算未实现盈亏）"""
        try:
            ticker = self.binance_client.futures_mark_price(symbol=symbol)
            return float(ticker['markPrice'])
        except BinanceAPIException as e:
            self.logger.error(f"Failed to get mark price for {symbol}: {e}")
            return None
