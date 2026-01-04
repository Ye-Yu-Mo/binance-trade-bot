#!/bin/bash
# Binance Trade Bot 启动脚本

echo "==================================="
echo "Binance Trade Bot 启动脚本"
echo "==================================="

# 进入项目目录
cd /Users/jasxu/Documents/crypto/binance-trade-bot

# 检查配置
echo -e "\n[1/4] 检查配置..."
uv run python -c "
from binance_trade_bot.config import Config
c = Config()
print(f'策略: {c.STRATEGY}')
print(f'桥接币: {c.BRIDGE.symbol}')
print(f'支持币种: {c.SUPPORTED_COIN_LIST}')
print(f'侦察倍数: {c.SCOUT_MULTIPLIER}')
print(f'侦察间隔: {c.SCOUT_SLEEP_TIME}秒')
"

# 初始化数据库
echo -e "\n[2/4] 初始化数据库..."
uv run python -c "
from binance_trade_bot.database import Database
from binance_trade_bot.config import Config
from binance_trade_bot.logger import Logger
from binance_trade_bot.models import Base

config = Config()
logger = Logger()
db = Database(logger, config)
Base.metadata.create_all(db.engine)
print('✅ 数据库初始化完成')
" || { echo "❌ 数据库初始化失败"; exit 1; }

# 检查现有进程
echo -e "\n[3/4] 检查现有进程..."
if pgrep -f "binance_trade_bot" > /dev/null; then
    echo "⚠️  发现已运行的交易机器人进程！"
    echo "是否停止旧进程？(y/n)"
    read -r response
    if [[ "$response" == "y" ]]; then
        pkill -f binance_trade_bot
        echo "✅ 已停止旧进程"
        sleep 2
    else
        echo "❌ 取消启动"
        exit 1
    fi
fi

# 启动机器人
echo -e "\n[4/4] 启动交易机器人..."
echo "选择启动方式："
echo "  1) 前台运行（测试推荐，可实时查看日志）"
echo "  2) 后台运行（生产推荐，日志保存到 bot.log）"
echo "  3) Screen 会话（推荐，可随时重新连接）"
read -p "请选择 (1/2/3): " choice

case $choice in
    1)
        echo "✅ 前台运行模式"
        echo "提示: 按 Ctrl+C 停止"
        echo -e "\n==================================="
        uv run python -m binance_trade_bot
        ;;
    2)
        echo "✅ 后台运行模式"
        nohup uv run python -m binance_trade_bot > bot.log 2>&1 &
        sleep 2
        echo "✅ 机器人已启动"
        echo "查看日志: tail -f bot.log"
        echo "停止机器人: pkill -f binance_trade_bot"
        tail -20 bot.log
        ;;
    3)
        echo "✅ Screen 会话模式"
        screen -dmS trading-bot bash -c "uv run python -m binance_trade_bot"
        sleep 2
        echo "✅ Screen 会话已创建: trading-bot"
        echo "连接会话: screen -r trading-bot"
        echo "脱离会话: Ctrl+A 然后按 D"
        echo "停止会话: screen -X -S trading-bot quit"
        ;;
    *)
        echo "❌ 无效选择"
        exit 1
        ;;
esac

echo -e "\n==================================="
echo "✅ 启动完成！"
echo "==================================="
