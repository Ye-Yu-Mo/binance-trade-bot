#!/bin/bash
# Binance Trade Bot - 智能启动脚本
# 自动选择延迟最低的非美国节点

set -e

# 配置
CLASH_API="http://127.0.0.1:9090"
PROXY_GROUP="节点选择"
BOT_DIR="$HOME/binance-trade-bot"
EXCLUDED_KEYWORDS="美国|US|United States"  # 排除的节点关键词

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=========================================="
echo "Binance Trade Bot - 智能启动"
echo -e "==========================================${NC}\n"

# 1. 检查 Clash
echo -e "${YELLOW}[1/5] 检查 Clash 状态...${NC}"

if ! curl -s --connect-timeout 2 "$CLASH_API/configs" > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠ Clash 未运行，正在启动...${NC}"

    cd ~/clash
    pkill clash 2>/dev/null || true
    nohup ./clash -d . > clash.log 2>&1 &

    echo "等待 Clash 启动..."
    sleep 5

    if ! curl -s --connect-timeout 2 "$CLASH_API/configs" > /dev/null 2>&1; then
        echo -e "${RED}✗ Clash 启动失败${NC}"
        exit 1
    fi
fi

echo -e "${GREEN}✓ Clash 运行中${NC}\n"

# 2. 获取并筛选节点
echo -e "${YELLOW}[2/5] 获取可用节点...${NC}"

ALL_NODES=$(curl -s "$CLASH_API/proxies/$PROXY_GROUP" | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | grep -v "^$PROXY_GROUP$")

if [ -z "$ALL_NODES" ]; then
    echo -e "${RED}✗ 无法获取节点列表${NC}"
    exit 1
fi

# 排除美国节点
FILTERED_NODES=$(echo "$ALL_NODES" | grep -viE "$EXCLUDED_KEYWORDS" || echo "$ALL_NODES")

NODE_COUNT=$(echo "$FILTERED_NODES" | wc -l)
echo -e "${GREEN}✓ 找到 $NODE_COUNT 个非美国节点${NC}\n"

# 3. 测试节点延迟
echo -e "${YELLOW}[3/5] 测试节点延迟（这可能需要一些时间）...${NC}"

BEST_NODE=""
BEST_DELAY=999999

# 临时文件存储测试结果
TEMP_FILE=$(mktemp)

while IFS= read -r node; do
    echo -n "  测试: $node ... "

    # 切换到该节点
    curl -s -X PUT "$CLASH_API/proxies/$PROXY_GROUP" \
      -H "Content-Type: application/json" \
      -d "{\"name\": \"$node\"}" > /dev/null 2>&1

    sleep 1

    # 测试延迟 (ping Binance API)
    START=$(date +%s%N)
    if curl -s --max-time 3 https://api.binance.com/api/v3/ping > /dev/null 2>&1; then
        END=$(date +%s%N)
        DELAY=$(( (END - START) / 1000000 ))  # 转换为毫秒

        echo -e "${GREEN}${DELAY}ms${NC}"
        echo "$node|$DELAY" >> "$TEMP_FILE"

        # 更新最佳节点
        if [ $DELAY -lt $BEST_DELAY ]; then
            BEST_DELAY=$DELAY
            BEST_NODE="$node"
        fi
    else
        echo -e "${RED}失败${NC}"
    fi
done <<< "$FILTERED_NODES"

echo ""

if [ -z "$BEST_NODE" ]; then
    echo -e "${RED}✗ 没有可用的节点${NC}"
    rm -f "$TEMP_FILE"
    exit 1
fi

echo -e "${GREEN}✓ 最佳节点: $BEST_NODE (${BEST_DELAY}ms)${NC}\n"

# 显示延迟排名（前5）
echo "延迟排名 (前5):"
sort -t'|' -k2 -n "$TEMP_FILE" | head -5 | while IFS='|' read -r node delay; do
    if [ "$node" = "$BEST_NODE" ]; then
        echo -e "  ${GREEN}★ $node - ${delay}ms${NC}"
    else
        echo -e "    $node - ${delay}ms"
    fi
done
rm -f "$TEMP_FILE"

echo ""

# 4. 切换到最佳节点
echo -e "${YELLOW}[4/5] 切换到最佳节点...${NC}"

curl -s -X PUT "$CLASH_API/proxies/$PROXY_GROUP" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"$BEST_NODE\"}" > /dev/null

sleep 2

# 验证连接
if curl -s --max-time 5 https://api.binance.com/api/v3/ping | grep -q "{}"; then
    echo -e "${GREEN}✓ Binance API 连接正常${NC}"

    # 显示出口 IP
    IP_INFO=$(curl -s --max-time 5 https://ipapi.co/json/ 2>/dev/null)
    if [ -n "$IP_INFO" ]; then
        IP=$(echo "$IP_INFO" | grep -o '"ip":"[^"]*"' | cut -d'"' -f4)
        COUNTRY=$(echo "$IP_INFO" | grep -o '"country_name":"[^"]*"' | cut -d'"' -f4)
        CITY=$(echo "$IP_INFO" | grep -o '"city":"[^"]*"' | cut -d'"' -f4)
        echo -e "  出口: ${BLUE}$IP${NC} (${BLUE}$CITY, $COUNTRY${NC})"
    fi
else
    echo -e "${RED}✗ Binance API 连接失败${NC}"
    exit 1
fi

echo ""

# 5. 启动交易机器人
echo -e "${YELLOW}[5/5] 启动 Binance Trade Bot...${NC}\n"

cd "$BOT_DIR" || exit 1

echo -e "${BLUE}=========================================="
echo "交易机器人启动中..."
echo -e "==========================================${NC}\n"

# 运行机器人
uv run python -m binance_trade_bot

# 如果程序退出
echo ""
echo -e "${YELLOW}机器人已停止${NC}"
