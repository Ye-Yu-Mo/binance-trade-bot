#!/bin/bash
# 通用 Clash 节点切换脚本
# 自动从 Clash API 获取所有可用节点

set -e

CLASH_API="http://127.0.0.1:9090"
PROXY_GROUP="节点选择"  # 修改为你的代理组名称

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=================================="
echo "Clash 节点切换工具"
echo -e "==================================${NC}\n"

# 检查 Clash 是否运行
if ! curl -s --connect-timeout 2 "$CLASH_API/configs" > /dev/null 2>&1; then
    echo -e "${RED}✗ Clash 未运行或 API 不可访问${NC}"
    echo ""
    echo "请先启动 Clash:"
    echo "  cd ~/clash && ./clash -d ."
    exit 1
fi

echo -e "${GREEN}✓ Clash 运行中${NC}\n"

# 获取当前节点
CURRENT=$(curl -s "$CLASH_API/proxies/$PROXY_GROUP" | grep -o '"now":"[^"]*"' | cut -d'"' -f4)
echo -e "当前节点: ${YELLOW}$CURRENT${NC}\n"

# 获取所有可用节点
echo "正在获取可用节点列表..."
NODES=$(curl -s "$CLASH_API/proxies/$PROXY_GROUP" | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | grep -v "^$PROXY_GROUP$")

if [ -z "$NODES" ]; then
    echo -e "${RED}✗ 无法获取节点列表${NC}"
    exit 1
fi

# 允许用户筛选
echo ""
echo "输入关键词筛选节点 (直接回车显示所有):"
read -p "> " FILTER

if [ -n "$FILTER" ]; then
    FILTERED=$(echo "$NODES" | grep -i "$FILTER")
    if [ -z "$FILTERED" ]; then
        echo -e "${YELLOW}⚠ 没有匹配的节点，显示所有节点${NC}"
        FILTERED="$NODES"
    fi
else
    FILTERED="$NODES"
fi

# 显示节点列表
echo ""
echo -e "${BLUE}可用节点:${NC}"
echo "$FILTERED" | nl -w2 -s'. '

# 统计节点数量
COUNT=$(echo "$FILTERED" | wc -l)

# 选择节点
echo ""
read -p "选择节点 (1-$COUNT, 或按 q 退出): " CHOICE

if [ "$CHOICE" = "q" ] || [ "$CHOICE" = "Q" ]; then
    echo "已取消"
    exit 0
fi

# 验证输入
if ! [[ "$CHOICE" =~ ^[0-9]+$ ]] || [ "$CHOICE" -lt 1 ] || [ "$CHOICE" -gt "$COUNT" ]; then
    echo -e "${RED}✗ 无效选择${NC}"
    exit 1
fi

# 获取选中的节点名称
SELECTED=$(echo "$FILTERED" | sed -n "${CHOICE}p")

echo ""
echo -e "切换到: ${GREEN}$SELECTED${NC}"

# 切换节点
RESPONSE=$(curl -s -X PUT "$CLASH_API/proxies/$PROXY_GROUP" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"$SELECTED\"}" 2>&1)

# 检查是否成功
sleep 1

# 验证切换
NEW_NODE=$(curl -s "$CLASH_API/proxies/$PROXY_GROUP" | grep -o '"now":"[^"]*"' | cut -d'"' -f4)

if [ "$NEW_NODE" = "$SELECTED" ]; then
    echo -e "${GREEN}✓ 节点切换成功${NC}"
else
    echo -e "${YELLOW}⚠ 切换可能失败，当前节点: $NEW_NODE${NC}"
fi

# 测试连接
echo ""
echo "测试网络连接..."

# 测试延迟
if ping -c 1 -W 2 8.8.8.8 > /dev/null 2>&1; then
    echo -e "${GREEN}✓ 网络连接正常${NC}"
else
    echo -e "${YELLOW}⚠ 网络连接可能有问题${NC}"
fi

# 测试 Binance API
echo "测试 Binance API..."
if curl -s --max-time 5 https://api.binance.com/api/v3/ping | grep -q "{}"; then
    echo -e "${GREEN}✓ Binance API 可访问${NC}"

    # 显示当前 IP
    IP_INFO=$(curl -s --max-time 5 https://ipapi.co/json/ 2>/dev/null)
    if [ -n "$IP_INFO" ]; then
        IP=$(echo "$IP_INFO" | grep -o '"ip":"[^"]*"' | cut -d'"' -f4)
        COUNTRY=$(echo "$IP_INFO" | grep -o '"country_name":"[^"]*"' | cut -d'"' -f4)
        echo -e "  出口 IP: ${BLUE}$IP${NC} (${BLUE}$COUNTRY${NC})"
    fi
else
    echo -e "${RED}✗ Binance API 无法访问${NC}"
    echo "  请尝试其他节点"
fi

echo ""
echo -e "${GREEN}完成！${NC}"
