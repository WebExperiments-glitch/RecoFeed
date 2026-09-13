"""本地双塔推荐：物品塔（embedding）+ 用户塔（个人微调 MLP）+ 本地重排。

模块划分：
    embedder.py               物品塔：本地 embedding 模型 → 仓库语义向量（存 SQLite BLOB）
    user_model.py             用户塔 + 个人 MLP：用本地行为数据微调 → personal_model_u*.pth
    rerank.py                 本地重排：TF-IDF/向量召回 → 个人模型精排
    sklearn_lite.py           无 torch 时的 LSA 兜底向量化

设计原则：
    · 数据不出本机（模型、向量、训练全在本地 SQLite + 本地文件）
    · 只服务一个用户 → 不需要分布式/大模型，几秒重训即跟上兴趣漂移
    · 任何一环缺失都能优雅降级（没模型→退回结构化排序；没 torch→LSA 向量）
"""
