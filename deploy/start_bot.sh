#!/bin/bash
# Binance Trade Bot - 服务器部署脚本
# 切换 Clash 到香港节点并启动交易机器人

set -e

echo "=================================="
echo "Binance Trade Bot - 启动脚本"
echo "=================================="

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. 切换 Clash 到香港节点
echo -e "\n${YELLOW}[1/4] 切换 Clash 代理节点...${NC}"

CLASH_API="http://127.0.0.1:9090"
HK_NODE="🇭🇰|香港-IEPL 01"  # 可修改为其他香港节点

echo "目标节点: $HK_NODE"

# 切换节点
RESPONSE=$(curl -s -X PUT "$CLASH_API/proxies/节点选择" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"$HK_NODE\"}")

if echo "$RESPONSE" | grep -q "success\|ok\|\"\""; then
    echo -e "${GREEN}✓ 节点切换成功${NC}"
else
    echo -e "${YELLOW}⚠ 响应: $RESPONSE${NC}"
fi

# 等待节点切换生效
sleep 2

# 2. 测试 Binance 连接
echo -e "\n${YELLOW}[2/4] 测试 Binance API 连接...${NC}"

TEST_URL="https://api.binance.com/api/v3/ping"

if curl -s --max-time 10 "$TEST_URL" | grep -q "{}"; then
    echo -e "${GREEN}✓ Binance API 连接成功${NC}"
else
    echo -e "${RED}✗ 连接失败，请检查：${NC}"
    echo "  1. Clash 是否运行？"
    echo "  2. TUN 模式是否启用？"
    echo "  3. 节点是否正常？"

    # 显示当前 IP 和位置
    echo -e "\n当前出口 IP 信息:"
    curl -s https://ipapi.co/json/ | grep -E "ip|country_name|city" || echo "无法获取"

    exit 1
fi

# 3. 检查邮件配置
echo -e "\n${YELLOW}[3/4] 检查邮件通知配置...${NC}"

EMAIL_CONFIG="$HOME/binance-trade-bot/config/email.ini"

if [ -f "$EMAIL_CONFIG" ]; then
    echo -e "${GREEN}✓ 邮件配置文件存在${NC}"
else
    echo -e "${YELLOW}⚠ 邮件配置文件不存在: $EMAIL_CONFIG${NC}"
    echo "   邮件通知将被禁用"
fi

# 4. 启动交易机器人
echo -e "\n${YELLOW}[4/4] 启动 Binance Trade Bot...${NC}"

cd "$HOME/binance-trade-bot" || exit 1

echo -e "${GREEN}启动中...${NC}"
echo ""
echo "=================================="
echo ""

# 运行机器人
uv run python -m binance_trade_bot

# 如果程序退出
echo ""
echo -e "${YELLOW}机器人已停止${NC}"
