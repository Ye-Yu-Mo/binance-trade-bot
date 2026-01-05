"""
横截面趋势锁定策略 (Cross-Sectional Trend Lock Strategy)

核心理念：
- 只在市场出现极端横截面趋势时出手
- 长时间不交易是正常状态
- 收益高度集中于少数交易
- 拒绝频繁轮动和提前止盈

状态机：
    IDLE (防守态) → TREND_LOCK (进攻态) → IDLE
"""
from datetime import datetime, timedelta
from collections import deque, defaultdict
from typing import Dict, List, Optional, Tuple
import numpy as np

from binance_trade_bot.auto_trader import AutoTrader
from binance_trade_bot.models import Coin


class CrossSectionalState:
    """横截面状态追踪"""

    def __init__(self):
        # 核心状态
        self.mode = "IDLE"  # IDLE | TREND_LOCK
        self.locked_coin: Optional[Coin] = None
        self.lock_entry_price: Optional[float] = None
        self.lock_entry_time: Optional[datetime] = None

        # 历史数据：相对价格比率 {(coin_i, coin_j): deque([ratio1, ratio2, ...])}
        self.price_ratios: Dict[Tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=1000))

        # 历史数据：相对优势评分 {(coin_i, coin_j): deque([score1, score2, ...])}
        self.dominance_scores: Dict[Tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=1000))

        # 触发条件追踪
        self.trigger_count: Dict[str, int] = defaultdict(int)  # {coin: consecutive_count}
        self.collapse_count: int = 0  # 崩塌连续计数

    def reset_lock(self):
        """重置锁定状态"""
        self.mode = "IDLE"
        self.locked_coin = None
        self.lock_entry_price = None
        self.lock_entry_time = None
        self.collapse_count = 0


class Strategy(AutoTrader):
    """横截面趋势锁定策略"""

    # ========== 策略参数 ==========
    BREADTH_THRESHOLD = 0.7        # 广度条件：需要相对70%币种满足"极端强势"
    EXTREME_PERCENTILE = 0.98      # pair-level 极端性阈值：98%分位（更安静）
    GLOBAL_PERCENTILE = 0.95       # 全局二次检查：95%分位（更安静）

    PERSISTENCE_PERIODS = 3        # 进入持续性：3个周期（你可按侦察频率调整）
    EMA_WINDOW = 20                # EMA窗口（按侦察频率调整；不是"天"）
    MIN_HISTORY_REQUIRED = 50      # 全局最小历史数据点（预热期）

    EXIT_PERSISTENCE = 3           # 退出持续性：3个周期
    EXIT_COLLAPSE_THRESHOLD = 0.65 # 退出：>=65% eligible pairs 发生"结构崩塌"
    EXIT_LOW_PERCENTILE = 0.30     # 退出低分位阈值：30%分位（可调更狠：0.20/0.10）

    FEE_RATE = 0.00075             # 单边手续费（按交易所/账户实际填写）
    FEE_ROUND_TRIP = 1 - (1 - FEE_RATE) ** 2  # 双边手续费（round-trip），修复硬编码

    def initialize(self):
        super().initialize()
        self.state = CrossSectionalState()
        self.logger.info("=" * 60)
        self.logger.info("🎯 横截面趋势锁定策略已启动")
        self.logger.info("=" * 60)
        self.logger.info(f"广度条件: {self.BREADTH_THRESHOLD*100:.0f}% 币种")
        self.logger.info(f"极端性阈值: {self.EXTREME_PERCENTILE*100:.0f}% 分位")
        self.logger.info(f"持续性要求: {self.PERSISTENCE_PERIODS} 个周期")
        self.logger.info(f"EMA 窗口: {self.EMA_WINDOW} 周期")
        self.logger.info("=" * 60)
        self.logger.info("⚠️  注意：本策略接受长时间不交易，这是正常状态")
        self.logger.info("=" * 60)

    def scout(self):
        """主侦察循环"""
        if self.state.mode == "IDLE":
            self._scout_idle_mode()
        elif self.state.mode == "TREND_LOCK":
            self._scout_trend_lock_mode()

    # ========== IDLE 模式逻辑 ==========

    def _scout_idle_mode(self):
        """防守态：持有桥币，等待横截面极端事件"""
        current_coin = self.db.get_current_coin()

        # 确保持有桥币
        if current_coin != self.config.BRIDGE:
            # self.logger.info(f"⚙️  IDLE模式：切换到桥币 {self.config.BRIDGE.symbol}")
            # 只有当前币不是桥币且不是None时才卖出
            if current_coin and current_coin.symbol != self.config.BRIDGE.symbol:
                self.manager.sell_alt(current_coin, self.config.BRIDGE)
            self.db.set_current_coin(self.config.BRIDGE)
            return

        # 计算所有币种的横截面强度
        coins = self._get_tradeable_coins()
        if len(coins) < 3:
            self.logger.info("⚠️  可交易币种不足，跳过侦察")
            return

        # 获取所有价格
        prices = self._get_all_prices(coins)
        if not prices:
            return

        # 更新相对价格比率和优势评分
        self._update_cross_sectional_data(coins, prices)

        # 检测横截面极端事件
        dominant_coin, score = self._detect_extreme_event(coins, prices)

        if dominant_coin:
            # 修复：多币种 streak 追踪，而不是单冠军连续制
            # 先衰减所有未触发币种的计数
            for sym in list(self.state.trigger_count.keys()):
                if sym != dominant_coin.symbol:
                    self.state.trigger_count[sym] = max(0, self.state.trigger_count[sym] - 1)
                    if self.state.trigger_count[sym] == 0:
                        del self.state.trigger_count[sym]

            # 增加当前触发币种的计数
            self.state.trigger_count[dominant_coin.symbol] += 1

            self.logger.info(
                f"🔥 检测到强势币种: {dominant_coin.symbol} "
                f"(横截面评分: {score:.2%}, "
                f"连续周期: {self.state.trigger_count[dominant_coin.symbol]}/{self.PERSISTENCE_PERIODS})"
            )

            # 检查是否满足持续性条件
            if self.state.trigger_count[dominant_coin.symbol] >= self.PERSISTENCE_PERIODS:
                self._trigger_trend_lock(dominant_coin, prices[dominant_coin.symbol])
        else:
            # 没有极端事件，衰减所有计数
            for sym in list(self.state.trigger_count.keys()):
                self.state.trigger_count[sym] = max(0, self.state.trigger_count[sym] - 1)
                if self.state.trigger_count[sym] == 0:
                    del self.state.trigger_count[sym]

    # ========== TREND_LOCK 模式逻辑 ==========

    def _scout_trend_lock_mode(self):
        """进攻态：锁定强势币种，禁止轮动，等待横截面崩塌"""
        if not self.state.locked_coin:
            self.logger.error("❌ TREND_LOCK模式但没有锁定币种，强制回到IDLE")
            self.state.reset_lock()
            return

        locked_coin = self.state.locked_coin
        current_coin = self.db.get_current_coin()

        # 修复 None bug：检查 current_coin 是否为 None
        if not current_coin or current_coin.symbol != locked_coin.symbol:
            self.logger.warning(f"⚠️  持仓不符，重新锁定 {locked_coin.symbol}")
            if current_coin and current_coin != self.config.BRIDGE:
                self.manager.sell_alt(current_coin, self.config.BRIDGE)
            self.manager.buy_alt(locked_coin, self.config.BRIDGE)
            self.db.set_current_coin(locked_coin)
            return

        # 获取当前价格
        coins = self._get_tradeable_coins()
        prices = self._get_all_prices(coins)

        if locked_coin.symbol not in prices:
            self.logger.warning(f"⚠️  无法获取 {locked_coin.symbol} 价格")
            return

        current_price = prices[locked_coin.symbol]

        # 计算持仓盈亏
        pnl_pct = (current_price - self.state.lock_entry_price) / self.state.lock_entry_price * 100
        hold_duration = datetime.now() - self.state.lock_entry_time

        self.logger.info(
            f"🔒 TREND_LOCK: {locked_coin.symbol} | "
            f"入场: {self.state.lock_entry_price:.8f} | "
            f"当前: {current_price:.8f} | "
            f"盈亏: {pnl_pct:+.2f}% | "
            f"持续: {hold_duration.days}天{hold_duration.seconds//3600}小时"
        )

        # 更新横截面数据
        self._update_cross_sectional_data(coins, prices)

        # 检测横截面崩塌
        if self._detect_collapse(locked_coin, coins, prices):
            self.state.collapse_count += 1
            self.logger.info(
                f"⚠️  横截面崩塌信号 "
                f"({self.state.collapse_count}/{self.EXIT_PERSISTENCE})"
            )

            if self.state.collapse_count >= self.EXIT_PERSISTENCE:
                self._exit_trend_lock(current_price, pnl_pct, hold_duration)
        else:
            # 没有崩塌，重置计数
            if self.state.collapse_count > 0:
                self.logger.info("✅ 横截面强势恢复")
            self.state.collapse_count = 0

    # ========== 核心计算函数 ==========

    def _get_tradeable_coins(self) -> List[Coin]:
        """获取可交易币种列表（排除桥币）"""
        return [coin for coin in self.db.get_coins() if coin != self.config.BRIDGE]

    def _get_all_prices(self, coins: List[Coin]) -> Dict[str, float]:
        """获取所有币种的当前价格"""
        prices = {}
        for coin in coins:
            pair = coin + self.config.BRIDGE
            price = self.manager.get_ticker_price(pair)
            if price and price > 0:
                prices[coin.symbol] = price
        return prices

    def _update_cross_sectional_data(self, coins: List[Coin], prices: Dict[str, float]):
        """更新相对价格比率和优势评分"""
        symbols = [c.symbol for c in coins if c.symbol in prices]

        for i, sym_i in enumerate(symbols):
            for j, sym_j in enumerate(symbols):
                if i == j:
                    continue

                # 计算相对价格比率 R[i,j] = P[i] / P[j]
                ratio = prices[sym_i] / prices[sym_j]
                key = (sym_i, sym_j)
                self.state.price_ratios[key].append(ratio)

                # 计算 EMA（作为中期参考）
                ratios_list = list(self.state.price_ratios[key])
                if len(ratios_list) >= 2:
                    ema_ratio = self._calculate_ema(ratios_list, self.EMA_WINDOW)

                    # 计算相对优势评分 S[i,j] = (1-fee_roundtrip) * R[i,j] / EMA(R) - 1
                    # 修复：用双边手续费而不是单边
                    score = (1 - self.FEE_ROUND_TRIP) * ratio / ema_ratio - 1
                    self.state.dominance_scores[key].append(score)

    def _calculate_ema(self, data: List[float], window: int) -> float:
        """计算指数移动平均"""
        if len(data) == 0:
            return 0.0

        # 如果数据不足window，使用简单平均
        if len(data) < window:
            return np.mean(data)

        # 计算 EMA
        alpha = 2.0 / (window + 1)
        ema = data[0]
        for value in data[1:]:
            ema = alpha * value + (1 - alpha) * ema
        return ema

    def _detect_extreme_event(self, coins: List[Coin], prices: Dict[str, float]) -> Tuple[Optional[Coin], float]:
        """
        检测横截面极端事件（Patch版）

        修复点：
        1) 分位数计算排除当前点（避免 self-referential bias）
        2) breadth 分母只算 eligible pairs（有足够历史且能算阈值）
        3) 保留 pair-level 罕见性 + 全局二次检查（但全局阈值也排除当前点）

        返回：(dominant_coin, score) 或 (None, 0)
        """
        symbols = [c.symbol for c in coins if c.symbol in prices]
        if len(symbols) < 3:
            return None, 0.0

        # === 构建全局历史分布（排除每个pair的当前点）===
        all_scores_excl_current: List[float] = []
        for key, dq in self.state.dominance_scores.items():
            if len(dq) >= 2:
                # 排除当前点：dq[-1]
                all_scores_excl_current.extend(list(dq)[:-1])

        # 预热期：全局历史数据不足
        if len(all_scores_excl_current) < self.MIN_HISTORY_REQUIRED:
            # 可选：降低日志频率避免刷屏
            if len(all_scores_excl_current) % 25 == 0:
                self.logger.info(f"📊 预热中：{len(all_scores_excl_current)}/{self.MIN_HISTORY_REQUIRED} 全局历史数据点")
            return None, 0.0

        # 全局二次检查阈值（也必须基于排除当前点的历史）
        global_threshold = float(np.percentile(all_scores_excl_current, self.GLOBAL_PERCENTILE * 100))

        best_coin: Optional[Coin] = None
        best_avg_score: float = float("-inf")

        for sym_i in symbols:
            current_scores: List[float] = []
            eligible_count = 0
            strong_count = 0

            for sym_j in symbols:
                if sym_i == sym_j:
                    continue

                key = (sym_i, sym_j)
                dq = self.state.dominance_scores.get(key)
                if not dq or len(dq) < 2:
                    # 需要至少 2 个点，才能排除当前点还剩历史
                    continue

                current_score = float(dq[-1])
                history_excl_current = list(dq)[:-1]  # 排除当前点

                # pair 历史不足：不参与 breadth 分母（核心修复点 #2）
                if len(history_excl_current) < 20:
                    continue

                eligible_count += 1
                current_scores.append(current_score)

                pair_threshold = float(np.percentile(history_excl_current, self.EXTREME_PERCENTILE * 100))
                if current_score >= pair_threshold:
                    strong_count += 1

            # 没有足够 eligible pairs，直接跳过（避免误触发）
            if eligible_count == 0:
                continue

            # breadth：用 eligible pairs 作分母（核心修复点 #2）
            breadth_ratio = strong_count / eligible_count
            if breadth_ratio < self.BREADTH_THRESHOLD:
                continue

            # 结构强度：用 eligible pairs 的当前 score 均值
            avg_score = float(np.mean(current_scores)) if current_scores else float("-inf")

            # 全局二次检查：avg_score 必须 >= global_threshold（也基于排除当前点历史）
            if avg_score < global_threshold:
                continue

            if avg_score > best_avg_score:
                best_avg_score = avg_score
                best_coin = next((c for c in coins if c.symbol == sym_i), None)

        if best_coin is None:
            return None, 0.0

        return best_coin, best_avg_score

    def _detect_collapse(self, locked_coin: Coin, coins: List[Coin], prices: Dict[str, float]) -> bool:
        """
        检测横截面崩塌（Patch版）

        修复点：
        3) Exit 用低分位阈值（pair-level lower percentile），而不是 score<0

        思路：
        - 对每个 pair (locked, j)：
          * 用历史（排除当前点）算 exit_threshold = percentile(history_excl_current, EXIT_LOW_PERCENTILE)
          * 若 current_score <= exit_threshold，视为该pair"崩塌"
        - 崩塌比例 >= EXIT_COLLAPSE_THRESHOLD 则返回 True

        注意：
        - EXIT_LOW_PERCENTILE 这里默认用 self.EXIT_LOW_PERCENTILE（若你没有该参数，会回退到 0.30）
        - 同样排除当前点，避免自嗨偏置
        """
        symbols = [c.symbol for c in coins if c.symbol in prices]
        locked_sym = locked_coin.symbol

        if locked_sym not in symbols:
            return True  # 数据缺失，直接视为崩塌

        # 低分位阈值：你可之后自己调成更"残忍"
        exit_low_p = getattr(self, "EXIT_LOW_PERCENTILE", 0.30)  # 默认 30% 分位

        eligible_count = 0
        collapse_count = 0

        for sym_j in symbols:
            if locked_sym == sym_j:
                continue

            key = (locked_sym, sym_j)
            dq = self.state.dominance_scores.get(key)
            if not dq or len(dq) < 2:
                continue

            current_score = float(dq[-1])
            history_excl_current = list(dq)[:-1]

            # 历史不足：不参与退出判定（避免因缺数据误判崩塌）
            if len(history_excl_current) < 20:
                continue

            eligible_count += 1

            exit_threshold = float(np.percentile(history_excl_current, exit_low_p * 100))
            if current_score <= exit_threshold:
                collapse_count += 1

        # 如果没有足够eligible pairs，保守处理：视为崩塌
        # （否则会出现"数据不够就永远不退出"的死锁）
        if eligible_count == 0:
            return True

        collapse_ratio = collapse_count / eligible_count
        return collapse_ratio >= self.EXIT_COLLAPSE_THRESHOLD

    # ========== 状态转换 ==========

    def _trigger_trend_lock(self, coin: Coin, entry_price: float):
        """触发趋势锁定"""
        self.logger.info("=" * 60)
        self.logger.info(f"🚀 触发 TREND_LOCK: {coin.symbol}")
        self.logger.info(f"   入场价: {entry_price:.8f}")
        self.logger.info(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.logger.info("=" * 60)

        # 切换到目标币种
        current_coin = self.db.get_current_coin()
        if current_coin != coin:
            if current_coin != self.config.BRIDGE:
                self.manager.sell_alt(current_coin, self.config.BRIDGE)
            self.manager.buy_alt(coin, self.config.BRIDGE)
            self.db.set_current_coin(coin)

        # 更新状态
        self.state.mode = "TREND_LOCK"
        self.state.locked_coin = coin
        self.state.lock_entry_price = entry_price
        self.state.lock_entry_time = datetime.now()
        self.state.trigger_count.clear()
        self.state.collapse_count = 0

    def _exit_trend_lock(self, exit_price: float, pnl_pct: float, duration: timedelta):
        """退出趋势锁定"""
        self.logger.info("=" * 60)
        self.logger.info(f"🏁 退出 TREND_LOCK: {self.state.locked_coin.symbol}")
        self.logger.info(f"   入场价: {self.state.lock_entry_price:.8f}")
        self.logger.info(f"   出场价: {exit_price:.8f}")
        self.logger.info(f"   盈亏: {pnl_pct:+.2f}%")
        self.logger.info(f"   持续时间: {duration.days}天 {duration.seconds//3600}小时")
        self.logger.info("=" * 60)

        # 卖出回桥币
        self.manager.sell_alt(self.state.locked_coin, self.config.BRIDGE)
        self.db.set_current_coin(self.config.BRIDGE)

        # 重置状态
        self.state.reset_lock()
