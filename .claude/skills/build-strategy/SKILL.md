---
name: build-strategy
description: 结合用户实仓、收益明细与风险偏好，把选股候选转成具体的仓位/进出场/风控操作计划。当用户问"我该怎么操作""帮我制定今天的策略""结合我的持仓给建议"时使用。底层是 finbot 的风控约束策略引擎。
---

# 组合策略引擎

把候选榜 + 用户持仓 → 风险受限的操作计划。底层是 `finbot.strategy.build_strategy`。

## 准备
把 `config/portfolio.example.json` 复制为 `config/portfolio.json`（git 已忽略），
填入真实持仓、成本、现金、收益与风险偏好。

## 用法
```bash
finbot strategy --date 2026-06-02 --portfolio config/portfolio.json
finbot strategy --date 2026-06-02 --source mock    # 无 portfolio 时按空仓 10万演示
```

## 逻辑
- **持仓管理**：按盈亏触发 止损(SELL)/止盈(TRIM)/加仓(ADD)/持有(HOLD)。
- **新开仓**：从候选榜按 `分数 × 风格系数` 定仓位，受
  `max_position_pct / max_new_positions / max_total_exposure` 等风控硬约束。
- 风格 `conservative / balanced / aggressive` 调节仓位激进度。

## 产物
- `artifacts/<date>/04_strategy.json`：`summary` + `manage_existing` + `new_entries`。

## 纪律
- 风控线是**硬约束**，不得为博弹性突破。
- 这是程序化建议，应交由 `strategy-advisor` 叠加研判；始终保留免责声明，不承诺收益。
