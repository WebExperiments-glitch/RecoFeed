# 项目：RecoFeed（暂定名）

> **抖音有视频，我有仓库。**
> 让被遗忘的优质开源项目，重新被人看见。

---

## ⚠️ 本项目为开发者内部版本（Beta）

产品处于高速提交期，请勿用于有危险的地方，出现问题本项目不背任何风险。

---

## 这是什么

GitHub 从不主动推流。全世界有大量开发者写完仓库就再没推广过，优质的仓库最终因为无人访问而落灰。

RecoFeed 用**抖音的信息流范式**来做**开源仓库的发现**：

| 抖音 | RecoFeed |
|---|---|
| 视频 | GitHub 仓库 |
| 双击点赞 | 本地点赞 |
| 收藏 | Star on GitHub |
| 评论区 | Issues / PRs / Discussions → 词云 |
| 搜索干预 | 4:1 混合推荐 |
| 不感兴趣 | 梯度标签惩罚 |
| 推荐算法 | 双塔（TF-IDF + 用户画像） |
| 用户数据 | 完全本地，隐私自有 |

**核心理念**：商业平台让热门更热，我们要让**遗珠浮上来**。

---

## 核心特性

- 🎞️ **仿抖音上下滑动信息流** —— 垂直滑动、自动播放式交互
- 🧊 **Apple Liquid Glass 毛玻璃 UI** —— 高级动画与纵深感
- 🏷️ **TF-IDF 三层标签体系** —— README 核心 ×1.0 / 用户足迹 ×0.6 / Issue 词云 ×0.3
- 👤 **三层用户画像** —— 能力圈（自建仓库）×1.0 / 兴趣圈（Star）×0.6 / 注意力圈（完读）×0.3
- 🔍 **搜索干预** —— 搜索词作临时标签，4:1 混合，5 次刷新后归零
- 👎 **梯度负反馈** —— 区分「讨厌这个仓库」与「讨厌这个类别」
- 🌱 **渐进式冷启动** —— Trending 诱饵 → 70/30 混合 → 纯算法
- 🔒 **本地优先** —— 数据存本地 SQLite / IndexedDB，隐私自有

---

## 技术栈

### 前端
```
React 19 + TypeScript 5.7 + Vite 6
├─ 样式      Tailwind CSS 3.4（抖音式深色 + 毛玻璃）
├─ 路由状态  React Hooks（无重型状态库）
├─ 埋点      IntersectionObserver 曝光 + dwell 深读判定
└─ 本地存储  LocalStorage（i18n / 互动记忆）
```

### 后端
```
Python 3.11+ FastAPI
├─ 分词      jieba（TF-IDF / TextRank）
├─ 数据库    SQLite（零配置，Python 内置）
├─ 全文检索  SQLite FTS5 + jieba 预分词
├─ 任务调度  APScheduler
└─ HTTP     httpx（异步）
```

> 选型说明见 `docs/算法实现规格.md` 第七章 —— 已从 PostgreSQL + Elasticsearch + Redis
> 精简为 SQLite 单文件方案（本地优先场景不需要重型组件，且 ES 的 SSPL 协议有商用风险）。

---

## 快速开始

### 后端（端口 8000）

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 初始化数据库 + 灌入种子数据（94 个示例仓库）
python jobs/seed_data.py

# 启动
python app.py          # 或 uvicorn app:app --port 8000
```

### 前端（端口 5173）

```bash
cd frontend
pnpm install
pnpm dev               # 已配置代理：/api → http://127.0.0.1:8000
```

打开 http://localhost:5173 即可开刷。

---

## 拿回本地开发

云端环境写的代码已全部入库，本地 clone 下来即可无缝继续。

### 方式 A：zip 包解压（推荐，收到的交付包走这条）

zip 里已带完整 `.git` 历史和 `backend/data/recofeed.db` 数据库，解压后：

```bash
# ① 推上 GitHub（先在 github.com/new 建空仓库 RecoFeed，不要勾 README）
git remote add origin https://github.com/WebExperiments-glitch/RecoFeed.git
git push -u origin main

# ② 跑后端
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py                # 数据库已自带，直接跑 → http://127.0.0.1:8000

# ③ 跑前端（另开终端）
cd frontend
pnpm install
pnpm dev                     # → http://localhost:5173
```

### 方式 B：git clone（仓库已在 GitHub 后）

```bash
git clone https://github.com/WebExperiments-glitch/RecoFeed.git
cd RecoFeed
```

后续启动同上 ②③；若 clone 下来没有数据库（.gitignore 排除了 *.db），先跑
`python jobs/seed_data.py` 生成。

注意事项：
- 数据库 `backend/data/recofeed.db` 不入库（见 `.gitignore`），clone 版首次运行先跑 `seed_data.py`
- 本地改完推回来：`git add -A && git commit -m "..." && git push`
- 云端和本地共用一个远程仓库，随时来回同步

---

## 项目结构

```
RecoFeed/
├── docs/
│   ├── 调研报告.md/.html          # 抖音等平台推流机制调研
│   ├── 推流引擎设计.md/.html       # 抖音机制 → 代码的翻译
│   ├── 算法实现规格.md/.html       # ⭐ 可直接编码的规格（含实测验证）
│   ├── schema.sql                 # 数据库 Schema（14 张表，已验证可执行）
│   └── 下载清单.md                 # 参考仓库下载清单
├── backend/
│   ├── app.py
│   ├── recall/                    # 多路召回
│   ├── rank/                      # 粗排/精排/重排
│   ├── pool/                      # 流量池状态机
│   ├── tags/                      # TF-IDF 标签提取
│   ├── profile/                   # 用户画像
│   └── jobs/                      # 定时任务
└── frontend/
    ├── src/
    │   ├── components/
    │   └── stores/
    └── ...
```

---

## 算法要点

### 推荐得分

```
推荐得分 = 预测行为概率 × 行为价值权重

其中行为权重（2026 抖音最新体系）：
  收藏率(Star) > 复访率 > 铁粉互动 > 5秒完播 > 整体完播 > 评论 > 点赞 > 转发
```

### 三层标签权重

| 层级 | 数据源 | 权重 |
|---|---|---|
| 第一层 | README 项目核心 | ×1.0 |
| 第二层 | 用户 Star / 完读 | ×0.6 |
| 第三层 | Issues / PRs 热词 | ×0.3 |

### 冷启动三阶段

| 阶段 | 位置 | 策略 |
|---|---|---|
| 1 | 第 1~5 个 | 100% Trending |
| 2 | 第 6~20 个 | 70% Trending + 30% 冷门池 |
| 3 | 第 21 个起 | 100% 算法推荐 |

---

## 数据来源与合规

- 仓库元数据来自 **GitHub 官方 API**（含 Search API / star 时间序列）
- 严格遵守 GitHub API 速率限制（认证后 5000 次/小时）
- 所有推荐结果均指向 GitHub 原仓库，**不镜像、不转载仓库内容**
- 用户本地数据不经服务器，**不上传、不分析、不商业利用**

---

## 开源协议

本项目采用 **MIT License**。

参考的第三方项目协议保持不变，详见 `THIRD_PARTY_NOTICES.md`。

---

## 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

**特别欢迎**：
- 新的召回通路设计
- 中文技术标签词典扩充
- 冷启动策略优化

---

## 致谢

灵感来自抖音推荐系统的公开机制，以及所有为解决"开源项目无人问津"问题而努力的人。
