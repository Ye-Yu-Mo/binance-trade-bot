#!/bin/bash
# 一键后台启动 Binance Trade Bot

cd "$(dirname "$0")"

# 检查是否已运行
if pgrep -f "binance_trade_bot" > /dev/null; then
    echo "⚠️  交易机器人已在运行！"
    echo "停止命令: pkill -f binance_trade_bot"
    exit 1
fi

# 后台启动
echo "🚀 启动交易机器人（后台模式）..."
nohup uv run python -m binance_trade_bot --yes > bot.log 2>&1 &

sleep 2

if pgrep -f "binance_trade_bot" > /dev/null; then
    echo "✅ 启动成功！"
    echo ""
    echo "查看日志: tail -f bot.log"
    echo "停止运行: pkill -f binance_trade_bot"
    echo ""
    echo "最近日志:"
    tail -20 bot.log
else
    echo "❌ 启动失败，查看日志:"
    cat bot.log
    exit 1
fi
