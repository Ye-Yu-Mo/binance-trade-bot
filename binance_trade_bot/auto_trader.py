from datetime import datetime
from typing import Dict, List

from sqlalchemy.orm import Session

from .binance_api_manager import BinanceAPIManager
from .config import Config
from .database import Database
from .logger import Logger
from .models import Coin, CoinValue, Pair


class AutoTrader:
    def __init__(
        self,
        binance_manager: BinanceAPIManager,
        database: Database,
        logger: Logger,
        config: Config,
    ):
        self.manager = binance_manager
        self.db = database
        self.logger = logger
        self.config = config

    def initialize(self):
        self.initialize_trade_thresholds()

    def transaction_through_bridge(self, pair: Pair):
        """
        Jump from the source coin to the destination coin through bridge coin
        """
        can_sell = False
        balance = self.manager.get_currency_balance(pair.from_coin.symbol)
        from_coin_price = self.manager.get_ticker_price(pair.from_coin + self.config.BRIDGE)

        if balance and balance * from_coin_price > self.manager.get_min_notional(
            pair.from_coin.symbol, self.config.BRIDGE.symbol
        ):
            can_sell = True
        else:
            self.logger.info("Skipping sell")

        if can_sell and self.manager.sell_alt(pair.from_coin, self.config.BRIDGE) is None:
            self.logger.info("Couldn't sell, going back to scouting mode...")
            return None

        result = self.manager.buy_alt(pair.to_coin, self.config.BRIDGE)
        if result is not None:
            self.db.set_current_coin(pair.to_coin)
            self.update_trade_threshold(pair.to_coin, result.price)
            return result

        self.logger.info("Couldn't buy, going back to scouting mode...")
        return None

    def update_trade_threshold(self, coin: Coin, coin_price: float):
        """
        Update all the coins with the threshold of buying the current held coin
        """

        if coin_price is None:
            self.logger.info(f"Skipping update... current coin {coin + self.config.BRIDGE} not found")
            return

        session: Session
        with self.db.db_session() as session:
            for pair in session.query(Pair).filter(Pair.to_coin == coin):
                from_coin_price = self.manager.get_ticker_price(pair.from_coin + self.config.BRIDGE)

                if from_coin_price is None:
                    self.logger.info(f"Skipping update for coin {pair.from_coin + self.config.BRIDGE} not found")
                    continue

                pair.ratio = from_coin_price / coin_price

    def initialize_trade_thresholds(self):
        """
        Initialize the buying threshold of all the coins for trading between them

        重要：每次启动都重新初始化所有 ratio，避免使用过期数据导致永远不交易
        """
        session: Session
        with self.db.db_session() as session:
            # 移除 Pair.ratio.is_(None) 过滤条件，强制重新初始化所有 ratio
            for pair in session.query(Pair).all():
                if not pair.from_coin.enabled or not pair.to_coin.enabled:
                    continue

                from_coin_price = self.manager.get_ticker_price(pair.from_coin + self.config.BRIDGE)
                if from_coin_price is None:
                    self.logger.info(f"Skipping initializing {pair.from_coin + self.config.BRIDGE}, symbol not found")
                    continue

                to_coin_price = self.manager.get_ticker_price(pair.to_coin + self.config.BRIDGE)
                if to_coin_price is None:
                    self.logger.info(f"Skipping initializing {pair.to_coin + self.config.BRIDGE}, symbol not found")
                    continue

                old_ratio = pair.ratio
                pair.ratio = from_coin_price / to_coin_price

                if old_ratio is None:
                    self.logger.info(f"Initializing {pair.from_coin} vs {pair.to_coin}: ratio={pair.ratio:.4f}")
                else:
                    change_pct = (pair.ratio - old_ratio) / old_ratio * 100 if old_ratio != 0 else 0
                    self.logger.info(
                        f"Resetting {pair.from_coin} vs {pair.to_coin}: "
                        f"{old_ratio:.4f} → {pair.ratio:.4f} ({change_pct:+.2f}%)"
                    )

    def scout(self):
        """
        Scout for potential jumps from the current coin to another coin
        """
        raise NotImplementedError()

    def _get_ratios(self, coin: Coin, coin_price):
        """
        Given a coin, get the current price ratio for every other enabled coin
        """
        ratio_dict: Dict[Pair, float] = {}

        for pair in self.db.get_pairs_from(coin):
            optional_coin_price = self.manager.get_ticker_price(pair.to_coin + self.config.BRIDGE)

            if optional_coin_price is None:
                self.logger.info(f"Skipping scouting... optional coin {pair.to_coin + self.config.BRIDGE} not found")
                continue

            self.db.log_scout(pair, pair.ratio, coin_price, optional_coin_price)

            # Obtain (current coin)/(optional coin)
            coin_opt_coin_ratio = coin_price / optional_coin_price

            # Fees
            from_fee = self.manager.get_fee(pair.from_coin, self.config.BRIDGE, True)
            to_fee = self.manager.get_fee(pair.to_coin, self.config.BRIDGE, False)
            transaction_fee = from_fee + to_fee - from_fee * to_fee

            if self.config.USE_MARGIN == "yes":
                ratio_dict[pair] = (
                    (1 - transaction_fee) * coin_opt_coin_ratio / pair.ratio - 1 - self.config.SCOUT_MARGIN / 100
                )
            else:
                ratio_dict[pair] = (
                    coin_opt_coin_ratio - transaction_fee * self.config.SCOUT_MULTIPLIER * coin_opt_coin_ratio
                ) - pair.ratio

            # 输出扫描结果
            if self.config.USE_MARGIN == "yes":
                # margin 模式：ratio_dict[pair] 已经是相对百分比
                profit_pct = ratio_dict[pair] * 100
                self.logger.info(
                    f"  └─ {pair.to_coin.symbol}: 价格 {optional_coin_price:.8f}, "
                    f"当前比率 {coin_opt_coin_ratio:.4f}, 预期收益 {profit_pct:+.2f}%"
                )
            else:
                # multiplier 模式：ratio_dict[pair] 是绝对差值
                score = ratio_dict[pair]
                relative_change = (score / pair.ratio * 100) if pair.ratio != 0 else 0
                self.logger.info(
                    f"  └─ {pair.to_coin.symbol}: 价格 {optional_coin_price:.8f}, "
                    f"比率 {coin_opt_coin_ratio:.4f}/{pair.ratio:.4f}, "
                    f"评分 {score:+.4f} ({relative_change:+.2f}%)"
                )
        return ratio_dict

    def _jump_to_best_coin(self, coin: Coin, coin_price: float):
        """
        Given a coin, search for a coin to jump to
        """
        ratio_dict = self._get_ratios(coin, coin_price)

        # keep only ratios bigger than zero
        positive_ratios = {k: v for k, v in ratio_dict.items() if v > 0}

        # if we have any viable options, pick the one with the biggest ratio
        if positive_ratios:
            best_pair = max(positive_ratios, key=positive_ratios.get)
            best_score = positive_ratios[best_pair]

            if self.config.USE_MARGIN == "yes":
                best_profit_pct = best_score * 100
                self.logger.info(
                    f"✅ 决策: 跳转 {coin.symbol} → {best_pair.to_coin.symbol}, "
                    f"预期收益 {best_profit_pct:+.2f}%"
                )
            else:
                relative_change = (best_score / best_pair.ratio * 100) if best_pair.ratio != 0 else 0
                self.logger.info(
                    f"✅ 决策: 跳转 {coin.symbol} → {best_pair.to_coin.symbol}, "
                    f"评分 {best_score:+.4f} ({relative_change:+.2f}%)"
                )
            self.transaction_through_bridge(best_pair)
        else:
            self.logger.info(
                f"⏸️  决策: 持有 {coin.symbol}, 暂无更优选项 "
                f"(扫描 {len(ratio_dict)} 个币种，均无正收益)"
            )

    def bridge_scout(self):
        """
        If we have any bridge coin leftover, buy a coin with it that we won't immediately trade out of
        """
        bridge_balance = self.manager.get_currency_balance(self.config.BRIDGE.symbol)

        for coin in self.db.get_coins():
            current_coin_price = self.manager.get_ticker_price(coin + self.config.BRIDGE)

            if current_coin_price is None:
                continue

            ratio_dict = self._get_ratios(coin, current_coin_price)
            if not any(v > 0 for v in ratio_dict.values()):
                # There will only be one coin where all the ratios are negative. When we find it, buy it if we can
                if bridge_balance > self.manager.get_min_notional(coin.symbol, self.config.BRIDGE.symbol):
                    self.logger.info(f"Will be purchasing {coin} using bridge coin")
                    self.manager.buy_alt(coin, self.config.BRIDGE)
                    return coin
        return None

    def update_values(self):
        """
        Log current value state of all altcoin balances against BTC and USDT in DB.
        """
        now = datetime.now()

        session: Session
        with self.db.db_session() as session:
            coins: List[Coin] = session.query(Coin).all()
            for coin in coins:
                balance = self.manager.get_currency_balance(coin.symbol)
                if balance == 0:
                    continue
                usd_value = self.manager.get_ticker_price(coin + "USDT")
                btc_value = self.manager.get_ticker_price(coin + "BTC")
                cv = CoinValue(coin, balance, usd_value, btc_value, datetime=now)
                session.add(cv)
                self.db.send_update(cv)
