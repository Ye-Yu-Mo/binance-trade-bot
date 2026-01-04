#!/usr/bin/env python3
"""
DOGEUSDT 一年回测脚本

功能：
1. 回测DOGEUSDT在最近一年的表现
2. 输出详细的收益报告
3. 统计交易次数和胜率
4. 生成CSV报告（可选）
"""

from datetime import datetime, timedelta
from binance_trade_bot import backtest
from binance_trade_bot.config import Config
import sys

def format_percentage(value, decimals=2):
    """格式化百分比"""
    return f"{value:+.{decimals}f}%"

def format_crypto(value, decimals=8):
    """格式化加密货币数量"""
    return f"{value:.{decimals}f}"

def print_separator(char="=", length=70):
    """打印分割线"""
    print(char * length)

def main():
    print_separator()
    print("DOGEUSDT 一年回测系统")
    print_separator()

    # 配置回测参数
    config = Config()

    # 确保DOGE在支持列表中
    if 'DOGE' not in config.SUPPORTED_COIN_LIST:
        print("[警告] DOGE不在支持列表中，正在添加...")
        config.SUPPORTED_COIN_LIST.append('DOGE')

    # 设置回测时间范围（使用UTC时间，对齐到分钟）
    end_date = datetime.utcnow().replace(second=0, microsecond=0)
    start_date = (end_date - timedelta(days=365)).replace(second=0, microsecond=0)

    print(f"\n[配置信息]")
    print(f"  回测币种: DOGE")
    print(f"  桥接币: {config.BRIDGE.symbol}")
    print(f"  开始时间: {start_date.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  结束时间: {end_date.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  回测天数: 365天")
    print(f"  使用策略: {config.STRATEGY}")
    print(f"  侦察倍数: {config.SCOUT_MULTIPLIER}")
    print(f"  侦察间隔: {config.SCOUT_SLEEP_TIME}秒")
    print_separator()

    # 初始化统计变量
    history = []
    trade_count = 0
    balance_changes = []
    last_balances = None

    print("\n[开始回测] 正在加载历史数据...")
    print("(首次运行需要下载数据，可能需要几分钟...)\n")

    iteration_count = 0

    try:
        for manager in backtest(
            start_date,
            end_date,
            starting_coin='DOGE',  # 指定从DOGE开始
            config=config
        ):
            iteration_count += 1

            # 计算BTC和桥接币价值
            btc_value = manager.collate_coins("BTC")
            bridge_value = manager.collate_coins(config.BRIDGE.symbol)
            current_balances = manager.balances.copy()

            # 检测交易（余额变化）
            if last_balances is not None and last_balances != current_balances:
                trade_count += 1
                # 记录余额变化
                balance_changes.append({
                    'time': manager.datetime,
                    'from': last_balances,
                    'to': current_balances,
                    'btc_value': btc_value,
                    'bridge_value': bridge_value
                })

            last_balances = current_balances
            history.append({
                'datetime': manager.datetime,
                'btc_value': btc_value,
                'bridge_value': bridge_value,
                'balances': current_balances.copy()
            })

            # 每100次迭代显示一次进度
            if iteration_count % 100 == 0:
                if len(history) > 1:
                    btc_diff = ((btc_value - history[0]['btc_value']) /
                               history[0]['btc_value'] * 100)
                    bridge_diff = ((bridge_value - history[0]['bridge_value']) /
                                  history[0]['bridge_value'] * 100)

                    print(f"[进度 {iteration_count:4d}] "
                          f"{manager.datetime.strftime('%Y-%m-%d %H:%M')} | "
                          f"BTC: {format_percentage(btc_diff, 2)} | "
                          f"{config.BRIDGE.symbol}: {format_percentage(bridge_diff, 2)} | "
                          f"交易: {trade_count}次")

    except KeyboardInterrupt:
        print("\n\n[中断] 用户终止回测")
        if len(history) < 2:
            print("数据不足，无法生成报告")
            sys.exit(0)

    except Exception as e:
        print(f"\n[错误] 回测过程中出现异常: {e}")
        import traceback
        traceback.print_exc()
        if len(history) < 2:
            sys.exit(1)

    # 生成最终报告
    print("\n")
    print_separator("=")
    print("回测完成！生成最终报告")
    print_separator("=")

    if len(history) < 2:
        print("[错误] 历史数据不足，无法生成报告")
        sys.exit(1)

    # 计算收益率
    initial = history[0]
    final = history[-1]

    initial_btc = initial['btc_value']
    final_btc = final['btc_value']
    initial_bridge = initial['bridge_value']
    final_bridge = final['bridge_value']

    btc_return = (final_btc - initial_btc) / initial_btc * 100
    bridge_return = (final_bridge - initial_bridge) / initial_bridge * 100

    # 计算最大回撤
    max_btc = initial_btc
    max_drawdown_btc = 0
    max_bridge = initial_bridge
    max_drawdown_bridge = 0

    for record in history:
        # BTC最大回撤
        if record['btc_value'] > max_btc:
            max_btc = record['btc_value']
        drawdown_btc = (max_btc - record['btc_value']) / max_btc * 100
        if drawdown_btc > max_drawdown_btc:
            max_drawdown_btc = drawdown_btc

        # Bridge最大回撤
        if record['bridge_value'] > max_bridge:
            max_bridge = record['bridge_value']
        drawdown_bridge = (max_bridge - record['bridge_value']) / max_bridge * 100
        if drawdown_bridge > max_drawdown_bridge:
            max_drawdown_bridge = drawdown_bridge

    # 打印报告
    print(f"\n{'='*20} 时间统计 {'='*20}")
    print(f"  回测开始: {initial['datetime'].strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  回测结束: {final['datetime'].strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  回测天数: {(final['datetime'] - initial['datetime']).days}天")
    print(f"  数据点数: {len(history)}个")

    print(f"\n{'='*20} 交易统计 {'='*20}")
    print(f"  总交易次数: {trade_count}次")
    if trade_count > 0:
        days = (final['datetime'] - initial['datetime']).days
        if days > 0:
            print(f"  平均每天交易: {trade_count/days:.2f}次")

    print(f"\n{'='*20} BTC计价收益 {'='*20}")
    print(f"  初始BTC价值: {format_crypto(initial_btc, 8)}")
    print(f"  最终BTC价值: {format_crypto(final_btc, 8)}")
    print(f"  绝对收益: {format_crypto(final_btc - initial_btc, 8)} BTC")
    print(f"  收益率: {format_percentage(btc_return, 2)}")
    print(f"  最大回撤: {format_percentage(max_drawdown_btc, 2)}")

    print(f"\n{'='*20} {config.BRIDGE.symbol}计价收益 {'='*20}")
    print(f"  初始{config.BRIDGE.symbol}价值: {format_crypto(initial_bridge, 2)}")
    print(f"  最终{config.BRIDGE.symbol}价值: {format_crypto(final_bridge, 2)}")
    print(f"  绝对收益: {format_crypto(final_bridge - initial_bridge, 2)} {config.BRIDGE.symbol}")
    print(f"  收益率: {format_percentage(bridge_return, 2)}")
    print(f"  最大回撤: {format_percentage(max_drawdown_bridge, 2)}")

    print(f"\n{'='*20} 持仓信息 {'='*20}")
    print(f"  初始持仓:")
    for coin, amount in initial['balances'].items():
        if amount > 0:
            print(f"    {coin}: {format_crypto(amount, 8)}")

    print(f"  最终持仓:")
    for coin, amount in final['balances'].items():
        if amount > 0:
            print(f"    {coin}: {format_crypto(amount, 8)}")

    # 显示最近的几笔交易
    if balance_changes:
        print(f"\n{'='*20} 最近5笔交易 {'='*20}")
        recent_trades = balance_changes[-5:] if len(balance_changes) > 5 else balance_changes
        for i, trade in enumerate(recent_trades, 1):
            print(f"\n  交易 #{len(balance_changes) - len(recent_trades) + i}")
            print(f"    时间: {trade['time'].strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"    持仓变化:")
            # 找出变化的币种
            all_coins = set(trade['from'].keys()) | set(trade['to'].keys())
            for coin in all_coins:
                from_amt = trade['from'].get(coin, 0)
                to_amt = trade['to'].get(coin, 0)
                if abs(from_amt - to_amt) > 0.00000001:  # 避免浮点误差
                    diff = to_amt - from_amt
                    print(f"      {coin}: {format_crypto(from_amt, 8)} → "
                          f"{format_crypto(to_amt, 8)} "
                          f"({format_crypto(diff, 8):+})")

    # 性能评估
    print(f"\n{'='*20} 性能评估 {'='*20}")
    if bridge_return > 0:
        print(f"  ✅ 盈利策略")
    elif bridge_return == 0:
        print(f"  ⚠️  保本策略")
    else:
        print(f"  ❌ 亏损策略")

    # 与持有DOGE对比
    print(f"\n  提示: 此收益率为机器人交易策略的收益")
    print(f"        如需对比，可查看同期DOGE单纯持有的收益率")

    print_separator("=")
    print("报告生成完毕！")
    print_separator("=")

    # 询问是否保存详细数据
    print("\n[提示] 如需保存详细数据，可修改脚本添加CSV导出功能")
    print("       或查看数据库文件: data/crypto_trading.db")

if __name__ == "__main__":
    main()
