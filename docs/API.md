# RecoFeed API 参考

> 后端：FastAPI + SQLite，默认监听 `http://127.0.0.1:8000`
> 交互式文档：启动后访问 `http://127.0.0.1:8000/docs`（Swagger UI）
> 前端：Vite dev server（默认 `http://localhost:5173`，端口可用 `VITE_PORT` 覆盖；
> 被系统保留时 Vite 会自动往后找），通过 `/api` 前缀代理到后端

## 约定

| 项 | 说明 |
|---|---|
| 基础路径 | `/api` |
| 请求体 | JSON（`Content-Type: application/json`） |
| 认证 | 登录后返回会话令牌，前端以 `Authorization: Bearer <token>` 携带；未登录的接口用 `user_id` 查询参数 |
| 错误体 | `{"detail": "错误说明"}`；状态码 400 参数错 / 401 未登录 / 404 不存在 / 429 频率限制 / 500 服务端 |
| 时间 | 一律 `YYYY-MM-DD HH:MM:SS`（UTC） |

---

## 一、基础

### `GET /api/health`
健康检查 + 数据量。

```json
{ "status": "ok", "repos": 258, "users": 2 }
```

---

## 二、信息流（缓存池）

### `GET /api/feed`
拉取 Feed。**队列优先，见底自动补货**（这是本项目的核心：先取缓存池，池子低于水位就按画像定向补货）。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `user_id` | int | 1 | 用户 |
| `limit` | int | 10 | 每次取几条（1~50） |
| `search_query` | str | — | 搜索干预（4:1 混入搜索结果） |
| `session_languages` | str | — | 本次会话已出现的语言（逗号分隔，用于打散） |
| `session_owners` | str | — | 本次会话已出现的作者（逗号分隔，用于打散） |
| `mode` | str | `all` | 分档：`all` 全部 / `hot` 热门（≥1000 星）/ `gem` 遗珠（<1000 星） |

**响应**
```json
{
  "items": [{
    "repo_id": 16,
    "full_name": "open-mmlab/Amphion",
    "owner": "open-mmlab", "name": "Amphion",
    "description": "…",
    "language": "Python", "stars": 8968,
    "topics": ["audio-generation", "tts"],
    "tags": [{ "tag": "audio", "weight": 0.62 }],
    "license_spdx": "MIT", "license_risk": "safe",
    "score": 0.95,
    "channels": ["refill"],
    "reason": "根据你的浏览兴趣挑选",
    "forgotten_score": 0.12,
    "pushed_at_gh": "2026-08-01 12:00:00",
    "url": "https://github.com/open-mmlab/Amphion",

    "personal_score": 0.997,
    "readme_excerpt": "Amphion is a toolkit for Audio, Music, and Speech Generation…",
    "stats": {
      "forks": 46, "open_issues": 3, "size_kb": 85000,
      "created_at": "2024-01-01 00:00:00", "pushed_at": "2026-09-10 00:00:00",
      "has_ci": true, "has_tests": false, "homepage": null,
      "quality": 0.79, "velocity": 0.98, "freshness": 0.02, "forgotten": 0.77
    }
  }],
  "meta": {
    "source": "queue",
    "queue_size": 47,
    "needs_refill": false,
    "refill": { "refilled": false, "reason": "already_full" },
    "refill_plan": { "queries": ["local llm", "voice"], "languages": [], "domains": [] }
  }
}
```
`meta.source`：`queue` = 全部来自缓存池；`queue+realtime` = 补货没跟上、实时兜底；`realtime_search` = 纯实时。

`meta.ml_rerank`：本地重排统计（`applied` / `scored` / `kept` / `dropped` / `threshold` / `filled_from_dropped`）。
`personal_score`：个人模型匹配度（**卡片上的 🎯 用它**，而不是结构化质量分 —— 两把尺子不能混用）。
`stats`：仓库自身客观指标（概览 + 打分明细），与 `personal_score` 口径不同。

### `GET /api/stats/queue`
队列水位与最近补货记录。

| 参数 | 类型 | 说明 |
|---|---|---|
| `user_id` | int | 用户 |

响应含 `size` / `low_watermark` / `target_size` / `needs_refill` / `by_channel` / `recent_refills[].strategy`（补货方向词、语言、领域）。

### `POST /api/queue/refill`
手动补货（按画像定向抓取）。

```json
{ "user_id": 1, "target": 60 }
```

### `POST /api/queue/rebuild?user_id=1`
清空队列并按当前画像重灌 60 条。用于「画像变了想立刻重排」。

### `POST /api/queue/crawl`
⭐ **用户自定义关键词爬虫**：用 Scrapling 去 GitHub 抓真实仓库 → 入库 → 直接入队。

```json
{ "user_id": 1, "keywords": ["rvc", "wasm"], "per_keyword": 8 }
```
- `keywords` 留空数组 = 用画像面板里保存的自定义关键词
- `per_keyword` 每个关键词抓多少候选（1~15）
- 频控：两次抓取间隔 60 秒（GitHub search 限流 10 次/分钟），过频返回 429

**响应**
```json
{
  "ok": true,
  "keywords": ["local llm", "voice conversion"],
  "found": 11, "new_repos": 10, "enqueued": 11,
  "details": [{ "keyword": "local llm", "found": 8, "kept": 6 }]
}
```
README 与标签由后台任务异步补齐（走 jsdelivr CDN），接口秒回。

---

## 三、搜索

### `GET /api/search`

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `q` | str | `""` | 搜索词 |
| `user_id` | int | 1 | 用于个性化排序与「搜索干预」 |
| `limit` / `offset` | int | 12 / 0 | 分页 |
| `language` | str | — | 语言过滤 |
| `min_stars` | int | — | 最低星数 |
| `sort` | str | `relevance` | `relevance` / `stars` / `recent` / `forgotten` |

响应含 `items[].search_reasons[]`（为什么命中，例如「标签命中 rvc」）与 `meta.total` / `has_more`。

### `GET /api/search/suggest?prefix=rvc&limit=10`
搜索联想（标签 / 语言 / topics）。

### `GET /api/search/hot?limit=10`
热门搜索词：`{ "hot": [{ "word": "rvc", "count": 12 }] }`

---

## 四、互动（推荐系统的输入）

### `POST /api/events`
批量上报埋点。**浏览时长与滚动深度是推荐系统的核心输入**，前端在卡片成为 active / 失活时结算上报。

```json
{
  "user_id": 1,
  "events": [{
    "repo_id": 16,
    "event_type": "deep_read",
    "dwell_ms": 8400,
    "scroll_depth": 0.72,
    "source_channel": "refill",
    "batch_id": "b_20260913_1",
    "rank_position": 0
  }]
}
```
`event_type`：`impression` / `click` / `deep_read` / `skip` / `like` / `dislike` / `star_click` / `scroll`

### 点赞
- `POST /api/repos/{repo_id}/like?user_id=1`
- `DELETE /api/repos/{repo_id}/like?user_id=1`

### `POST /api/repos/{repo_id}/star`
收藏（同步 GitHub star 意图）。`{ "user_id": 1 }`

### `POST /api/repos/{repo_id}/dislike`
不感兴趣。

```json
{ "user_id": 1, "reason": "category" }
```
`reason`：`repo` = 只屏蔽该仓库；`category` = 对其 Top 标签做梯度惩罚（会立刻重建画像）。

### 评论
- `GET /api/repos/{repo_id}/comments?limit=50`
- `POST /api/repos/{repo_id}/comments` → `{ "user_id": 1, "body": "好项目" }`

### `GET /api/repos/{repo_id}`
仓库详情（比 Feed 多出流量池等级与统计字段）。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `user_id` | int | 1 | 用于返回 `liked` / `starred` |
| `with_readme` | bool | false | 是否下发完整 README 正文 |

响应额外字段：`current_pool`（-1 淘汰 / 0 冷启动 / 1 基础 / 2 中级 / 3 高级 / 4 爆款）、`impressions`、`deep_rate`、`ctr`、`tags_json`。

---

## 五、用户与画像

### `GET /api/user/profile?user_id=1`

```json
{
  "user_id": 1,
  "interests": [{ "tag": "rvc", "weight": 0.512 }],
  "languages": [{ "lang": "Python", "n": 12 }],
  "events": 128, "seen": 43, "cold_start": false,
  "updated_at": "2026-09-13 12:00:00"
}
```

### `POST /api/user/profile/rebuild?user_id=1`
从三层足迹（自建仓库 / 点星 / 深读）+ GitHub 实证圈重新聚合画像。

### `GET /api/user/events?user_id=1&limit=50`
行为流水（调试用，核对埋点是否正确）。

### 自定义关键词
- `GET /api/user/keywords?user_id=1` → `{ "keywords": [{ "id": 1, "keyword": "rvc", "weight": 1.0, "created_at": "…" }] }`
- `POST /api/user/keywords` → `{ "user_id": 1, "keyword": "rvc" }`（小写去重，上限 12 个）
- `DELETE /api/user/keywords/{kid}?user_id=1`

这些词会：① 驱动爬虫抓取；② 作为补货方向的**最高优先级**查询词。

---

## 六、GitHub 登录与实证数据

### `GET /api/auth/status`
认证能力与回调地址（配置 OAuth App 时要用）。

```json
{ "configured": false, "callback_url": "http://localhost:8000/api/auth/github/callback" }
```

### `GET /api/auth/github/login?origin=http://localhost:5273`
> ⭐ `origin`（可选）：发起登录的前端地址。后端把它绑到 OAuth `state` 上，回调时按它跳回 ——
> 于是 dev server 换端口（5173 被 Windows 保留段占用时）也能正常登录。
> 只接受 localhost/127.0.0.1，缺省时回退到 `AUTH_FRONTEND_REDIRECT`（默认 `http://localhost:5173`）。

### `GET /api/auth/github/login`（不带 origin）
302 跳转到 GitHub 授权页。未配置 OAuth 时返回 400 并给出配置指引。

### `GET /api/auth/github/callback?code=…&state=…`
GitHub 回调：换 token → 建/取用户 → 签发会话 → 302 回前端
`http://localhost:5173/?auth_token=<token>&auth_sync=1`

### `POST /api/auth/github/pat`
**令牌登录**（不用建 OAuth App）。

```json
{ "token": "ghp_xxx" }
```
→ `{ "token": "<会话令牌>", "user": { "user_id": 3, "github_login": "…", "avatar_url": "…" } }`

令牌需要 `read:user` + `public_repo` 权限。

### `GET /api/auth/me`
当前用户 + GitHub 实证圈概况（需 `Authorization: Bearer`）。

```json
{
  "user_id": 3, "github_login": "WebExperiments-glitch",
  "footprint": {
    "owned_count": 12, "starred_count": 148, "boost": 2.0,
    "owned":   [{ "full_name": "u/recofeed", "weight": 0.5, "pushed_at": "2026-09-10 09:00:00", "stars": 3 }],
    "starred": [{ "full_name": "RVC-Project/…", "weight": 0.4, "rank": 1, "stars": 38191 }]
  }
}
```

### `POST /api/auth/sync`
⭐ 拉取用户**自建仓库**与**点星仓库**，按权重规则入库并重建画像。

```
自建仓库（看最近推送）：7 天内 0.50 / 30 天内 0.20 / 90 天 0.12 / 180 天 0.08 / 1 年 0.05 / 更久 0.03
点星仓库（按仓库星数排名）：第 1 名 0.40 / 第 10 名 0.30 / 第 20 名 0.20 / 50 名 0.12 / 100 名 0.08 / 300 名 0.05
```
进入画像时整体乘 `GITHUB_SIGNAL_BOOST`（默认 2.0）——「用户亲手做过的东西」主导推荐。

### `POST /api/auth/logout`
注销当前会话。

---

## 七、翻译（Agnes AI 免费档模型级联）

### `POST /api/repos/{repo_id}/translate`
翻译仓库简介 + README 为中文（带 SQLite 缓存与限流）。

```json
{
  "repo_id": 16, "cached": false,
  "zh_description": "……", "zh_readme": "……",
  "model": "nvidia/nemotron-3-super-120b-a12b:free",
  "readme_truncated": true
}
```

### `GET /api/translate/status`
额度与冷却：`chain[]`（级联模型 + 各自冷却）、`used_last_minute`、`used_today`、`deepseek_used_today`、`cache_entries`。

### `POST /api/translate/batch`
批量拉取卡片标题 + 简介译文（整批合并 **1 次** LLM 调用）。

```json
{ "repo_ids": [16, 6, 19] }
```
→ `{ "translations": { "16": { "zh_name": "…", "zh_description": "…" }, "6": null } }`
`null` = 暂不可用（限流 / 质量门禁丢弃），前端会退避重试。

---

## 八、本地双塔模型（个人专属推荐）

> 全部计算在本机完成：向量存 SQLite BLOB（不需要 faiss / chroma）；个人模型是一个 271KB 的 `.pth`。

### `GET /api/ml/status?user_id=1`
向量库 / 个人模型 / 证据量 / 后台任务进度。

```json
{
  "embeddings": { "count": 3616, "dim": 512, "model": "bge-small-zh-v1.5", "backend": "st" },
  "user_model": { "exists": true, "size_kb": 271.2, "epochs": 100,
                  "metrics": { "auc_holdout": 0.837, "acc_holdout": 0.769, "seconds": 0.56 } },
  "evidence": { "count": 52 },
  "tasks": { "embed": { "running": false }, "train": { "running": false } }
}
```

### `POST /api/ml/embed?force=false&limit=`
物品塔向量化（后台跑，进度看 `/api/ml/status`）。首次运行会自动从魔搭下载 embedding 模型（约 180MB）。

### `POST /api/ml/train?user_id=1&epochs=100`
训练个人模型并返回体检指标（CPU 通常 < 1 秒）。

### `POST /api/ml/explain`
⭐ LLM 推荐理由：一批卡片**合并成 1 次调用**，结果按 `(用户, 卡片, 画像指纹)` 缓存。

```json
{ "user_id": 1, "repo_ids": [16, 6, 19] }
```
→ `{ "explanations": { "16": "你在做 TTS 和 RVC，这个项目正好把语音识别、克隆、混音串起来了" }, "generated": 3, "model": "…" }`

### `GET /api/ml/recall?user_id=1&k=20`
向量召回演示：用用户向量在全部仓库里检索最相近的 k 个，并附个人模型打分。

---

## 九、LLM 画像归纳

### `GET /api/user/profile/summary?user_id=1`
读取缓存的画像归纳（画像指纹变了会返回 `null`）。

### `POST /api/user/profile/summarize?user_id=1&force=false`
⭐ 让 LLM 归纳画像（1 次调用）。

```json
{
  "ok": true,
  "summary": "专注 TTS/语音克隆与 AI Agent 的开发者，涉足 RVC 声线转换、Intel XPU 加速与 Vocaloid 调音工具",
  "interests": ["text-to-speech", "voice-cloning", "rvc", "ai-agents", "mcp", "intel-xpu"],
  "noise": ["ai", "python", "typescript", "热度"],
  "noise_applied": 4,
  "cached": false,
  "model": "deepseek-flash"
}
```
> `noise` 里的词会被写入**噪声黑名单**（表 `profile_noise`），后续构建画像时真正剔除 ——
> AI 的判断要生效，不是只展示。

---

## 十、调试

### `GET /api/stats/pool`
流量池分布与晋级统计（观察冷启动 → 爆款池的流转）。

---

## 十一、限流与防护一览

| 机制 | 参数 | 说明 |
|---|---|---|
| 本地限流总开关 | `LLM_LOCAL_RATE_LIMIT_ENABLED` | **默认关闭**（不限流）；`RECOFEED_LLM_LIMIT=1` 可恢复 |
| 免费模型限流（开关打开时） | 120 次/分钟、2000 次/天 | 本地先行拒绝，不打上游 |
| 模型级联 | `agnes-2.5-flash` → `agnes-3.0-flash` → `agnes-2.0-flash` | 任一模型 429/失败自动换下一个 |
| 推理模式 | `chat_template_kwargs.enable_thinking=false` | **必须关闭**：实测 4~10 倍提速（11.7s → 1.2s）|
| 模型冷却（反应式） | 单模型 429 后 60s / 全模型 429 后全局 20s | 不是提前拒绝，只在撞到上游限流时短暂避让 |
| 爬虫频控 | 60s 一次 | GitHub search 限流 10/min |
| 关键词上限 | 12 个/用户 | — |
| SQLite | 自动提交 + `busy_timeout=30s` | 规避"读→写升级死锁"（该死锁不走超时，见 `db/connection.py` 注释） |
