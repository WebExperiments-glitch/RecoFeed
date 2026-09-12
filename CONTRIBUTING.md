# 贡献指南

感谢你愿意为 RecoFeed 做出贡献。

本项目是**开发者内部版本（Beta）**，产品处于高速提交期，接口和数据结构可能随时变动。

---

## 开发环境

### 通用要求

| 工具 | 版本 |
|---|---|
| Python | 3.11+ |
| Node.js | 20+ |
| pnpm | 8+ |

### 前端

```bash
cd frontend
pnpm install
pnpm dev          # 开发服务器
pnpm build        # 生产构建
pnpm lint         # 代码检查
pnpm typecheck    # 类型检查
```

### 后端

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # 测试依赖
pytest -v
```

---

## 提交规范

采用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat:     新功能
fix:      修复 Bug
docs:     文档变更
style:    格式调整（不影响逻辑）
refactor: 重构
perf:     性能优化
test:     测试相关
chore:    构建/工具链变更
```

示例：

```
feat(recall): 新增遗珠召回通路
fix(tags): 修正 llama.cpp 被分词错误
docs: 补充冷启动策略说明
```

---

## 分支策略

```
main        稳定分支，可运行
dev         开发主分支
feat/*      功能分支
fix/*       修复分支
```

流程：`feat/xxx` → `dev` → `main`

---

## 代码规范

### Python

- 遵循 **PEP 8**
- 使用 **type hints**（函数签名必须标注）
- 函数保持单一职责，超过 50 行考虑拆分
- 关键算法必须写注释说明**为什么这么做**，而不只是做什么

```python
def is_deep_read(scroll_depth: float, dwell_ms: int, readme_chars: int) -> bool:
    """
    深读判定。

    ⚠️ 预估阅读时间必须加上限：长 README（8000字）按实际阅读速度
    需要 13 分钟，不加限制会导致大项目永远无法被判定为深读。
    """
    if scroll_depth < 0.95:
        return False
    est_sec = min(max(readme_chars / 300 * 60, 15), 180)   # 上限 180s
    return dwell_ms / 1000 >= est_sec * 0.5
```

### TypeScript / React

- 严格模式（`strict: true`）
- 组件使用函数式 + Hooks
- 禁止 `any`，必要时用 `unknown` + 类型守卫
- 动画只使用 GPU 合成属性：`transform` / `opacity` / `filter` / `backdrop-filter`

---

## 测试要求

| 类型 | 覆盖对象 | 要求 |
|---|---|---|
| 单元测试 | 算法函数（打分、衰减、惩罚） | **必须**，边界值要覆盖 |
| 集成测试 | API 接口 | 主要路径必须覆盖 |
| 前端测试 | 关键交互 | 建议 |

运行：

```bash
pytest -v                    # 后端
pnpm test                    # 前端
```

> ⚠️ **算法改动必须附带数值验证**。例如修改衰减系数，需要给出改动前后的曲线对比。
> 这是本项目的历史教训 —— 详见 `docs/算法实现规格.md` 第零章（4 个实测发现的缺陷）。

---

## 特别需要的贡献

### 1. 中文技术标签词典扩充 ⭐

`backend/tags/dict/tech_terms.txt`

jieba 默认会把技术名词切碎（实测：`llama.cpp` → `llama` + `cpp`）。
欢迎提交你遇到的技术分词问题：

```
词语 词频 词性
向量数据库 10000 n
微服务 10000 n
```

### 2. 新的召回通路

当前有 7 路召回，见 `backend/recall/`。
如果你有新的召回思路（例如按 README 语言、按 awesome-list 关联、按依赖关系），欢迎实现。

### 3. 冷启动策略优化

新人前 20 个推荐是最难的。如果你有更好的摸底策略，欢迎讨论。

---

## 提交 Pull Request

1. Fork 本仓库
2. 创建分支：`git checkout -b feat/your-feature`
3. 提交（遵循 Conventional Commits）
4. 确保 `pytest` 与 `pnpm build` 通过
5. 发起 PR，说明**改了什么、为什么改、怎么验证的**

PR 描述模板：

```markdown
## 改了什么

## 为什么改

## 怎么验证的
（附测试输出或数值对比）

## 影响范围
```

---

## 行为准则

- 友善、有耐心，对新人友好
- 技术讨论聚焦问题本身，不做人身攻击
- 不同意见请给出**理由和数据**，而不是"我觉得"

---

## 协议

提交贡献即表示你同意以 **MIT License** 授权你的贡献。

---

## 有疑问？

发起 Issue 或在 Discussions 讨论。
