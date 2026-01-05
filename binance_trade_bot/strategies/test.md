# 现货轮动交易系统  
## Cross-Sectional Trend Lock Strategy  
**Final, Minimal, Brutal Specification**

---

## 0. 系统目标（Objective）

本系统的目标只有一个：

> **在极少数市场阶段，捕获横截面趋势的右尾收益。**

系统不试图：
- 在无结构阶段赚钱  
- 提供稳定反馈  
- 通过频繁交易制造存在感  

系统明确接受：
- 长时间不作为  
- 大部分时间跑输市场  
- 收益高度集中于少数交易  
- 回撤看起来“很不聪明”  

---

## 1. 核心假设（Single Assumption）

系统只依赖一个假设：

> **市场在极少数时间，存在可持续的横截面相对强弱。**

若该假设在某段历史中不存在，  
系统的正确行为是：**什么都不做**。

---

## 2. 交易对象与价格（Universe & Price）

- 交易币种集合  

  $$
  \mathcal{C} = \{c_1, c_2, \dots, c_N\}
  $$

- 桥币  

  $$
  b \in \{\text{USDT}, \text{BTC}\}
  $$

- 价格函数  

  $$
  P(x,t) = \text{现货价格（以 } b \text{ 计）}
  $$

---

## 3. 相对价格（Price Ratio）

$$
R_{i,j}(t) = \frac{P(c_i,t)}{P(c_j,t)}
$$

系统不使用任何其它原始信号。

---

## 4. 核心信号：横截面优势（Cross-Sectional Dominance）

### 4.1 相对优势评分（仅用于排序）

$$
S_{i,j}(t)
=
(1 - f_{tx})
\cdot
\frac{R_{i,j}(t)}{\tilde{R}_{i,j}(t)}
- 1
$$

其中：

- $\tilde{R}_{i,j}(t)$：  
  中期参考比率（EMA / rolling median）

**注意：**  
$S$ 只用于比较强弱顺序，  
**不直接决定是否交易**。

---

## 5. 唯一的进攻触发条件（Only Entry Condition）

### 横截面极端事件（Cross-Sectional Extreme）

存在某币 $c^*$，满足：

1. **广度条件（Breadth）**

   $$
   \#\{j \neq c^* \mid S_{c^*,j} \ge q\} \ge K
   $$

2. **极端性条件（Rarity）**

   - 上述 $S$ 位于历史分布的 **95%–99% 分位**

3. **持续性条件（Persistence）**

   - 条件 1、2 连续成立 ≥ $T$

满足以上条件，定义为：

> **TREND\_LOCK 触发**

---

## 6. 进攻态（Trend Lock Mode）

### 6.1 行为规则

一旦进入 **TREND\_LOCK**：

- 全仓持有 $c^*$
- 禁止轮动  
- 禁止因弱反向信号退出  
- 禁止人为干预  

系统在此状态下的唯一任务：

> **不做任何“看起来聪明”的事。**

---

## 7. 唯一退出条件（Only Exit Condition）

### 横截面崩塌（Cross-Sectional Collapse）

同时满足：

- $c^*$ 对多数币：

  $$
  S_{c^*,j} < 0
  $$

- 且持续 ≥ $T_{exit}$

退出后：

$$
c^* \rightarrow b
$$

---

## 8. 防守态（Idle / Defensive State）

当系统不处于 **TREND\_LOCK** 时：

- 默认持有桥币 $b$
- 允许长期完全不交易  

防守态的 KPI：

> **不死，不乱动，不自我欺骗。**

---

## 9. 资产退化控制（Asset Survival Filter）

满足以下任一条件的资产可被移出交易集合：

- 长期失去流动性  
- 多次极端下跌且无恢复  
- 长期横截面表现垫底  

**该机制不影响已触发的 TREND\_LOCK。**

---

## 10. 状态机（State Machine）

```

IDLE / DEFENSIVE
↓
TREND_LOCK
↓
(Collapse)
↓
IDLE / DEFENSIVE

```

---

## 11. 风险声明（Non-Negotiable）

本系统必然经历：

- 多数时间零收益  
- 少数交易决定全部利润  
- 明显回撤  
- 长时间看起来“像失败”  

任何试图：

- 增加交易频率  
- 平滑曲线  
- 提前退出趋势  

的修改，都会**破坏系统的唯一 Alpha**。

---

## 12. 最终说明（Final Statement）

> **这不是一个“持续工作的系统”，  
> 而是一个“只在值得时才出手的系统”。**

如果一年不交易，  
那不是缺陷，  
而是设计正确。

