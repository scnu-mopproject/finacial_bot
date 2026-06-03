---
name: analyze-market
description: 构建A股因子特征矩阵（带经济先验+中性化）。当需要从本地数据仓库计算价值/质量/动量/反转/低波/流动性/情绪/跨资产宏观敏感度等因子，并做行业+市值中性化时使用。是排序模型与回测的输入。
---

# 因子工程（经济先验 + 中性化）

把数据仓库的行情/基础/宏观/新闻转成**中性化后的因子面板**。底层是
`finbot.features.registry.build_factor_panel`。

## 用法
```bash
finbot factors --catalog              # 查看因子目录与每个因子的经济逻辑
finbot factors                        # 计算最新一日的中性化因子面板（需先 update）
finbot build-dataset --h 5            # 因子 + 前瞻收益标签 → 训练/回测面板
```

## 因子家族（每个都绑定经济逻辑）
- **价值** ep（需基本面）：估值均值回归。
- **质量** roe（需基本面）：优质企业长期跑赢。
- **动量** momentum_20_5 / 60_5：中期动量延续（剔除最近5日反转噪声）。
- **反转** reversal_5：A股散户主导的短期反转。
- **低波** low_vol_20：低波动异象（彩票偏好致高波高估）。
- **流动性** turnover / illiquidity（Amihud）：拥挤度与非流动性溢价。
- **情绪** news_sentiment：板块新闻情绪（短期动量、易反转）。
- **跨资产宏观** fx_sensitivity / commodity_sensitivity：个股对人民币/商品的滚动 beta × 该宏观变量趋势（出口、外资、资源链传导）。

## 中性化（关键）
每个交易日横截面：**去极值(winsorize) → 标准化(z-score) → 对行业哑变量+对数市值回归取残差**。
目的：剥离市值/行业/beta 暴露，留下**纯粹的相对选股能力**，避免"以为是 alpha、其实在吃风险溢价"。
规模（amount_size）只作中性化对象，不直接当 alpha 因子。

## 扩展
新增因子：在 `registry.py` 写 builder 并登记到 `FACTOR_REGISTRY`（注明 family/direction/经济逻辑）。
跨资产因子务必遵守防前视的时区对齐：只用决策时点前已发生的宏观数据预测前瞻收益。
新因子必须在回测中证明**相对已有因子有增量 IC**，否则删除。
