---
name: predict-limitup
description: 运行涨停概率排序模型，输出当日候选榜。当需要从特征矩阵得到"最可能走强/涨停"的相对排序时使用。底层用 LightGBM（无训练模型时回退透明的规则打分）。输出的是相对排序分(0-1)，不是概率保证。
---

# 涨停概率排序模型

对特征矩阵打分排序，得出候选观察池。底层是 `finbot.models.LimitUpRanker`。

## 用法
```bash
finbot predict --date 2026-06-02 --top 20
finbot predict --date 2026-06-02 --top 20 --source mock
```

## 模型说明
- **lightgbm**：若 `models_store/limitup_lgbm.txt` 存在则加载；用历史标注数据
  （标签 = 次日是否涨停）通过 `LimitUpRanker.train(X, y)` 训练。
- **rule_score**（回退）：对标准化因子做加权打分，透明可解释，开箱即用。

## 产物
- `artifacts/<date>/03_candidates.json`：候选榜，每只含 `code, name, sector, score, drivers`。
- `using_trained_model` 字段标明用的是训练模型还是规则回退。

## ⚠️ 必读限制
- `score` 是**当日横截面的相对排序分**，不是校准概率，**更不是涨停保证**。
- 涨停由资金博弈与突发情绪驱动，本质难以可靠预测；任何"高准确率涨停预测"都不可信。
- 候选榜只是**研究观察池**，必须交由 `stock-picker` 叠加逻辑与新闻催化后再使用。
