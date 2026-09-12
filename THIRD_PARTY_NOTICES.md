# 第三方参考项目与协议声明

本项目采用 **MIT License**。

以下列出开发过程中参考的开源项目。**参考的仓库协议保持不变**，
本项目仅借鉴其设计思想或交互范式，未复制其代码（除注明外）。

---

## 一、算法思路参考

### 抖音 / 字节系

| 项目 | 协议 | 参考内容 | 是否复制代码 |
|---|---|---|---|
| [bytedance/monolith](https://github.com/bytedance/monolith) | Apache-2.0 | 实时训练理念、无碰撞嵌入表 | ❌ 仅阅读 |
| [blacckoscar/SOCIAL-ALGORITHM](https://github.com/blacckoscar/SOCIAL-ALGORITHM) | ⚠️ **无 LICENSE** | 信息流打分公式（互动率/时效/亲近度/探索） | ❌ 仅参考公式（算法思路不受版权保护） |
| [jasperan/discover-github](https://github.com/jasperan/discover-github) | UPL-1.0 + Apache-2.0 | 多信号评分引擎、Star Velocity 权重 | ❌ 仅参考 |
| [Kuaishou-OneRec/OpenOneRec](https://github.com/Kuaishou-OneRec/OpenOneRec) | Apache-2.0（代码）⚠️ 模型权重另有协议 | 生成式推荐、推荐理由生成 | ❌ 仅参考 |

### GitHub 相关

| 项目 | 协议 | 参考内容 |
|---|---|---|
| [fradia/github_recommender_systems](https://github.com/fradia/github_recommender_systems) | 未标注 | 基于 star/fork 的协同过滤 |
| [verysleepylemon/repo-radar](https://github.com/verysleepylemon/repo-radar) | 未标注 | 多源融合评分 |
| [loly-baby/Hydra](https://github.com/loly-baby/Hydra) | MIT | Trending 多源降级方案 |

---

## 二、UI / 交互参考

| 项目 | 协议 | 参考内容 | 是否复制代码 |
|---|---|---|---|
| [zyronon/douyin](https://github.com/zyronon/douyin) | ⚠️ **README 明确禁止商用** | 抖音式滑动交互范式 | ❌ **绝不复制**，仅学习交互思路 |
| [HipsterZipster/react-tiktok-style-video-scroller](https://github.com/HipsterZipster/react-tiktok-style-video-scroller) | MIT | 虚拟滚动、无限加载、自动播放 | ❌ 仅参考工程化做法 |

> ⚠️ **重要声明**：本项目与抖音/TikTok 及其运营方**无任何关联**。
> 项目描述中出现的"抖音风格"仅指**通用信息流交互范式**，不涉及任何商标、
> 视觉识别元素或素材的使用。

---

## 三、依赖库

### Python

| 库 | 协议 | 用途 |
|---|---|---|
| FastAPI | MIT | Web 框架 |
| jieba | MIT | 中文分词 |
| httpx | BSD-3-Clause | 异步 HTTP |
| APScheduler | MIT | 定时任务 |
| SQLAlchemy | MIT | ORM |
| Pydantic | MIT | 数据校验 |
| numpy | BSD-3-Clause | 数值计算 |

### 前端

| 库 | 协议 | 用途 |
|---|---|---|
| React | MIT | UI 框架 |
| Vite | MIT | 构建工具 |
| Tailwind CSS | MIT | 样式 |
| [GSAP](https://github.com/greensock/GSAP) | 标准免费许可（含商用） | 高级动画 |
| [Motion](https://github.com/motiondivision/motion) | MIT | React 动画 |
| [Lenis](https://github.com/darkroomengineering/lenis) | MIT | 丝滑滚动 |
| @tanstack/react-virtual | MIT | 虚拟滚动 |
| d3-cloud | BSD-3-Clause | 词云 |
| idb | ISC | IndexedDB 封装 |

---

## 四、明确未采用的方案

以下项目因**协议风险**或**架构不匹配**未采用：

| 项目 | 原因 |
|---|---|
| Elasticsearch | 7.11+ 起采用 **SSPL**（非 OSI 开源协议），另有 AGPL v3 双许可，商用有法律风险 |
| 原版 RVC 相关模型 | 与本项目无关，仅记录 |
| 各平台爬虫项目 | 违反目标平台 ToS，法律风险高 |

---

## 五、协议兼容性说明

本项目 MIT 协议与以上依赖**完全兼容**。

唯一需要注意：
- 若未来引入 **GPL/AGPL** 类库，需评估传染性影响
- **SSPL** 协议库（如 Elasticsearch）**不建议引入**，因其非 OSI 认可的开源协议

---

## 六、致谢

感谢所有开源作者。如果本项目的某个设计来自你的作品而未被列出，
请发起 Issue，我们会立即补充。
