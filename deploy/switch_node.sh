#!/bin/bash
# 快速切换 Clash 节点脚本

CLASH_API="http://127.0.0.1:9090"

echo "可用的香港节点:"
echo "1. 🇭🇰|香港-IEPL 01"
echo "2. 🇭🇰|香港-IEPL 02"
echo "3. 🇭🇰|香港-IEPL 03"
echo "4. 🇭🇰|香港-中转 01"
echo "5. 🇭🇰|香港-中转 02"
echo ""

read -p "选择节点 (1-5): " choice

case $choice in
    1) NODE="🇭🇰|香港-IEPL 01" ;;
    2) NODE="🇭🇰|香港-IEPL 02" ;;
    3) NODE="🇭🇰|香港-IEPL 03" ;;
    4) NODE="🇭🇰|香港-中转 01" ;;
    5) NODE="🇭🇰|香港-中转 02" ;;
    *) echo "无效选择"; exit 1 ;;
esac

echo "切换到: $NODE"

curl -X PUT "$CLASH_API/proxies/节点选择" \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"$NODE\"}"

echo ""
echo "✓ 节点已切换"

# 测试连接
echo ""
echo "测试 Binance 连接..."
if curl -s --max-time 5 https://api.binance.com/api/v3/ping | grep -q "{}"; then
    echo "✓ Binance API 可访问"
else
    echo "✗ 连接失败"
fi
