"""灌入种子数据，让 Feed 在没有任何爬虫运行的情况下就能出结果。

用法：
    python backend/jobs/seed_data.py            # 建表 + 灌数据（幂等，可重复跑）
    python backend/jobs/seed_data.py --reset    # 先删库再灌

设计说明：
- 30 个仓库刻意分成三类，用来验证推流引擎的核心主张：
    A. 当红项目（stars 高、增速快）      → 应走 trending 召回
    B. 遗珠仓库（stars 低但质量高、久未更新）→ ⭐ 应被 forgotten 召回救活
    C. 新鲜仓库（近期创建，无人问津）     → 应走 fresh 召回
- 如果 Feed 里全是 A 类，说明反马太效应没生效，算法白写。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from db.connection import get_conn, init_db  # noqa: E402
from pool.state_machine import ensure_pool  # noqa: E402
from tags.extractor import extract_readme_tags, init_jieba  # noqa: E402

NOW = datetime.now(timezone.utc)


def _days_ago(n: float) -> str:
    return (NOW - timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ 种子仓库
# 字段顺序：owner, name, desc, lang, topics, stars, forks, license,
#           risk, quality, velocity, forgotten, freshness, created_ago, pushed_ago, readme
SEEDS: list[dict] = [
    # ============ A 类：当红项目 ============
    dict(owner="ollama", name="ollama", lang="Go",
         desc="Get up and running with Llama 3, Mistral, Gemma, and other large language models.",
         topics=["llm", "llama", "local-inference", "golang"],
         stars=112000, forks=9200, license="MIT", risk="safe",
         quality=0.95, velocity=0.88, forgotten=0.05, freshness=0.9,
         created=560, pushed=0.2, ci=1, tests=1, size=180000,
         readme="""# Ollama

Get up and running with large language models locally.

## Features
- Local inference for Llama 3, Mistral, Gemma, Phi
- Simple CLI and REST API
- GPU acceleration on NVIDIA and Apple Silicon
- Model library with quantized GGUF weights

## Quickstart
```bash
ollama run llama3
curl http://localhost:11434/api/generate -d '{"model":"llama3","prompt":"hello"}'
```

## Supported platforms
macOS, Linux, Windows. Quantization via GGUF, GGML backend.
"""),

    dict(owner="ggml-org", name="llama.cpp", lang="C++",
         desc="LLM inference in C/C++ with minimal dependencies, GGUF quantization and Vulkan/Metal backends.",
         topics=["llm", "inference", "gguf", "quantization", "cpp"],
         stars=72000, forks=10500, license="MIT", risk="safe",
         quality=0.96, velocity=0.90, forgotten=0.03, freshness=0.95,
         created=900, pushed=0.1, ci=1, tests=1, size=420000,
         readme="""# llama.cpp

Inference of Meta's LLaMA model in pure C/C++, no dependencies.

## Features
- GGUF quantization (Q4_K, Q5_K, Q8_0) for low memory footprint
- CPU inference with AVX2 and AVX512
- Vulkan, Metal, CUDA, and SYCL GPU backends
- Server mode compatible with OpenAI API
- Speculative decoding and grammar-constrained sampling

## Building
```bash
cmake -B build -DGGML_VULKAN=ON
cmake --build build --config Release -j
```

Runs on consumer hardware. Supports llama, mistral, qwen, phi architectures.
"""),

    dict(owner="microsoft", name="vscode", lang="TypeScript",
         desc="Visual Studio Code, the open source code editor with extension marketplace and LSP support.",
         topics=["editor", "ide", "electron", "typescript", "lsp"],
         stars=168000, forks=30500, license="MIT", risk="careful",
         quality=0.94, velocity=0.72, forgotten=0.02, freshness=0.92,
         created=3300, pushed=0.05, ci=1, tests=1, size=2500000,
         readme="""# Visual Studio Code

Visual Studio Code is a lightweight but powerful source code editor.

## Features
- IntelliSense code completion powered by language servers
- Built-in Git integration
- Debugger for multiple runtimes
- Extension marketplace with 50000+ extensions
- Remote development over SSH and containers

## Contributing
See CONTRIBUTING.md. Note: the product builds are proprietary;
this repository contains the source with a Microsoft-specific license.
"""),

    dict(owner="facebook", name="react", lang="JavaScript",
         desc="The library for web and native user interfaces, with declarative components.",
         topics=["react", "ui", "frontend", "javascript", "components"],
         stars=232000, forks=47500, license="MIT", risk="safe",
         quality=0.93, velocity=0.65, forgotten=0.03, freshness=0.85,
         created=3400, pushed=0.4, ci=1, tests=1, size=1800000,
         readme="""# React

React is a JavaScript library for building user interfaces.

## Declarative
React makes it painless to create interactive UIs with components.

## Component-Based
Build encapsulated components that manage their own state.

## Learn Once, Write Anywhere
Develop new features without rewriting existing code.
React Native lets you build mobile apps with the same component model.

```jsx
function App() {
  return <h1>Hello, world!</h1>;
}
```
"""),

    dict(owner="vercel", name="next.js", lang="TypeScript",
         desc="The React framework for the web with server components, routing and edge rendering.",
         topics=["react", "framework", "ssr", "frontend", "webpack"],
         stars=132000, forks=28400, license="MIT", risk="safe",
         quality=0.92, velocity=0.78, forgotten=0.04, freshness=0.93,
         created=2600, pushed=0.15, ci=1, tests=1, size=1400000,
         readme="""# Next.js

The React Framework for the Web.

## Features
- File-system routing with React Server Components
- Streaming SSR and incremental static regeneration
- Built-in image, font and script optimization
- Edge runtime deployment
- Turbopack bundler

```bash
npx create-next-app@latest
```
"""),

    # ============ B 类：遗珠仓库 ⭐ 本项目的存在意义 ============
    dict(owner="hwchase17", name="dspy-mini", lang="Python",
         desc="A tiny declarative prompt optimization library, 200 lines, no dependencies. Automatic prompt tuning with bootstrap few-shot.",
         topics=["llm", "prompt-engineering", "optimization", "python", "research"],
         stars=340, forks=28, license="MIT", risk="safe",
         quality=0.90, velocity=0.12, forgotten=0.94, freshness=0.10,
         created=980, pushed=690, ci=1, tests=1, size=1200,
         readme="""# dspy-mini

A minimal declarative prompt optimization library in 200 lines of pure Python.
No dependencies, no LangChain, no magic.

## Why
Most prompt optimizers are 50000 lines. This one you can read in one sitting.

## Features
- Bootstrap few-shot: automatically selects the best demonstrations
- Compiles a prompt program into optimized instructions
- Works with any LLM that accepts a string and returns a string
- Unit tested with 94% coverage

## Usage
```python
from dspy_mini import Program, BootstrapFewShot
optimizer = BootstrapFewShot(metric=my_metric, max_bootstrapped=8)
optimized = optimizer.compile(program, trainset=examples)
```

## Design notes
The optimizer treats prompts as programs with learnable parameters.
It scores candidate demonstrations by mutual information against the metric.
See paper.md for the derivation.
"""),

    dict(owner="s-tk", name="hexo-lite", lang="JavaScript",
         desc="A 3KB static site generator written from scratch, no build step, no node_modules. Markdown to HTML with incremental rebuilds.",
         topics=["static-site", "markdown", "ssg", "nodejs", "minimal"],
         stars=180, forks=12, license="MIT", risk="safe",
         quality=0.86, velocity=0.08, forgotten=0.96, freshness=0.08,
         created=1500, pushed=820, ci=1, tests=1, size=340,
         readme="""# hexo-lite

A static site generator in 3KB. Written from scratch, zero dependencies.

## Why another SSG
Because a blog generator should not need 400MB of node_modules.

## Features
- Markdown to HTML with front-matter parsing
- Incremental rebuild: only changed files are regenerated
- Template inheritance with a 40-line engine
- Live reload dev server
- Deploys as plain static files anywhere

## Benchmarks
1000 posts rebuild: full 1.2s, incremental 0.04s.
Compared to Hexo 6.3 (28s full build) this is 23x faster.

```bash
node hexo-lite.js build --src ./posts --out ./public
```
"""),

    dict(owner="karpathy-fan", name="micrograd-cn", lang="Python",
         desc="中文注释版的自动微分引擎教学实现，逐行讲解反向传播。纯 Python 300 行，配套 12 篇图文解析。",
         topics=["autograd", "deep-learning", "education", "backpropagation", "chinese"],
         stars=620, forks=95, license="MIT", risk="safe",
         quality=0.88, velocity=0.10, forgotten=0.91, freshness=0.12,
         created=1100, pushed=540, ci=0, tests=1, size=890,
         readme="""# micrograd 中文详解

一个从零手写的自动微分引擎，逐行中文注释，配套 12 篇图文解析。

## 为什么做这个
反向传播是深度学习的基石，但大多数人只会调 `loss.backward()`，
不知道那行代码背后发生了什么。这个仓库把它拆解到每一行。

## 内容
- `engine.py` —— 标量自动微分，支持加减乘除和 tanh
- `nn.py` —— 在上面搭出 MLP
- `tutorials/` —— 12 篇图文解析，从链式法则讲到计算图
- `tests/` —— 每个算子都有数值梯度校验

## 适合谁
读过反向传播公式但没写过一遍的人。不需要微积分基础之外的数学。
"""),

    dict(owner="quiet-dev", name="rustdbc", lang="Rust",
         desc="A zero-copy CSV to Parquet converter, 10x faster than pandas, single binary, no runtime.",
         topics=["rust", "parquet", "csv", "data-engineering", "performance"],
         stars=210, forks=19, license="Apache-2.0", risk="safe",
         quality=0.91, velocity=0.09, forgotten=0.93, freshness=0.09,
         created=1250, pushed=760, ci=1, tests=1, size=560,
         readme="""# rustdbc

Convert CSV to Parquet at 10x the speed of pandas, as a single static binary.

## Benchmarks
On a 4.2GB CSV with mixed types:
| tool | time | peak RSS |
|---|---|---|
| pandas + pyarrow | 214s | 11.8GB |
| duckdb | 41s | 3.2GB |
| **rustdbc** | **19s** | **340MB** |

The trick is a streaming schema inference pass followed by
column-wise encoding, so we never materialize the whole table.

## Install
```bash
cargo install --path .
rustdbc in.csv out.parquet --compression zstd
```

Zero-copy decoding, no JVM, no Python runtime.
"""),

    dict(owner="a11y", name="contrast-check", lang="TypeScript",
         desc="WCAG 2.2 contrast ratio checker with APCA support, 2KB gzipped, works in browser and Node.",
         topics=["accessibility", "wcag", "color", "design", "typescript"],
         stars=95, forks=7, license="MIT", risk="safe",
         quality=0.84, velocity=0.06, forgotten=0.89, freshness=0.15,
         created=1330, pushed=610, ci=1, tests=1, size=210,
         readme="""# contrast-check

Check color contrast against WCAG 2.2 and APCA, in 2KB.

## Features
- WCAG 2.0 / 2.1 / 2.2 contrast ratio (AA and AAA)
- APCA (the proposed WCAG 3.0 model) lightness contrast
- Suggests the nearest passing color when you fail
- Works in browser, Node, and Deno

```ts
import { check } from 'contrast-check';
check('#777777', '#ffffff');
// { ratio: 4.48, aa: false, aaa: false, apca: 63.2, suggestion: '#767676' }
```

Most libraries only do WCAG. APCA is what the accessibility community
is actually moving toward, and almost nobody ships it yet.
"""),

    dict(owner="oldschool", name="vanilla-router", lang="JavaScript",
         desc="A 1KB hash-free SPA router with nested routes and scroll restoration, no build step.",
         topics=["router", "spa", "vanilla-js", "frontend", "minimal"],
         stars=140, forks=11, license="MIT", risk="safe",
         quality=0.82, velocity=0.07, forgotten=0.90, freshness=0.11,
         created=1420, pushed=580, ci=1, tests=1, size=150,
         readme="""# vanilla-router

A 1KB SPA router. No build step, no framework, no dependencies.

## Features
- History API routing (no ugly `#`)
- Nested routes with relative paths
- Scroll restoration that actually works
- Route guards with async hooks
- 1.2KB minified, 0.6KB gzipped

```js
import { Router } from './vanilla-router.js';
const r = new Router(document.body);
r.add('/users/:id', ({ id }) => renderUser(id));
r.add('/users/:id/posts/*', ({ wildcard }) => renderPosts(wildcard));
r.start();
```

Written before every framework grew its own router and forgot why.
"""),

    dict(owner="ml-notes", name="attention-visualized", lang="Python",
         desc="Interactive attention mechanism visualizer for transformers, runs in Jupyter, exports GIF.",
         topics=["transformer", "attention", "visualization", "education", "pytorch"],
         stars=430, forks=52, license="MIT", risk="safe",
         quality=0.87, velocity=0.11, forgotten=0.88, freshness=0.13,
         created=1050, pushed=500, ci=1, tests=0, size=1400,
         readme="""# attention-visualized

See what the attention heads actually look at.

## What it does
Loads a HuggingFace model, runs your sentence through it, and draws
every attention head as an interactive heatmap you can hover.

## Why
Attention diagrams in papers are all hand-drawn averages.
Real models have 144 heads and they do wildly different things.

## Use
```python
from attn_viz import visualize
viz = visualize("bert-base-uncased", "The cat sat on the mat")
viz.save_gif("heads.gif", heads=range(0, 12), fps=4)
viz.animate_layer(6)   # single layer animation
```

Exports GIF and MP4 for slides. Works with BERT, GPT-2, T5.
"""),

    dict(owner="tiny-tools", name="sqlite-vec-demo", lang="Python",
         desc="Vector similarity search in pure SQLite, no extensions, no server. Demonstrates cosine and cosine on BLOBs.",
         topics=["sqlite", "vector-search", "embeddings", "rag", "python"],
         stars=270, forks=23, license="MIT", risk="safe",
         quality=0.85, velocity=0.10, forgotten=0.86, freshness=0.14,
         created=960, pushed=470, ci=1, tests=1, size=680,
         readme="""# sqlite-vec-demo

Vector search inside plain SQLite. No extension, no server, no Docker.

## The trick
Store float32 vectors as BLOBs, then write a Python UDF that does
cosine similarity. SQLite calls it per row; for under ~200k vectors
this is fast enough and you skip an entire piece of infrastructure.

## Benchmarks
100k vectors, 768 dims, top-10 query: 41ms warm.

```sql
SELECT id, cosine(query_vec, embedding) AS score
FROM docs
ORDER BY score DESC LIMIT 10;
```

## When not to use this
Above ~500k vectors, use sqlite-vec or faiss. This repo includes
a script that tells you where your crossover point is.
"""),

    # ============ C 类：新鲜仓库 ============
    dict(owner="newbie-ai", name="tiny-embed", lang="Python",
         desc="A 4MB embedding model that runs on CPU in 8ms, distilled from bge-small.",
         topics=["embeddings", "nlp", "distillation", "cpu", "onnx"],
         stars=42, forks=3, license="Apache-2.0", risk="safe",
         quality=0.80, velocity=0.55, forgotten=0.12, freshness=0.96,
         created=6, pushed=0.5, ci=1, tests=1, size=4200,
         readme="""# tiny-embed

A 4MB text embedding model. CPU inference in 8ms per sentence.

## Why
`bge-small` is 130MB and needs a GPU to be pleasant. For a search box
over 10000 documents you do not need that.

## Results (MTEB subset, 6 tasks)
| model | size | latency | avg |
|---|---|---|---|
| bge-small-en | 130MB | 89ms | 62.3 |
| all-MiniLM-L6 | 90MB | 41ms | 56.3 |
| **tiny-embed** | **4MB** | **8ms** | **59.1** |

Distilled with 3.2M pairs. Retains 95% of bge-small while being 32x smaller.

## Use
```python
from tiny_embed import encode
vec = encode("hello world")   # float32[384]
```
"""),

    dict(owner="just-ship", name="llm-cost-cli", lang="Go",
         desc="Estimate LLM API cost from a prompt file before you run it. Supports 30 providers, no API key needed.",
         topics=["llm", "cost", "cli", "golang", "tools"],
         stars=28, forks=2, license="MIT", risk="safe",
         quality=0.78, velocity=0.48, forgotten=0.08, freshness=0.97,
         created=2, pushed=0.2, ci=1, tests=1, size=180,
         readme="""# llm-cost-cli

Know what your prompt costs before you send it.

## Why
You batch 50k prompts overnight and wake up to a $4000 bill.
This tells you the number first.

```bash
llm-cost ./prompts/*.txt --model gpt-4o --out output --max-tokens 500
# 50000 prompts
#   input  12,340,000 tokens   $30.85
#   output  4,120,000 tokens   $41.20
#   total                     $72.05
```

Supports OpenAI, Anthropic, Gemini, Mistral, DeepSeek, Qwen, local llama.cpp.
Prices in a YAML file you can edit when they change (they always change).
"""),

    dict(owner="weekend", name="md-to-slides", lang="TypeScript",
         desc="Turn a markdown file into a slide deck, single HTML file output, no PowerPoint.",
         topics=["markdown", "slides", "presentation", "typescript", "cli"],
         stars=35, forks=4, license="MIT", risk="safe",
         quality=0.76, velocity=0.42, forgotten=0.10, freshness=0.95,
         created=5, pushed=1, ci=1, tests=1, size=260,
         readme="""# md-to-slides

Markdown in, a self-contained HTML slide deck out.

## Features
- `---` splits slides, speakers notes in HTML comments
- Syntax highlighting, math, mermaid diagrams
- Single HTML file, no runtime, email it and it works
- Presenter mode with notes and timer
- PDF export via browser print

```bash
npx md-to-slides talk.md --out talk.html --theme dracula
```

No PowerPoint, no Keynote, no Google Slides account.
"""),

    dict(owner="late-night", name="git-heatmap", lang="Rust",
         desc="Terminal git commit heatmap with timezone-aware streaks, renders in 40ms.",
         topics=["git", "cli", "visualization", "rust", "terminal"],
         stars=19, forks=1, license="MIT", risk="safe",
         quality=0.74, velocity=0.38, forgotten=0.06, freshness=0.94,
         created=4, pushed=1, ci=1, tests=0, size=140,
         readme="""# git-heatmap

Your GitHub contribution graph, but in the terminal, and honest.

## Features
- Timezone-aware: shows when you really committed, not when GitHub thought
- Renders 5 years in 40ms
- Highlights suspicious clusters (the 23:58 commit runs)
- Exports SVG

```bash
cargo run -- ~/projects/foo --years 3 --tz Asia/Shanghai
```

Written because the GitHub graph lies about late-night sessions.
"""),

    dict(owner="pixel-baker", name="procedural-clouds", lang="GLSL",
         desc="Volumetric cloud renderer in 200 lines of GLSL, raymarched, real-time on integrated graphics.",
         topics=["shader", "glsl", "graphics", "volumetric", "procedural"],
         stars=52, forks=8, license="MIT", risk="safe",
         quality=0.83, velocity=0.58, forgotten=0.14, freshness=0.93,
         created=7, pushed=2, ci=0, tests=0, size=90,
         readme="""# procedural-clouds

Realistic volumetric clouds, raymarched in 200 lines of GLSL.
Runs at 60fps on integrated graphics.

## Technique
Density field from 3 octaves of Perlin-Worley, then raymarch with
16 steps and a Henyey-Greenstein phase function. The trick to making
it cheap is a low-res depth prefill pass that skips empty space.

## Controls
- `1-4` presets: stratocumulus, cumulus, cumulonimbus, cirrus
- `WASD` fly, mouse look
- `T` time of day

## Files
- `clouds.frag` — the whole thing, heavily commented
- `note.md` — derivation of the density function
"""),

    dict(owner="type-nerd", name="ts-type-challenges", lang="TypeScript",
         desc="120 TypeScript type challenges with progressive hints and solutions.",
         topics=["typescript", "types", "learning", "challenges", "education"],
         stars=88, forks=14, license="MIT", risk="safe",
         quality=0.85, velocity=0.62, forgotten=0.11, freshness=0.92,
         created=45, pushed=5, ci=1, tests=1, size=420,
         readme="""# ts-type-challenges

120 type-level TypeScript puzzles, from easy to "why does this exist".

## Structure
Each challenge is a `.ts` file with a failing type assertion.
Run `pnpm test` to check. Hints are in comments, expand one at a time.
Solutions in `solutions/` — try before you look.

## Sample
```ts
// Level 3: make this compile
type Result = MyPick<{ a: 1; b: 2; c: 3 }, 'a' | 'c'>;
// expected: { a: 1; c: 3 }
```

Covers mapped types, conditional types, template literals, variance,
and 20 challenges on infer. Updated as TS releases new features.
"""),

    # ============ 混合 / 其它领域 ============
    dict(owner="torvalds", name="linux", lang="C",
         desc="Linux kernel source tree.",
         topics=["kernel", "os", "c", "linux"],
         stars=190000, forks=55000, license="GPL-2.0", risk="risky",
         quality=0.97, velocity=0.60, forgotten=0.01, freshness=0.90,
         created=5200, pushed=0.02, ci=1, tests=1, size=4500000,
         readme="""# Linux kernel

There are several guides for kernel developers and users.

## Building
```bash
make menuconfig
make -j$(nproc)
```

## License
GPL-2.0. Note the syscall exception. See COPYING.
"""),

    dict(owner="elastic", name="elasticsearch", lang="Java",
         desc="Free and open, distributed, RESTful search engine.",
         topics=["search", "java", "lucene", "database"],
         stars=71000, forks=25000, license="SSPL", risk="risky",
         quality=0.90, velocity=0.40, forgotten=0.05, freshness=0.70,
         created=4800, pushed=1.5, ci=1, tests=1, size=6000000,
         readme="""# Elasticsearch

Elasticsearch is a distributed, RESTful search and analytics engine.

## License
Source available under SSPL and Elastic License 2.0.
NOT OSI-approved open source. Self-hosting as a service has restrictions.
"""),

    dict(owner="redis", name="redis", lang="C",
         desc="In-memory data store used as cache, message broker and streaming engine.",
         topics=["database", "cache", "kv-store", "c"],
         stars=67000, forks=23800, license="RSALv2", risk="risky",
         quality=0.89, velocity=0.35, forgotten=0.06, freshness=0.68,
         created=5600, pushed=2.0, ci=1, tests=1, size=1100000,
         readme="""# Redis

Redis is an in-memory database that persists on disk.

## License
Starting with 7.4, Redis is licensed under RSALv2 / SSPLv1.
Not OSI open source. See LICENSE.txt.
"""),

    dict(owner="psf", name="cpython", lang="Python",
         desc="The Python programming language reference implementation.",
         topics=["python", "interpreter", "language", "c"],
         stars=64000, forks=30500, license="PSF-2.0", risk="safe",
         quality=0.95, velocity=0.55, forgotten=0.02, freshness=0.88,
         created=4900, pushed=0.3, ci=1, tests=1, size=3200000,
         readme="""# CPython

This is Python version 3.14.0 alpha.

## Build
```bash
./configure --with-pydebug
make -j$(nproc)
```

See the Developer's Guide for how to contribute.
Licensed under the PSF License Agreement.
"""),

    dict(owner="sveltejs", name="svelte", lang="JavaScript",
         desc="Cybernetically enhanced web apps. Compiler-based UI framework, no virtual DOM.",
         topics=["svelte", "framework", "compiler", "frontend"],
         stars=81000, forks=4300, license="MIT", risk="safe",
         quality=0.91, velocity=0.58, forgotten=0.04, freshness=0.87,
         created=2300, pushed=0.5, ci=1, tests=1, size=980000,
         readme="""# Svelte

Svelte compiles your components to tiny vanilla JavaScript at build time.

## No virtual DOM
The compiler knows what changes, so the runtime does not have to diff.

```svelte
<script>
  let count = $state(0);
</script>
<button onclick={() => count++}>{count}</button>
```

Runes in Svelte 5 make reactivity explicit and fine-grained.
"""),

    dict(owner="rust-lang", name="rust", lang="Rust",
         desc="Empowering everyone to build reliable and efficient software.",
         topics=["rust", "compiler", "language", "systems"],
         stars=99000, forks=12800, license="MIT", risk="safe",
         quality=0.96, velocity=0.62, forgotten=0.02, freshness=0.89,
         created=4200, pushed=0.1, ci=1, tests=1, size=5200000,
         readme="""# The Rust Programming Language

A language empowering everyone to build reliable and efficient software.

## Why Rust
- Memory safety without garbage collection
- Fearless concurrency
- Zero-cost abstractions

```bash
./x.py build && ./x.py install
```

Dual licensed under MIT and Apache-2.0.
"""),

    dict(owner="obsidianmd", name="obsidian-releases", lang="TypeScript",
         desc="Community plugins list and release artifacts for Obsidian.",
         topics=["obsidian", "notes", "markdown", "plugins"],
         stars=6500, forks=1500, license="NOASSERTION", risk="unknown",
         quality=0.70, velocity=0.45, forgotten=0.20, freshness=0.75,
         created=1900, pushed=1.0, ci=0, tests=0, size=320,
         readme="""# Obsidian Releases

This repo hosts the release artifacts and the community plugin index.

## Note
No standard open source license file. The application itself is
proprietary freeware; community plugins are separately licensed.
"""),

    dict(owner="fmt-tools", name="csv-wizard", lang="Java",
         desc="Interactive CSV cleaner with fuzzy column matching, runs offline, GUI included.",
         topics=["csv", "data-cleaning", "java", "gui", "tools"],
         stars=76, forks=9, license="GPL-3.0", risk="caution",
         quality=0.80, velocity=0.13, forgotten=0.82, freshness=0.18,
         created=1600, pushed=430, ci=0, tests=1, size=2800,
         readme="""# csv-wizard

Clean messy CSVs interactively, offline.

## Features
- Fuzzy column matching ("Custmer Name" → "Customer Name")
- Detect and fix encoding rot (Latin-1 vs UTF-8 mojibake)
- Duplicate row detection with key selection
- Undo everything, export the cleaning recipe as a script

## License
GPL-3.0. If you link this into a product, your product must be GPL too.

```bash
java -jar csv-wizard.jar messy.csv
```
"""),

    dict(owner="pico-labs", name="pico-tts", lang="Python",
         desc="A 6MB Chinese TTS model that runs in realtime on CPU, trained from scratch on 500 hours.",
         topics=["tts", "speech", "chinese", "cpu", "onnx"],
         stars=115, forks=17, license="Apache-2.0", risk="safe",
         quality=0.84, velocity=0.20, forgotten=0.72, freshness=0.25,
         created=700, pushed=280, ci=1, tests=1, size=3400,
         readme="""# pico-tts

A 6MB Chinese text-to-speech model. Realtime factor 0.3 on a single CPU core.

## Why
Most Chinese TTS is 500MB and wants a GPU. For offline apps,
a screen reader, or a game NPC, that is absurd overhead.

## Quality
Trained from scratch on 500h of Mandarin. MOS 3.9 vs
Edge TTS 4.2, but runs entirely offline with 6MB of weights.

## Architecture
VITS-style, but with a distilled duration predictor and 8-bit
quantized flow. ONNX runtime, no PyTorch at inference.

```python
from pico_tts import speak
speak("你好世界", voice="female", out="hello.wav")
```
"""),

    dict(owner="edu-oss", name="os-from-scratch-cn", lang="C",
         desc="从零实现一个操作系统内核，中文教程，riscv64 架构，每章可运行。",
         topics=["os", "kernel", "riscv", "education", "chinese"],
         stars=395, forks=63, license="MIT", risk="safe",
         quality=0.89, velocity=0.15, forgotten=0.80, freshness=0.20,
         created=1350, pushed=320, ci=1, tests=1, size=2200,
         readme="""# 从零实现操作系统

一本能跑的中文操作系统教程。基于 riscv64，每章代码都可以在 QEMU 里跑起来。

## 章节
1. 引导与第一个 C 函数
2. 页表与虚拟内存
3. 中断与异常
4. 进程与调度
5. 文件系统
6. 用户态与系统调用

## 特点
- 不依赖任何教学框架，从 bare metal 开始
- 每章都有完整的可运行代码，`make run` 直接在 QEMU 启动
- 附 12 篇原理讲解，说明为什么这样设计而不是那样

## 环境
```bash
make run    # 需要 qemu-system-riscv64 和 riscv64-gcc
```

面向想搞懂内核但被 Linux 源码规模劝退的人。
"""),

    # ============================================================
    # 以下为扩容种子 —— 缓存池架构需要足够大的候选池
    # ============================================================
    # 说明：队列要给用户灌 60 条、刷完还能源源不断补，
    #      只有 ~30 个仓库是撑不住的（刷两轮就见底，然后靠实时推荐
    #      重复推老内容）。所以按技术方向补齐到 120+ 个。
    #
    # 刻意覆盖多个方向，这样"定向补货"才能被验证：
    #   用户画像偏 LLM  → 补货方向应落在 llm/rag/inference
    #   用户画像偏前端  → 补货方向应落在 frontend/react/vue
    # 如果两个用户补到的货一模一样，说明定向补货没生效。
    # ============================================================

    # ---------------- LLM / 推理 ----------------
    dict(owner="ggml-org", name="gguf-spec", lang="Markdown",
         desc="GGUF 格式规范与量化类型参考：Q4_K / Q5_K / IQ 系列到底怎么排布的。",
         topics=["llm", "gguf", "quantization", "spec", "inference"],
         stars=680, forks=58, license="MIT", risk="safe",
         quality=0.84, velocity=0.30, forgotten=0.79, freshness=0.48,
         created=560, pushed=38, ci=1, tests=1, size=1800,
         readme="""# GGUF 格式规范

llama.cpp 的模型格式，把每个字段讲清楚。

## 为什么需要单独一份文档
官方文档只列了结构体定义，没说"这个字段为什么存在"、
"量化块里的 scale 和 min 是怎么用的"。看代码能看懂，但很慢。

## 内容
- 文件头：magic、版本、tensor 数量、metadata KV 对
- Tensor 描述：维度、类型、偏移（按 32 字节对齐的原因）
- 量化类型逐个拆解：
  - `Q4_0` / `Q4_1`：最早的 4-bit，块内一个 scale
  - `Q4_K` / `Q5_K`：分层的 scale + min，super-block 结构
  - `IQ4_XS`：用查找表代替乘法，解码更快
- 每个量化类型的实际显存占用和困惑度损失对照表

## 配套
附一个 Python 脚本，直接 dump 任意 gguf 的 header 和前 10 个 tensor 信息。
""" ),

    dict(owner="sveltejs", name="svelte-compiler", lang="TypeScript",
         desc="深入 Svelte 编译产物：从 .svelte 源码到最终 DOM 操作指令的完整变换链路。",
         topics=["frontend", "svelte", "compiler", "build-tools"],
         stars=920, forks=74, license="MIT", risk="safe",
         quality=0.85, velocity=0.28, forgotten=0.78, freshness=0.45,
         created=620, pushed=44, ci=1, tests=1, size=3600,
         readme="""# Svelte 编译产物剖析

Svelte 说"编译后没有运行时"，那编译出来的到底是什么？

## 内容
以一个 30 行的计数器组件为例，逐阶段展示：

1. **Parse** —— 模板 AST，`{#if}` / `{#each}` 变成什么节点
2. **Analyze** —— 哪些变量是响应式的，依赖图怎么建
3. **Transform** —— runes（`$state` / `$derived`）如何降级为 signal
4. **Generate** —— 最终输出两个函数：`create_fragment` 和 `update`

## 关键发现
`$derived` 不是惰性求值，编译期就插入了 dirty 标记检查。
这解释了为什么派生值链很长时 Svelte 仍然很快。

## 配套
每个阶段都有实际输出粘贴，可以对照 `svelte/compiler` 的 API 自己复现。
""" ),

    dict(owner="vllm-project", name="vllm", lang="Python",
         desc="A high-throughput and memory-efficient inference and serving engine for LLMs.",
         topics=["llm", "inference", "serving", "cuda", "paged-attention"],
         stars=42000, forks=6800, license="Apache-2.0", risk="safe",
         quality=0.93, velocity=0.80, forgotten=0.04, freshness=0.82,
         created=1100, pushed=0.1, ci=1, tests=1, size=62000,
         readme="""# vLLM

High-throughput LLM serving with PagedAttention.

## Highlights
- PagedAttention for KV cache memory management
- Continuous batching, 24x throughput over naive serving
- Tensor parallelism and pipeline parallelism
- OpenAI-compatible API server

## Quickstart
```python
from vllm import LLM, SamplingParams
llm = LLM(model="meta-llama/Llama-3-8B")
out = llm.generate(["Hello, my name is"], SamplingParams(temperature=0.8))
```

Supports Llama, Mistral, Qwen, Gemma, and most Hugging Face architectures.
"""),

    dict(owner="huggingface", name="text-generation-inference", lang="Rust",
         desc="Large Language Model Text Generation Inference server written in Rust.",
         topics=["llm", "inference", "rust", "serving", "quantization"],
         stars=9200, forks=780, license="Apache-2.0", risk="safe",
         quality=0.88, velocity=0.55, forgotten=0.12, freshness=0.7,
         created=980, pushed=0.5, ci=1, tests=1, size=41000,
         readme="""# Text Generation Inference

Production-ready LLM serving in Rust.

## Features
- Token streaming over SSE
- Continuous batching with custom CUDA kernels
- Flash Attention and Paged Attention
- Bitsandbytes, GPTQ, AWQ, EETQ quantization
- Distributed tracing with OpenTelemetry

## Run
```bash
docker run --gpus all -p 8080:80 \\
  ghcr.io/huggingface/text-generation-inference:latest \\
  --model-id mistralai/Mistral-7B-Instruct-v0.3
```

Used in production by Hugging Face Inference Endpoints.
"""),

    dict(owner="ollama-labs", name="ollama-lite", lang="Go",
         desc="Minimal Ollama reimplementation for embedded devices and edge boxes.",
         topics=["llm", "edge", "golang", "inference", "embedded"],
         stars=430, forks=41, license="MIT", risk="safe",
         quality=0.83, velocity=0.22, forgotten=0.82, freshness=0.35,
         created=880, pushed=210, ci=1, tests=1, size=3400,
         readme="""# ollama-lite

跑在路由器、树莓派、旧手机上的极简大模型服务。

## 为什么做这个
Ollama 很好用，但它默认要吃 2GB 内存、编译要拉一堆 CUDA 依赖。
这个项目把运行时砍到 18MB，能在只有 512MB 内存的 ARM 盒子上跑 1B 模型。

## 与 Ollama 的差异
| | Ollama | ollama-lite |
|---|---|---|
| 运行时体积 | ~1.2GB | 18MB |
| 最低内存 | 2GB | 512MB |
| 后端 | CUDA/Metal | 纯 CPU (NEON) |
| 模型格式 | GGUF | GGUF |

## 用法
```bash
./ollama-lite serve --model tinyllama-q4.gguf --ctx 2048
curl localhost:11434/api/generate -d '{"prompt":"hello"}'
```

不是要取代 Ollama，是给那些"根本装不上 Ollama"的设备一个选择。
"""),

    dict(owner="sgl-project", name="sglang", lang="Python",
         desc="Structured generation language and runtime for LLM serving with RadixAttention.",
         topics=["llm", "serving", "structured-generation", "radix-attention"],
         stars=8100, forks=920, license="Apache-2.0", risk="safe",
         quality=0.89, velocity=0.70, forgotten=0.10, freshness=0.75,
         created=620, pushed=0.4, ci=1, tests=1, size=28000,
         readme="""# SGLang

Fast serving framework for LLMs with RadixAttention.

## Core idea
Prefix caching via a radix tree over the KV cache. Multi-turn chat and
few-shot prompts share prefixes, so re-prefill cost drops dramatically.

## Performance
- 5x throughput on multi-turn benchmarks vs naive vLLM
- 3x faster on structured output (JSON schema constrained)
- Frontend DSL for chained generation calls

## Example
```python
import sglang as sgl

@sgl.function
def qa(s, question):
    s += sgl.user(question)
    s += sgl.assistant(sgl.gen("answer", max_tokens=256))
```
"""),

    dict(owner="quip-ai", name="spec-decode-kit", lang="Python",
         desc="Drop-in speculative decoding: draft-model framing, tree attention, and acceptance-rate tooling.",
         topics=["llm", "inference", "speculative-decoding", "optimization"],
         stars=210, forks=18, license="MIT", risk="safe",
         quality=0.85, velocity=0.30, forgotten=0.86, freshness=0.30,
         created=430, pushed=95, ci=1, tests=1, size=1800,
         readme="""# spec-decode-kit

Speculative decoding that you can actually measure.

## The problem
Everyone says "2x speedup from speculative decoding". In practice you get
1.1x because nobody profiles the acceptance rate per token position.

## What this gives you
- Acceptance-rate heatmap: which token positions eat your draft budget
- Tree attention scaffolding with configurable depth/branching
- Draft-model selection helper (compares vocab overlap before you commit)
- Works with Hugging Face generate() via a logits processor

## Usage
```python
from specdecode import SpeculativeRunner, profile_acceptance
runner = SpeculativeRunner(target="Qwen2.5-7B", draft="Qwen2.5-0.5B")
profile_acceptance(runner).to_html("acceptance.html")
```

实测：A100 上 Qwen2.5 7B + 0.5B draft，长文本生成 1.8x，代码生成 1.3x。
"""),

    # ---------------- RAG / 向量检索 ----------------
    dict(owner="chroma-core", name="chroma", lang="Python",
         desc="The AI-native open-source embedding database for building LLM apps with memory.",
         topics=["rag", "vector-database", "embeddings", "llm"],
         stars=15200, forks=1280, license="Apache-2.0", risk="safe",
         quality=0.90, velocity=0.62, forgotten=0.08, freshness=0.8,
         created=1050, pushed=0.6, ci=1, tests=1, size=24000,
         readme="""# Chroma

Embedding database for LLM applications.

## Quickstart
```python
import chromadb
client = chromadb.Client()
col = client.create_collection("docs")
col.add(documents=["..."], ids=["a"])
col.query(query_texts=["how do I"], n_results=5)
```

## Features
- Runs in-process, or as a server
- HNSW index with metadata filtering
- Multi-modal embeddings (text + image)
- Built-in embedding function adapters

## Persistence
```python
client = chromadb.PersistentClient(path="./db")
```
"""),

    dict(owner="pgvector", name="pgvector", lang="C",
         desc="Open-source vector similarity search for Postgres.",
         topics=["rag", "vector-search", "postgres", "embeddings"],
         stars=12800, forks=620, license="PostgreSQL", risk="safe",
         quality=0.91, velocity=0.48, forgotten=0.06, freshness=0.72,
         created=1600, pushed=1.2, ci=1, tests=1, size=5600,
         readme="""# pgvector

Vector similarity search inside Postgres.

## Index types
- HNSW: best recall/latency tradeoff
- IVFFlat: lower build memory

## Usage
```sql
CREATE EXTENSION vector;
CREATE TABLE items (id bigserial, embedding vector(1536));
CREATE INDEX ON items USING hnsw (embedding vector_cosine_ops);

SELECT * FROM items ORDER BY embedding <=> '[...]' LIMIT 5;
```

Supports L2, inner product, cosine, and binary quantized vectors.
Integrates with every Postgres client — no extra service to run.
"""),

    dict(owner="tianshu-labs", name="rag-eval-harness", lang="Python",
         desc="Answer-quality eval harness for RAG pipelines: context recall, groundedness, and citation checks.",
         topics=["rag", "evaluation", "llm", "testing"],
         stars=340, forks=29, license="MIT", risk="safe",
         quality=0.86, velocity=0.26, forgotten=0.84, freshness=0.40,
         created=520, pushed=140, ci=1, tests=1, size=2100,
         readme="""# rag-eval-harness

你的 RAG 到底行不行，别靠感觉。

## 三个真正会出问题的指标
1. **Context recall** —— 检索出来的片段里有没有答案（不是相似度）
2. **Groundedness** —— 模型的每句话能不能在上下文里找到出处
3. **Citation precision** —— 引用的片段是不是真的支持那句话

相似度高但 recall 低是 RAG 最常见的失败模式，而且大部分评测工具查不出来。

## 用法
```bash
rag-eval run --pipeline my_rag.py --dataset qa.jsonl --out report.html
```

输出每个失败样本的检索片段、模型回答、以及"哪一句没有出处"。
不依赖任何 API，本地模型也能跑。
"""),

    dict(owner="vectordb-community", name="usearch", lang="C++",
         desc="Smaller & faster single-file similarity search engine with bindings for 10+ languages.",
         topics=["vector-search", "hnsw", "simd", "embeddings"],
         stars=2400, forks=180, license="Apache-2.0", risk="safe",
         quality=0.87, velocity=0.40, forgotten=0.28, freshness=0.62,
         created=780, pushed=12, ci=1, tests=1, size=4800,
         readme="""# USearch

Single-file vector search engine with SIMD kernels.

## Why another ANN library
Most HNSW implementations allocate per-node pointers scattered across the heap.
USearch uses contiguous memory + 8-bit quantized levels, so cache misses drop sharply.

## Numbers
- 10x faster index build than hnswlib on 100M vectors
- 4 bytes/vector overhead for the graph structure
- Handles up to 4 billion vectors per index

## Bindings
Python, JavaScript, Java, Go, Rust, C#, Swift, Objective-C, Ruby, Wolfram.

```python
from usearch.index import Index
index = Index(ndim=256, metric='cos')
index.add(keys, vectors)
index.search(query, 10)
```
"""),

    # ---------------- Agent / 工具调用 ----------------
    dict(owner="langchain-proto", name="tool-schema-forge", lang="Python",
         desc="Turn messy REST docs into LLM-ready tool schemas with validation and dedupe.",
         topics=["agent", "tool-calling", "llm", "openapi"],
         stars=520, forks=47, license="MIT", risk="safe",
         quality=0.84, velocity=0.34, forgotten=0.80, freshness=0.45,
         created=400, pushed=60, ci=1, tests=1, size=2600,
         readme="""# tool-schema-forge

把 OpenAPI / Swagger 文档变成模型能用的工具定义。

## 为什么需要它
丢一份 200 个接口的 OpenAPI 给模型，它会挑错接口、参数填错类型、
然后在 8 轮之后忘记自己要干什么。问题不在模型，在工具描述太糙。

## 做什么
- 按语义聚类接口，把 200 个工具压成 18 个（模型上下文吃得下）
- 参数类型规范化：`string` + `format: date` → 真正的 date 校验
- 自动去重：三个接口本质是同一个操作时合并成一个
- 生成 few-shot 示例，实测工具调用成功率从 61% → 89%

## 用法
```python
from forge import from_openapi
tools = from_openapi("api.yaml", max_tools=20, cluster=True)
```
"""),

    dict(owner="bytedance-ui", name="ui-tars", lang="Python",
         desc="End-to-end GUI agent model: perception, grounding, and action in a unified VLM.",
         topics=["agent", "gui", "vlm", "automation"],
         stars=6700, forks=520, license="Apache-2.0", risk="safe",
         quality=0.88, velocity=0.66, forgotten=0.14, freshness=0.68,
         created=480, pushed=1.5, ci=1, tests=1, size=15000,
         readme="""# UI-TARS

GUI agent that operates real applications from screenshots.

## Approach
Rather than bolting a planner onto a VLM, UI-TARS is trained end-to-end on
screen-action pairs. Perception and grounding share the same visual encoder,
so it does not lose track of "the button I just identified" between steps.

## Capabilities
- Cross-app workflows (open browser → search → copy → paste into editor)
- Coordinates normalized to screen size (resolution independent)
- Mid-trajectory error recovery

## Benchmark
OSWorld: 24.6% → 42.5% success rate vs prior GUI agents.
"""),

    dict(owner="openagents-cn", name="mcp-gateway", lang="Go",
         desc="Unified MCP gateway: one endpoint, permissioned access, audit log for all your MCP servers.",
         topics=["agent", "mcp", "gateway", "golang"],
         stars=890, forks=96, license="MIT", risk="safe",
         quality=0.86, velocity=0.58, forgotten=0.42, freshness=0.72,
         created=310, pushed=4, ci=1, tests=1, size=3900,
         readme="""# mcp-gateway

一个入口管住所有 MCP server。

## 解决什么问题
接了 12 个 MCP server 之后：配置散在 6 个客户端里，某个 server 能读你
本地任意文件而你不知道，出问题没有任何日志。

## 功能
- 单端口聚合：客户端只连网关，网关分发到各 server
- 权限控制：按 server × 工具 授权，文件类工具有路径白名单
- 审计日志：谁在什么时候调了什么工具、参数是什么、返回什么
- 热重载：加 server 不用重启客户端

## 配置
```yaml
servers:
  filesystem:
    cmd: npx -y @modelcontextprotocol/server-filesystem /data
    allow_paths: ["/data/projects"]
```

## 启动
```bash
mcp-gateway --config gateway.yaml --port 7800
```
"""),

    dict(owner="agentloop", name="trajectory-viewer", lang="TypeScript",
         desc="Trace viewer for LLM agent runs: replay every tool call, token cost, and failure point.",
         topics=["agent", "observability", "debugging", "llm"],
         stars=280, forks=22, license="MIT", risk="safe",
         quality=0.82, velocity=0.28, forgotten=0.85, freshness=0.38,
         created=290, pushed=75, ci=1, tests=1, size=1600,
         readme="""# trajectory-viewer

Agent 跑失败了，先别改 prompt，先看清楚它到底干了什么。

## 为什么现有工具不够
LangSmith 这类平台很全，但要联网、要传数据。本地跑 agent 的时候
你只是想看那一次失败运行的完整轨迹。

## 做什么
把一个 JSONL 轨迹文件拖进浏览器，得到一个可交互的回放视图：
- 时间轴：每一步耗时，哪一步卡了 40 秒
- 工具调用树：嵌套调用展开
- Token 花费：按步骤拆解，找出哪一步在烧钱
- Diff 对比：两次运行的轨迹并排对照

## 用法
```bash
npx trajectory-viewer run.jsonl
```

纯前端，数据不出本地。
"""),

    # ---------------- 前端 / Web ----------------
    dict(owner="vuejs", name="core", lang="TypeScript",
         desc="Vue.js is a progressive, incrementally-adoptable JavaScript framework for building UI.",
         topics=["frontend", "vue", "javascript", "framework"],
         stars=47800, forks=8300, license="MIT", risk="safe",
         quality=0.92, velocity=0.42, forgotten=0.03, freshness=0.86,
         created=3800, pushed=0.4, ci=1, tests=1, size=34000,
         readme="""# Vue Core

Progressive JavaScript framework.

## Two APIs
Options API for approachable code, Composition API for large apps:
```javascript
import { ref, computed } from 'vue'
const count = ref(0)
const double = computed(() => count.value * 2)
```

## Reactivity
Proxy-based reactivity system with automatic dependency tracking.
Works with plain objects — no setState, no hooks rules.

## Ecosystem
Vue Router, Pinia, Vite, Vitest, Vue Devtools.
Full TypeScript support with Volar.
"""),

    dict(owner="frontend-perf", name="render-cost-profiler", lang="TypeScript",
         desc="Measure which component actually causes each layout shift and reflow, in dev and in production builds.",
         topics=["frontend", "performance", "profiling", "devtools"],
         stars=540, forks=41, license="MIT", risk="safe",
         quality=0.83, velocity=0.34, forgotten=0.81, freshness=0.42,
         created=350, pushed=56, ci=1, tests=1, size=2400,
         readme="""# render-cost-profiler

别再说"页面有点卡"，直接说清是哪一行卡的。

## 现有工具的缺口
Chrome DevTools 能告诉你"这里发生了 layout shift"，但如果是 React/Vue
应用，它不会告诉你是哪个组件、哪次 state 更新导致的。

## 做什么
在开发模式下给每个组件打上标记，采集：
- 每次 reflow / layout shift 的发起组件（按源码位置）
- 该次渲染的 props/state 变化（diff 形式）
- 累计耗时排行：哪个组件在 5 秒内重渲染了 400 次

生产环境可以用 `?profile=1` 打开采样模式，开销低于 3%。

## 用法
```tsx
<Profiler provider="react">
  <App />
</Profiler>
```
""" ),

    dict(owner="webgpu-labs", name="compute-shader-lab", lang="TypeScript",
         desc="Write and benchmark WebGPU compute shaders in the browser with side-by-side CPU comparison.",
         topics=["webgpu", "gpu", "compute", "shaders"],
         stars=980, forks=88, license="MIT", risk="safe",
         quality=0.86, velocity=0.42, forgotten=0.66, freshness=0.62,
         created=420, pushed=16, ci=1, tests=1, size=4200,
         readme="""# compute-shader-lab

浏览器里写 WebGPU 计算着色器，实时和 CPU 版本比性能。

## 为什么
WebGPU 的 compute shader 报错信息极其难懂，而且很难判断
"我这个 kernel 到底比 JS 快还是慢"。

## 功能
- WGSL 编辑器，带错误定位（映射回 WGSL 行号，不是 SPIR-V）
- 同一算法自动跑 CPU 参考实现，并排显示结果与耗时
- 支持 workgroup 大小、workgroup 数量的实时调参
- 内置常见 kernel 模板：矩阵乘、前缀和、直方图、并行归约

## 实测数据
M1 MacBook 上：4096×4096 矩阵乘，WebGPU 11ms，JS（typed array）1240ms。
核显上差距会小很多，工具有"跨设备对比"视图。
""" ),

    dict(owner="ui-anatomy", name="component-anatomy", lang="TypeScript",
         desc="Interactive breakdowns of how real-world UI components are actually implemented, pixel by pixel.",
         topics=["frontend", "ui", "education", "css"],
         stars=430, forks=35, license="MIT", risk="safe",
         quality=0.81, velocity=0.24, forgotten=0.85, freshness=0.34,
         created=280, pushed=84, ci=1, tests=0, size=1300,
         readme="""# component-anatomy

拆开看真实 UI 组件是怎么做出来的。

## 一个下拉菜单要处理多少事
看起来是个框加一个列表，实际上：
- 键盘导航（方向键、Home/End、Esc）
- 焦点管理（打开时焦点去哪、关闭时还给谁）
- 边缘翻转（下方空间不够时往上弹）
- 点击外部关闭，但点自己内部不关
- ARIA：`role`、`aria-expanded`、`aria-activedescendant` 的配合

每个组件都拆成"能用的最小实现"和"生产级实现"两版对照，
并标注每一处处理是为了解决什么真实问题。

已覆盖：下拉菜单、模态框、标签页、日期选择器、虚拟滚动列表、Toast。
""" ),

    dict(owner="state-machines", name="xstate-lite", lang="TypeScript",
         desc="A 3KB state machine library with actor model, for UIs that have grown too many boolean flags.",
         topics=["frontend", "state-machine", "typescript", "architecture"],
         stars=620, forks=52, license="MIT", risk="safe",
         quality=0.82, velocity=0.30, forgotten=0.80, freshness=0.44,
         created=400, pushed=48, ci=1, tests=1, size=900,
         readme="""# xstate-lite

3KB 的状态机，专治 `isLoading && !isError && hasData` 这种代码。

## 问题
一个表单有 5 个布尔标志 → 32 种组合，其中 20 种是非法状态但代码里没排除。
bug 通常就藏在这些非法组合里。

## 解法
```ts
const form = createMachine({
  initial: 'idle',
  states: {
    idle:    { on: { SUBMIT: 'validating' } },
    validating: { on: { VALID: 'submitting', INVALID: 'idle' } },
    submitting: { on: { OK: 'done', FAIL: 'idle' } },
    done:    {},
  }
})
```

非法状态根本表示不出来。3KB，无依赖，附带 React/Vue/Svelte 适配层。
""" ),

    dict(owner="solidjs", name="solid", lang="TypeScript",
         desc="A declarative, efficient, and flexible JavaScript library for building user interfaces.",
         topics=["frontend", "reactivity", "performance", "typescript"],
         stars=32400, forks=920, license="MIT", risk="safe",
         quality=0.89, velocity=0.38, forgotten=0.09, freshness=0.74,
         created=1700, pushed=0.9, ci=1, tests=1, size=12000,
         readme="""# SolidJS

Fine-grained reactivity with JSX, no virtual DOM.

## Why it is fast
Components run once. Everything else is a subscription — when a signal
changes, only the exact DOM nodes that read it update.

```jsx
const [count, setCount] = createSignal(0)
const double = () => count() * 2
return <button onClick={() => setCount(c => c + 1)}>{double()}</button>
```

## Performance
Consistently top-3 on the JS Framework Benchmark, near-vanilla JS numbers,
with a 7KB runtime.
"""),

    dict(owner="tailwindlabs", name="tailwindcss", lang="TypeScript",
         desc="A utility-first CSS framework for rapidly building custom user interfaces.",
         topics=["frontend", "css", "utility-first", "design-system"],
         stars=84200, forks=4300, license="MIT", risk="safe",
         quality=0.91, velocity=0.45, forgotten=0.02, freshness=0.84,
         created=2600, pushed=0.3, ci=1, tests=1, size=38000,
         readme="""# Tailwind CSS

Utility-first CSS framework.

## Idea
Instead of naming things and writing CSS in a separate file, compose
utilities directly in markup:

```html
<button class="px-4 py-2 rounded-lg bg-blue-600 text-white
               hover:bg-blue-700 transition">
  Save
</button>
```

## v4 engine
Rewritten in Rust (Oxide), full builds 5x faster, incremental builds over 100x.
CSS-first config via `@theme` — no more JS config file required.
"""),

    dict(owner="shuimo-dev", name="glassui", lang="TypeScript",
         desc="Liquid Glass component library: real refraction, specular highlights, and depth for web UI.",
         topics=["frontend", "ui", "glassmorphism", "animation"],
         stars=670, forks=58, license="MIT", risk="safe",
         quality=0.85, velocity=0.44, forgotten=0.83, freshness=0.55,
         created=340, pushed=18, ci=1, tests=1, size=2900,
         readme="""# glassui

网页上的液态玻璃效果，不是简单加个 backdrop-filter 就完事。

## 为什么大部分毛玻璃看起来假
`backdrop-filter: blur(20px)` 只做了模糊，没有折射、没有边缘高光、
没有厚度感，所以看起来像贴了张半透明塑料膜。

## 这个库做了什么
1. **边缘折射** —— 用 SVG filter + displacement map 让边缘真的弯折背景
2. **镜面高光** —— 跟随指针位置的高光，玻璃才有"表面"
3. **层厚阴影** —— 内侧阴影 + 外侧扩散，产生真实的纵深感
4. **性能兜底** —— 检测设备能力，低端机自动降级为纯 blur

## 用法
```tsx
import { GlassCard, GlassNav } from 'glassui'
<GlassCard depth={12} refraction={0.6}>...</GlassCard>
```

包含 24 个组件，全部支持深色模式与 prefers-reduced-motion。
"""),

    dict(owner="vite-community", name="vite-plugin-inspect-lite", lang="TypeScript",
         desc="Inspect Vite's transform pipeline per-module without the full overhead of vite-plugin-inspect.",
         topics=["frontend", "vite", "build-tools", "debugging"],
         stars=190, forks=14, license="MIT", risk="safe",
         quality=0.80, velocity=0.24, forgotten=0.87, freshness=0.32,
         created=260, pushed=88, ci=1, tests=1, size=900,
         readme="""# vite-plugin-inspect-lite

想看 Vite 把某个文件编译成了什么，别去猜。

## 和 vite-plugin-inspect 的区别
原版功能全，但会拦下所有模块的中间产物存内存里，大项目一开内存直接翻倍。

这个只在你主动指定模块时才去挂 hook：
```ts
inspectLite({ only: ['src/components/**', '**/*.vue'] })
```

## 能看什么
- 每个 plugin 的输入/输出（按 transform 顺序）
- 哪一步把代码体积撑大了（带 diff 高亮）
- HMR 时到底重编译了哪几个模块

体积 4KB，dev 模式下零常驻开销。
"""),

    # ---------------- 数据 / 后端基础设施 ----------------
    dict(owner="duckdb", name="duckdb", lang="C++",
         desc="An in-process SQL OLAP database management system: analytical queries without a server.",
         topics=["database", "olap", "sql", "analytics", "columnar"],
         stars=24600, forks=1980, license="MIT", risk="safe",
         quality=0.94, velocity=0.64, forgotten=0.04, freshness=0.85,
         created=2100, pushed=0.2, ci=1, tests=1, size=88000,
         readme="""# DuckDB

In-process analytical database.

## Why
SQLite handles transactions, DuckDB handles analytics. Both are single files,
both embed in your process, no server to run.

## Vectorized execution
Processes data in batches of 2048 rows through a push-based pipeline.
A 10GB Parquet aggregation that takes 40s in pandas runs in 1.2s here.

## Usage
```sql
SELECT language, count(*) AS n
FROM 'repos.parquet'
GROUP BY language ORDER BY n DESC LIMIT 10;
```

Reads Parquet, CSV, JSON, and Arrow directly. Zero-copy Pandas integration.
"""),

    dict(owner="clickhouse", name="clickhouse", lang="C++",
         desc="Column-oriented OLAP database management system for real-time analytics.",
         topics=["database", "olap", "analytics", "columnar", "real-time"],
         stars=38600, forks=7200, license="Apache-2.0", risk="safe",
         quality=0.92, velocity=0.58, forgotten=0.03, freshness=0.82,
         created=4200, pushed=0.5, ci=1, tests=1, size=210000,
         readme="""# ClickHouse

Column-oriented DBMS for real-time analytics.

## What makes it fast
- Column storage: reads only the columns a query touches
- Vectorized execution over blocks of 65,536 rows
- Sparse primary index — skips granules without reading them
- MergeTree engine with background compaction

## Query
```sql
SELECT toStartOfHour(ts) AS h, count() AS n, quantile(0.99)(latency) AS p99
FROM events WHERE ts > now() - INTERVAL 1 DAY
GROUP BY h ORDER BY h;
```

Ingests millions of rows per second per node.
"""),

    dict(owner="redis-labs", name="redis", lang="C",
         desc="An in-memory data store used as a database, cache, streaming engine, and message broker.",
         topics=["database", "cache", "in-memory", "key-value"],
         stars=68400, forks=23800, license="AGPL-3.0", risk="caution",
         quality=0.90, velocity=0.35, forgotten=0.02, freshness=0.80,
         created=5200, pushed=0.7, ci=1, tests=1, size=196000,
         readme="""# Redis

In-memory data structure store.

## Data structures
Strings, hashes, lists, sets, sorted sets, bitmaps, hyperloglogs,
geospatial indexes, and streams. Each has O(1) or O(log N) commands
operating on them directly.

## Persistence
- RDB: point-in-time snapshots
- AOF: append-only command log, fsync per write if you want

## Note on license
Redis 7.4+ is AGPLv3. The last BSD-licensed version is 7.2.4.
Valkey is the Linux Foundation fork if AGPL is incompatible with your project.
"""),

    dict(owner="sqlite-org", name="sqlite", lang="C",
         desc="A self-contained, serverless, zero-configuration, transactional SQL database engine.",
         topics=["database", "embedded", "sql", "serverless"],
         stars=14200, forks=1180, license="Public Domain", risk="safe",
         quality=0.93, velocity=0.22, forgotten=0.05, freshness=0.70,
         created=7600, pushed=3.0, ci=1, tests=1, size=120000,
         readme="""# SQLite

Embedded SQL database, public domain.

## Properties
- Single file, no server, no configuration
- Transactions are ACID even across power loss
- 100% branch test coverage on the core
- Runs everywhere: phones, browsers (WASM), satellites, and every OS

## WAL mode
```sql
PRAGMA journal_mode=WAL;
```
Readers do not block writers, writers do not block readers.
This is the setting you want for any concurrent workload.

## Size
Full library: ~750KB. With extensions optional, ~600KB is typical.
"""),

    dict(owner="pgmq", name="pgmq", lang="Rust",
         desc="A lightweight message queue built on Postgres: SQS-like semantics in your existing database.",
         topics=["database", "queue", "postgres", "messaging"],
         stars=1900, forks=98, license="PostgreSQL", risk="safe",
         quality=0.85, velocity=0.42, forgotten=0.45, freshness=0.66,
         created=820, pushed=6, ci=1, tests=1, size=2800,
         readme="""# PGMQ

Postgres 里直接用的消息队列。

## 为什么不用 Kafka / RabbitMQ
如果你已经在跑 Postgres，为了一个每天几万条消息的队列再起一个中间件，
运维成本远超收益。PGMQ 用一张表 + SKIP LOCKED 实现了基本等价的语义。

## 功能
- 发送 / 读取 / 归档 / 删除
- 可见性超时（消息处理失败自动回到队列）
- 延迟消息
- 精确一次消费（配合长轮询 + 归档）

## 用法
```sql
SELECT pgmq.create('orders');
SELECT pgmq.send('orders', '{"id": 42}');
SELECT * FROM pgmq.read('orders', 30, 5);
```

实测单表 3000 万条消息，读取延迟仍是毫秒级。
"""),

    # ---------------- DevOps / 工具链 ----------------
    dict(owner="astral-sh", name="uv", lang="Rust",
         desc="An extremely fast Python package and project manager, written in Rust.",
         topics=["python", "package-manager", "rust", "tooling"],
         stars=19800, forks=480, license="MIT", risk="safe",
         quality=0.92, velocity=0.82, forgotten=0.05, freshness=0.90,
         created=680, pushed=0.3, ci=1, tests=1, size=14000,
         readme="""# uv

Python packaging, 10-100x faster than pip.

## What it replaces
pip, pip-tools, pipx, poetry, pyenv, twine, virtualenv — one binary.

```bash
uv venv                      # create venv in 5ms
uv pip install torch         # ~10x faster than pip
uv run pytest                # auto-sync deps then run
uv add requests              # add + lock + sync
```

## How
Rust implementation with a global content-addressable cache and hardlinks
instead of copies. Resolver is PubGrub-based and resolves in parallel.

## Compatibility
Drop-in for pip via `uv pip`. Reads pyproject.toml, requirements.txt, and
PEP 723 inline scripts.
"""),

    dict(owner="astral-sh", name="ruff", lang="Rust",
         desc="An extremely fast Python linter and code formatter, written in Rust.",
         topics=["python", "linter", "formatter", "rust"],
         stars=34200, forks=1180, license="MIT", risk="safe",
         quality=0.91, velocity=0.78, forgotten=0.03, freshness=0.88,
         created=1100, pushed=0.4, ci=1, tests=1, size=22000,
         readme="""# Ruff

Linter and formatter for Python, in Rust.

## Speed
30-50x faster than Flake8, 50-100x faster than Black on large codebases.
Linting CPython itself: 0.4s versus 30s.

## Coverage
Replaces Flake8, isort, pyupgrade, autoflake, pydocstyle, and Black's
formatting — over 800 rules total.

```bash
ruff check --fix .
ruff format .
```

## Configuration
```toml
[tool.ruff]
line-length = 100
[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```
"""),

    dict(owner="nektos", name="act", lang="Go",
         desc="Run your GitHub Actions locally: no more push-and-wait to test a workflow.",
         topics=["ci", "github-actions", "golang", "developer-tools"],
         stars=57400, forks=1560, license="MIT", risk="safe",
         quality=0.88, velocity=0.44, forgotten=0.07, freshness=0.76,
         created=1900, pushed=1.8, ci=1, tests=1, size=9800,
         readme="""# act

在本地跑 GitHub Actions。

## 痛点
改一行 workflow 就要 push、等 3 分钟、看日志、再改。一天下来光等 CI 就没了。

## 用法
```bash
act                    # 跑 push 事件对应的 workflow
act -j build           # 只跑 build job
act --list             # 列出所有可跑的任务
```

## 原理
读 `.github/workflows/*.yml`，用 Docker 起一个接近 runner 环境的容器，
按 job 依赖顺序执行 step。

## 注意
runner 镜像里的预装软件版本和 GitHub 官方不完全一致，
数据库/缓存服务需要自己用 services 段声明。
"""),

    dict(owner="tmux-next", name="zellij", lang="Rust",
         desc="A terminal workspace with batteries included: layouts, panes, and a plugin system.",
         topics=["terminal", "multiplexer", "rust", "cli"],
         stars=22800, forks=720, license="MIT", risk="safe",
         quality=0.87, velocity=0.48, forgotten=0.11, freshness=0.74,
         created=1600, pushed=2.5, ci=1, tests=1, size=7800,
         readme="""# Zellij

Terminal workspace with a discoverable interface.

## Design goal
tmux is powerful but you have to memorise its keybindings and config syntax.
Zellij shows you the keybindings on screen and ships sane defaults.

## Features
- Layout files (KDL) for reproducible workspaces
- Floating panes
- Session resurrection after reboot
- WebAssembly plugin system — plugins in any language that compiles to WASM

## Layout example
```kdl
layout {
    pane split_direction="vertical" {
        pane size="70%"
        pane
    }
}
```
"""),

    dict(owner="charmbracelet", name="bubbletea", lang="Go",
         desc="A powerful little TUI framework based on The Elm Architecture.",
         topics=["tui", "golang", "cli", "terminal"],
         stars=29800, forks=790, license="MIT", risk="safe",
         quality=0.89, velocity=0.40, forgotten=0.08, freshness=0.72,
         created=1500, pushed=3.5, ci=1, tests=1, size=4200,
         readme="""# Bubble Tea

TUI framework for Go.

## Architecture
Elm-style: `Init` → `Update(msg)` → `View()`. State is explicit,
side effects are commands, so async work never races your render loop.

```go
func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
    switch msg := msg.(type) {
    case tea.KeyMsg:
        if msg.String() == "q" { return m, tea.Quit }
    }
    return m, nil
}
```

## Companion libraries
Bubbles (components), Lip Gloss (styling), Glamour (markdown rendering).
Used by gh CLI, glow, soft-serve, and many others.
"""),

    dict(owner="foundry-tools", name="depgraph-viz", lang="Python",
         desc="Visualize why a dependency exists: shortest path from any package to the one you care about.",
         topics=["python", "dependency", "visualization", "tooling"],
         stars=160, forks=11, license="MIT", risk="safe",
         quality=0.79, velocity=0.20, forgotten=0.88, freshness=0.28,
         created=240, pushed=105, ci=1, tests=1, size=800,
         readme="""# depgraph-viz

`pip install` 之后装了一堆莫名其妙的东西，这个告诉你为什么。

## 场景
你的项目依赖包体积 400MB，其中 380MB 来自你从没听说过的某个传递依赖。

## 用法
```bash
depgraph-viz why numpy --project .        # numpy 是怎么被引进来的
depgraph-viz tree --max-depth 3           # 依赖树
depgraph-viz weight --top 20              # 谁吃掉了最多体积
depgraph-viz render --out graph.html      # 可交互的依赖图
```

只读锁文件和已安装元数据，不联网、不装东西。
"""),

    # ---------------- 教育 / 学习类（易被埋没的长尾） ----------------
    dict(owner="codecrafters-io", name="build-your-own-x", lang="Markdown",
         desc="Master programming by recreating your favorite technologies from scratch.",
         topics=["education", "learning", "tutorial", "awesome-list"],
         stars=312000, forks=29000, license="CC0-1.0", risk="safe",
         quality=0.90, velocity=0.38, forgotten=0.01, freshness=0.82,
         created=2400, pushed=1.5, ci=0, tests=0, size=4800,
         readme="""# Build your own X

Recreate the technologies you use, from scratch, to understand them.

## Categories
Build your own: Database, Docker, Git, Neural Network, Operating System,
Programming Language, Regex Engine, Shell, Text Editor, Web Server.

## Why it works
Reading about B-trees gives you vocabulary. Writing a storage engine that
actually needs one gives you intuition.

## How to use
Pick one, give it a weekend, do not look at the finished implementation
until yours runs — even badly.
"""),

    dict(owner="gkcs-edu", name="algo-visual-cn", lang="TypeScript",
         desc="中文算法可视化：每一步都能暂停、回退、看变量状态，而不是只看结果动画。",
         topics=["education", "algorithm", "visualization", "chinese"],
         stars=1780, forks=210, license="MIT", risk="safe",
         quality=0.85, velocity=0.30, forgotten=0.72, freshness=0.50,
         created=920, pushed=45, ci=1, tests=1, size=5200,
         readme="""# 算法可视化（中文）

## 和现有可视化网站的区别
大部分可视化只能看"动画播放"，你不知道中间那一步的数组长什么样、
递归栈里压了什么。

## 这个做了什么
- **单步执行** —— 每一步都能暂停，看完整变量状态
- **回退** —— 发现看漏了可以往回走，不用从头看
- **代码联动** —— 左边高亮当前执行行，右边显示数据状态
- **中文讲解** —— 每一步为什么这样做，配上对应代码

## 已覆盖
排序（8 种）、图论（最短路/最小生成树/拓扑）、动态规划（背包/LCS/编辑距离）、
字符串（KMP/Trie）、树（BST/AVL/线段树）。

面向刷题时"看懂了但写不出来"的人。
"""),

    dict(owner="rust-edu", name="too-many-linked-lists", lang="Rust",
         desc="Learn Rust by building linked lists in every possible way, including the ones that fight the borrow checker.",
         topics=["rust", "education", "data-structures", "learning"],
         stars=4100, forks=190, license="MIT", risk="safe",
         quality=0.86, velocity=0.25, forgotten=0.38, freshness=0.58,
         created=1900, pushed=60, ci=1, tests=1, size=3200,
         readme="""# Learn Rust With Entirely Too Many Linked Lists

The best way to understand Rust's ownership model is to fight it on purpose.

## Approach
Six implementations of the same data structure, each teaching a distinct lesson:

1. `Box`-based — why you cannot have two owners
2. `Rc` — reference counting, and why it is not enough
3. `RefCell` — interior mutability, runtime borrow checks
4. `Rc<RefCell>` — sharing mutable state, and the leak it can cause
5. Unsafe pointers — what the compiler was protecting you from
6. Arena allocation — the pragmatic production answer

## Why linked lists
They are a terrible data structure. That is the point — the awkward cases
are exactly where Rust's rules need explaining.
"""),

    dict(owner="mit-press-oss", name="db-internals-notes-cn", lang="Markdown",
         desc="数据库内核读书笔记：从 B+ 树页结构到 WAL 恢复，附可运行的最小实现。",
         topics=["database", "education", "chinese", "internals"],
         stars=890, forks=102, license="CC-BY-4.0", risk="safe",
         quality=0.87, velocity=0.18, forgotten=0.78, freshness=0.44,
         created=760, pushed=180, ci=1, tests=1, size=6400,
         readme="""# 数据库内核笔记

读《数据库系统内幕》的过程记录，每一章配一个能跑的最小实现。

## 章节与实现
| 章 | 主题 | 配套代码 |
|---|---|---|
| 3 | 文件格式与页布局 | `page/` 一个 slab 分配器 |
| 5 | B 树 | `btree/` 可持久化的 B+ 树 |
| 6 | 日志与恢复 | `wal/` 崩溃可恢复的 WAL |
| 7 | 事务隔离 | `mvcc/` 快照隔离实现 |
| 9 | LSM 树 | `lsm/` 带 compaction 的 LSM |

## 特点
不贴大段书摘。每章只有三部分：我的理解、实现里踩的坑、
以及"书上没讲但实现绕不过去"的地方（比如页分裂时的锁顺序）。

代码不到 8000 行，每章都能独立跑测试。
"""),

    dict(owner="ozzie-oss", name="regex-golf", lang="Python",
         desc="Learn regex by solving graded puzzles with instant visual explanation of every match.",
         topics=["education", "regex", "learning", "tooling"],
         stars=130, forks=9, license="MIT", risk="safe",
         quality=0.78, velocity=0.16, forgotten=0.89, freshness=0.26,
         created=200, pushed=120, ci=1, tests=1, size=600,
         readme="""# regex-golf

正则不是靠背，是靠看出它在做什么。

## 设计
每个谜题给你若干"应该匹配"和"绝对不该匹配"的字符串，
你写一个正则通过全部用例。每次提交会显示：

- 哪一条用例失败了，失败在哪个字符位置
- 正则的每一步是怎么走的（可视化回溯过程）
- 你的解法和最短解法的字符数对比

## 难度
1-20 关：字面量、字符类、量词
21-50 关：分组、反向引用、环视
51-80 关：回溯陷阱、性能（有超时用例）

零依赖，`python regex_golf.py` 直接开始。
"""),

    # ---------------- 安全 / 隐私 ----------------
    dict(owner="localsend", name="localsend", lang="Dart",
         desc="An open-source cross-platform alternative to AirDrop for local file transfer.",
         topics=["file-transfer", "privacy", "cross-platform", "p2p"],
         stars=54600, forks=2900, license="MIT", risk="safe",
         quality=0.89, velocity=0.56, forgotten=0.06, freshness=0.78,
         created=1300, pushed=1.2, ci=1, tests=1, size=18000,
         readme="""# LocalSend

跨平台局域网文件传输，不经过任何服务器。

## 为什么
AirDrop 只在苹果设备间工作，微信传文件会压缩、会上传、有大小限制。

## 工作方式
设备之间通过 UDP 广播发现，然后建立 HTTPS 直连传输。
双方确认后点对点发送，数据不出局域网。

## 平台
Android, iOS, Windows, macOS, Linux — 全部支持互传。
命令行版本可以集成到脚本里：
```bash
localsend-cli send --file data.zip --to 192.168.1.23
```
"""),

    dict(owner="privacy-tools", name="tracker-blocklist", lang="Python",
         desc="Curated tracker blocklists with a diff engine: see exactly what a new list would block.",
         topics=["privacy", "adblock", "security", "lists"],
         stars=420, forks=38, license="CC0-1.0", risk="safe",
         quality=0.80, velocity=0.24, forgotten=0.84, freshness=0.34,
         created=640, pushed=30, ci=1, tests=1, size=1400,
         readme="""# tracker-blocklist

订阅新的拦截列表之前，先看清楚它到底拦了什么。

## 问题
列表动辄几万条规则，你订阅下去不知道会不会把一个正常网站搞坏，
也不知道全是重复的旧规则。

## 工具做什么
```bash
tracker-blocklist diff old.txt new.txt
```
输出：新增域名、删除域名、语义等价但写法不同的规则。
再给每条新增域名标上"属于哪个公司/哪个 CDN"，
这样你一眼能看出新增的是拦截器还是某个正常服务。

## 列表本身
按类别拆分：广告、分析、指纹、社交追踪、恶意域名。
可以只订阅你需要的类别，而不是吃一整个大列表。
"""),

    dict(owner="cn-security", name="sbom-diff", lang="Python",
         desc="Diff two SBOMs to find newly introduced components and license changes before you ship.",
         topics=["security", "sbom", "supply-chain", "compliance"],
         stars=280, forks=31, license="Apache-2.0", risk="safe",
         quality=0.82, velocity=0.28, forgotten=0.81, freshness=0.42,
         created=380, pushed=52, ci=1, tests=1, size=1700,
         readme="""# sbom-diff

发布前先看这次版本比上次多了哪些依赖、改了哪些许可证。

## 为什么
SBOM 生成工具很多，但没人看。几十页的组件清单，你要找的是
"这次新增了什么"和"许可证变了吗"。

## 用法
```bash
sbom-diff before.spdx.json after.spdx.json --format markdown
```

输出：
- 新增组件及其引入路径（谁把它拉进来的）
- 许可证变化（MIT → AGPL 这种要立刻拦住）
- 已删除组件
- 版本升级且跨越了 major 版本的组件

CI 里可以设 `--fail-on-license AGPL`，直接卡住发布。
"""),

    # ---------------- 多媒体 / 创意 ----------------
    dict(owner="leejet-oss", name="stable-diffusion-cpp", lang="C++",
         desc="Stable Diffusion inference in C/C++, with GGUF quantization and Vulkan support.",
         topics=["image-generation", "diffusion", "cpp", "vulkan", "quantization"],
         stars=3100, forks=290, license="MIT", risk="safe",
         quality=0.87, velocity=0.54, forgotten=0.34, freshness=0.68,
         created=700, pushed=8, ci=1, tests=1, size=12000,
         readme="""# stable-diffusion.cpp

纯 C/C++ 的扩散模型推理，不需要 Python、不需要 CUDA。

## 支持
- SD 1.5 / SDXL / SD3 / FLUX.1
- GGUF 量化，Q4_K 模型比 fp16 小 4 倍
- 后端：CPU、CUDA、Vulkan、Metal

## 为什么关心 Vulkan
没有 NVIDIA 显卡的机器（Intel Arc、AMD 核显、国产 GPU）
也能用 GPU 加速生图，这在 llama.cpp 生态里已经被验证是可行路线。

顺带一提：这是纯本地推理，文生图 / 图生图都可以离线跑，
不联网、不上传 prompt，图片生成全过程在本机完成。

## 用法
```bash
./sd -m flux1-schnell-q4_k.gguf \\
     -p "a cat reading a book, oil painting" \\
     --vae flux-ae.safetensors -o out.png
```

## 内存
Q4_K 的 SDXL 在 8GB 显存 / 16GB 内存的机器上可以跑。
"""),

    dict(owner="audio-oss", name="stem-splitter", lang="Python",
         desc="Local stem separation with a simple CLI and quality-report output.",
         topics=["audio", "music", "demucs", "cli"],
         stars=640, forks=88, license="MIT", risk="safe",
         quality=0.83, velocity=0.36, forgotten=0.76, freshness=0.52,
         created=550, pushed=25, ci=1, tests=1, size=3200,
         readme="""# stem-splitter

本地分轨，顺手给你一份音质报告。

## 一般工具的流程
跑 Demucs → 拿到四个 wav → 听一下 → "好像有点糊但说不清哪糊"

## 这个多做了什么
分离完成后自动分析并打印报告：
- 每个轨道的频谱泄漏（人声轨里有多少鼓的能量）
- 相位抵消检测（伴奏轨是不是把某个频段挖掉了）
- 建议：如果某轨泄漏严重，提示换用 htdemucs_ft 还是 mdx_extra

```bash
stem-splitter track.mp3 --model htdemucs --report
```

批处理整张专辑也不会串味，输出目录按曲目自动分好。
"""),

    # ============================================================
    # 以下为 AI 语音方向补齐 —— 用户核心兴趣领域
    # ============================================================
    # 说明：压测发现库里 RVC / 语音克隆 / 语音合成相关仓库是 0 个，
    #      而这是用户实际在做的方向（GPT-SoVITS / RVC / IndexTTS）。
    #      一个推荐系统如果对用户的核心兴趣无货可推，
    #      那"定向补货"就永远补不出正确的东西 —— 这是数据层的缺口，
    #      不是算法问题。所以这里按 AI 语音全链路补齐。
    # ============================================================

    # ---------------- 语音克隆 / 变声（RVC 方向）----------------

    dict(owner="rvc-project", name="retrieval-based-voice-conversion-webui", lang="Python",
         desc="Retrieval-based Voice Conversion WebUI: train a singing voice model from 10 minutes of audio.",
         topics=["rvc", "voice-conversion", "voice-cloning", "audio", "singing"],
         stars=28600, forks=3400, license="MIT", risk="safe",
         quality=0.90, velocity=0.58, forgotten=0.05, freshness=0.78,
         created=1400, pushed=2.5, ci=1, tests=1, size=26000,
         readme="""# Retrieval-based Voice Conversion WebUI

RVC —— 用 10 分钟音频训练一个声音转换模型。

## 它做什么
不是文字转语音，是**音色转换**：把 A 的歌声变成 B 的音色，
保留原来的音高、节奏、咬字。

## 训练流程
1. 准备干燥人声（无伴奏、无混响），10~30 分钟
2. 切片 + 特征提取（hubert-base 最后一层）
3. 训练：先 pretrain 冻结，再 finetune 解冻 decoder
4. 导出：`model.pth` + `model.index`（检索索引，决定音色像不像）

## 关键参数
- `batch_size` 与显存的关系
- `总 epoch`：RVC 社区经验值 100~200，超过容易过拟合
- `index_rate`：推理时调，0.5~0.75 之间音色最稳

## 采样率
40k / 48k 二选一。48k 高频更完整，但对训练素材质量要求更高 ——
素材里有一点底噪就会被放大成"沙沙声"。

## 常见坑
- 咬字不清：多半是 index 训练不充分或 index_rate 太低
- 爆裂/噼啪声：切片边缘没对齐，或素材里有削波
- 音色不像：训练集里混入了多个说话人

```bash
python infer-web.py --pycmd python --port 7865
```
"""),

    dict(owner="applio-labs", name="applio", lang="Python",
         desc="Ultimate voice cloning tool with RVC, edge-tts and TTS support, improved training pipeline.",
         topics=["rvc", "voice-cloning", "tts", "audio", "training"],
         stars=2900, forks=340, license="MIT", risk="safe",
         quality=0.87, velocity=0.62, forgotten=0.48, freshness=0.70,
         created=680, pushed=6, ci=1, tests=1, size=19000,
         readme="""# Applio

RVC 的改良分支，重写了训练与推理管线。

## 相比原版 RVC 改了什么
原版 RVC 的几个老毛病：
- 训练脚本耦合在 WebUI 里，命令行跑不了
- 预处理阶段静默失败（切片坏了不报错，训练完才发现）
- 推理的 f0 提取器选项少，某些曲风出来全是电音

Applio 的做法：
- 把训练/推理拆成独立 CLI，`core.py` 是纯函数
- 预处理阶段每个环节校验中间产物，坏了立刻停
- 支持 rmvpe / crepe / fcpe 三种 f0 提取器

## 咬字问题的针对性改进
社区反馈最集中的是"咬字不清"。Applio 允许在训练时
冻结 hubert 的更多层，让音色更贴、咬字更清楚，
但代价是泛音会弱一些 —— 这是个取舍，不是纯改进。

```bash
python core.py train --model_name myvoice --epochs 200
```

## 注意
实时变声需要虚拟声卡（VB-Cable / VoiceMeeter），
延迟跟块大小和采样率强相关。
"""),

    dict(owner="voice-dev", name="rvc-realtime-gui", lang="Python",
         desc="Low-latency realtime voice changer GUI built on RVC with ASIO support.",
         topics=["rvc", "realtime", "voice-conversion", "asio", "gui"],
         stars=780, forks=120, license="MIT", risk="safe",
         quality=0.82, velocity=0.40, forgotten=0.79, freshness=0.55,
         created=520, pushed=22, ci=1, tests=1, size=6800,
         readme="""# rvc-realtime-gui

实时变声，延迟压到 90ms 以内。

## 延迟从哪来
一条链路拆开看：
- 采集缓冲块：块越大延迟越高，但太小会爆音
- f0 提取：rmvpe 比 crepe 快 3 倍，音质差距在变声场景听不出来
- 模型推理：GPU 上 8ms，CPU 上 40ms+
- 输出缓冲：同样要留余量

## 实测
| 配置 | 总延迟 |
|---|---|
| ASIO + GPU + rmvpe | 88ms |
| WASAPI + GPU + rmvpe | 140ms |
| WASAPI + CPU + crepe | 320ms |

超过 200ms 唱歌就会明显跟不上节拍。

## 用法
```bash
python gui.py --model myvoice.pth --index myvoice.index --chunk 160
```

`--chunk` 是块大小，160 是延迟和稳定性的平衡点。
"""),

    dict(owner="tts-oss", name="gpt-sovits", lang="Python",
         desc="Few-shot voice cloning TTS: clone a voice from 1 minute of audio, supports Chinese/English/Japanese.",
         topics=["tts", "voice-cloning", "few-shot", "audio", "speech-synthesis"],
         stars=34000, forks=4600, license="MIT", risk="safe",
         quality=0.92, velocity=0.68, forgotten=0.04, freshness=0.80,
         created=720, pushed=3.2, ci=1, tests=1, size=32000,
         readme="""# GPT-SoVITS

1 分钟音频就能克隆一个声音。

## 架构
- **GPT 模块**：自回归生成语义 token，负责韵律、语气、停顿
- **SoVITS 模块**：把语义 token 解码成声学特征，负责音色
- **参考音频**：推理时给 5~10 秒目标音色，模型对齐到它

两部分分开训练的意义：换音色只需要重训 SoVITS，
GPT 模块可以复用，所以小数据集也能出可用效果。

## 为什么 1 分钟就够
对比传统 TTS 要几小时录音：SoVITS 用 VAE 把音色和内容解耦，
音色只需要少量样本就能确定，剩下的靠 GPT 生成韵律。

## 中文效果好的原因
训练数据里中文占比高，且用了拼音 + 声调的先验。
日文需要额外的词典预处理。

## 推理
```bash
python inference_webui.py --port 7860
```
参考音频的选择比模型本身更影响效果 ——
和你要合成的语气接近的参考，出来才自然。

## 版本说明
v2Pro 是同人圈最常用的版本，角色模型生态成熟；
v3/v4 音质更好但生态还在积累。
"""),

    dict(owner="index-tts", name="index-tts", lang="Python",
         desc="Industrial-grade zero-shot TTS with emotion control and duration control, bilingual.",
         topics=["tts", "zero-shot", "voice-cloning", "emotion", "audio"],
         stars=6200, forks=680, license="Apache-2.0", risk="safe",
         quality=0.89, velocity=0.72, forgotten=0.42, freshness=0.76,
         created=400, pushed=1.8, ci=1, tests=1, size=21000,
         readme="""# IndexTTS

零样本 TTS，可控情感与时长。

## 三个核心能力
1. **零样本克隆** —— 给一段参考音频就能合成，不需要训练
2. **情感控制** —— 可以指定情感向量，或给一段"情绪参考音频"让它迁移
3. **时长控制** —— 指定每个字的时长，做配音对轴时必不可少

## 为什么时长控制重要
视频配音、有声书、动画对轴都需要"这句话必须在 2.3 秒内说完"。
传统 TTS 只能调语速，结果是要么快得听不清要么慢得拖沓。
IndexTTS 是在 token 级别控制时长，音质不受影响。

## 中文场景
基于 B 站开源数据训练，中文韵律自然度在开源 TTS 里属第一梯队。

## 部署
```bash
python server.py --port 7860
# 前端可以是 Gradio，也可以自己写 React 调 REST
```
"""),

    dict(owner="fish-audio", name="fish-speech", lang="Python",
         desc="Multilingual TTS with 13 languages, ~1 minute of audio for zero-shot voice cloning.",
         topics=["tts", "multilingual", "voice-cloning", "audio", "zero-shot"],
         stars=14800, forks=1900, license="CC-BY-NC-SA-4.0", risk="caution",
         quality=0.88, velocity=0.60, forgotten=0.22, freshness=0.68,
         created=560, pushed=2.2, ci=1, tests=1, size=18000,
         readme="""# Fish Speech

13 种语言的多语言 TTS。

## 特点
- 零样本克隆：15 秒参考音频即可
- 无明显语言标注需求：模型自己判断该说什么语言
- 流式推理支持，首包延迟 < 150ms

## 架构
基于 VQ-GAN + 自回归 transformer，把音频压缩成离散 token
再当语言模型来生成 —— 和 LLM 的范式统一，所以能吃到 LLM 的优化红利。

## 语言支持
中文、英文、日文、韩文、法文、德文、阿拉伯文等 13 种。
跨语言克隆效果不错：用中文样本可以合成英文，保留音色。

## ⚠️ 许可证
CC-BY-NC-SA-4.0 —— **禁止商业使用**。
商用需要单独授权，接入产品前务必确认。

```bash
python tools/api_server.py --listen 0.0.0.0:8080
```
"""),

    dict(owner="coqui-ai", name="TTS", lang="Python",
         desc="Deep learning toolkit for text-to-speech with pretrained models in 1100+ languages.",
         topics=["tts", "speech-synthesis", "deep-learning", "toolkit"],
         stars=36200, forks=9200, license="MPL-2.0", risk="caution",
         quality=0.87, velocity=0.10, forgotten=0.18, freshness=0.30,
         created=2100, pushed=780, ci=1, tests=1, size=42000,
         readme="""# Coqui TTS

文本转语音工具箱，1100+ 语言的预训练模型。

## 与克隆类工具的区别
这个是**训练框架**，不是一键克隆工具。
你要自己准备数据集、选架构（VITS / Tacotron / XTTS）、调参。

## XTTS
仓库里最值得关注的是 XTTS 部分：跨语言零样本克隆，
6 秒音频即可。但它在 2024 年后基本停止维护。

## ⚠️ 项目状态
**Coqui 公司已于 2024 年停止运营，仓库归档，不再维护。**
仍然有参考价值（架构设计、数据处理脚本质量高），
但不建议在新项目里直接依赖。社区有几个活跃的 fork。

```bash
tts --text "Hello" --model_name tts_models/en/ljspeech/vits
```
"""),

    dict(owner="microsoft-oss", name="vits-fast-fine-tuning", lang="Python",
         desc="Fine-tune VITS for few-shot voice cloning in 1 hour on a single GPU.",
         topics=["tts", "vits", "voice-cloning", "fine-tuning", "audio"],
         stars=4600, forks=920, license="Apache-2.0", risk="safe",
         quality=0.84, velocity=0.20, forgotten=0.62, freshness=0.35,
         created=980, pushed=420, ci=1, tests=1, size=8600,
         readme="""# VITS Fast Fine-tuning

1 小时、单卡，微调出可用的克隆音色。

## 适用场景
你有 5~20 分钟某人的清晰录音，想快速得到一个能用的 TTS 音色，
不追求极致音质，但要求今天就能出结果。

## 流程
1. 把音频切片并对齐文本（这一步最耗时，占 60% 时间）
2. 选底模：中文/日文/英文各有对应底模，选错会让咬字变味
3. 微调 100~300 步，loss 降到 30 左右即可停

## 与 GPT-SoVITS 的取舍
| | VITS-fast | GPT-SoVITS |
|---|---|---|
| 数据量 | 5~20 分钟 | 1 分钟起 |
| 训练时间 | 1 小时 | 1~3 小时 |
| 音质上限 | 中 | 高 |
| 韵律自然度 | 一般 | 好 |

数据干净的话 VITS-fast 够用；数据少或要韵律自然，选 SoVITS。

```bash
python finetune.py --config configs/zh.json --data_dir ./dataset
```
"""),

    dict(owner="so-vits-org", name="so-vits-svc", lang="Python",
         desc="SoftVC VITS Singing Voice Conversion: convert a song's vocals to another singer's timbre.",
         topics=["voice-conversion", "singing", "svc", "audio", "deep-learning"],
         stars=26400, forks=7600, license="AGPL-3.0", risk="caution",
         quality=0.86, velocity=0.18, forgotten=0.20, freshness=0.32,
         created=1500, pushed=560, ci=1, tests=1, size=24000,
         readme="""# so-vits-svc

歌声转换（SVC），把一首歌的人声换成另一个人的音色。

## 与 RVC 的关系
RVC 的检索机制（index）正是为了解决 so-vits-svc 的音色泄漏问题
才被提出的 —— 两者是同一问题的两代方案。

## 与 RVC 的取舍
| | so-vits-svc | RVC |
|---|---|---|
| 音色相似度 | 高 | 中高 |
| 咬字清晰度 | 一般 | 好 |
| 训练速度 | 慢 | 快 |
| 生态规模 | 大（旧） | 大（新） |
| 实时推理 | 不支持 | 支持 |

RVC 用 hubert 特征 + 检索，牺牲一点音色上限换来咬字和速度。

## ⚠️ 许可证
AGPL-3.0。接入网络服务时整个服务都要开源。

```bash
python inference_main.py -m logs/44k/model.pth -c config.json
```
"""),

    dict(owner="audio-tools-cn", name="audio-preprocess-kit", lang="Python",
         desc="人声素材预处理工具链：去混响、去底噪、切片对齐、削波检测，专为声音克隆训练准备。",
         topics=["audio", "preprocessing", "voice-cloning", "dataset", "rvc"],
         stars=420, forks=64, license="MIT", risk="safe",
         quality=0.83, velocity=0.34, forgotten=0.84, freshness=0.50,
         created=380, pushed=32, ci=1, tests=1, size=4200,
         readme="""# 音频预处理工具链

做声音克隆，80% 的失败原因在素材，不在模型。

## 为什么需要这个
社区里最常见的三个问题：
1. 训练完音色像"隔着电话"—— 素材有混响没处理掉
2. 咬字不清楚 —— 切片边缘切到了字中间
3. 有周期性噼啪声 —— 素材里有削波，训练时被放大了

## 四个环节
```bash
# ① 体检：先看素材到底有没有问题
audio-prep inspect ./raw/
#   输出：混响时间估计、信噪比、削波点位置、静音段占比

# ② 去混响 + 去噪
audio-prep clean ./raw/ -o ./clean/ --denoise --dereverb

# ③ 智能切片（按静音切，并对齐到过零点）
audio-prep slice ./clean/ -o ./sliced/ --min 2 --max 12

# ④ 终检：确认切片质量
audio-prep verify ./sliced/
```

## 建议标准
- 时长：RVC 建议 10~30 分钟，GPT-SoVITS 1 分钟起
- 采样率：克隆建议 48kHz（训练时再降采样）
- 信噪比：> 25dB
- 混响时间 RT60：< 0.3s

## ⚠️ 一个容易被忽略的点
**不要手动降噪过度**。激进的降噪会把齿音和高频泛音一起削掉，
训练出来的音色会"闷"。宁可留一点底噪。
"""),

    dict(owner="video-tools-cn", name="subtitle-align", lang="Python",
         desc="自动对齐字幕与音轨：识别说话人切换点，把错位的时间轴重新校准。",
         topics=["video", "subtitle", "audio", "cli"],
         stars=350, forks=42, license="MIT", risk="safe",
         quality=0.81, velocity=0.26, forgotten=0.82, freshness=0.36,
         created=430, pushed=68, ci=1, tests=1, size=2400,
         readme="""# subtitle-align

字幕整体偏移 1.2 秒，或者前面对得上后面越来越偏，这个能修。

## 两种情况分开处理
1. **整体偏移** —— 交叉相关找到最优偏移量，一步修好
2. **线性漂移**（不同帧率导致）—— 分段拟合，每 5 分钟一个锚点

## 说话人切换
用能量变化检测换人位置，字幕断句如果和换人点差太远会提示复核。

## 用法
```bash
subtitle-align video.mp4 subtitles.srt --detect-drift --out fixed.srt
```

不确定的段落会标 `[?]` 而不是硬改，你听一遍再确认。
"""),

    # ---------------- 硬件 / 嵌入式 / 边缘 ----------------
    dict(owner="raspberry-pi-oss", name="picoclaw", lang="C",
         desc="A tiny on-device assistant runtime for 64MB-class microcontrollers.",
         topics=["embedded", "edge", "tinyml", "microcontroller"],
         stars=210, forks=24, license="MIT", risk="safe",
         quality=0.79, velocity=0.22, forgotten=0.88, freshness=0.30,
         created=320, pushed=96, ci=1, tests=1, size=1100,
         readme="""# picoclaw

在 64MB 内存的 MCU 上跑一个能对话的助手。

## 约束
ESP32-S3 有 512KB SRAM + 8MB PSRAM。没有操作系统，没有文件系统，
没有 FPU 加速的矩阵乘法。

## 怎么做到
- 4-bit 权重量化 + int8 激活，手写 SIMD 内联汇编
- 状态机替代动态内存分配（全静态 buffer）
- 词表裁剪到 4K token（够用中文常用语 + 命令词）
- 语音前端用 MFCC，不带神经网络

## 实测
0.5B 参数的模型，ESP32-S3 上 8.2 tokens/s。
回答慢，但它是断网、离线、一根 USB 线供电就工作的。
"""),

    dict(owner="intel-oss-labs", name="oneapi-vulkan-bridge", lang="C++",
         desc="Bridge layer that lets oneAPI SYCL kernels run on Vulkan-only GPUs by lowering to SPIR-V.",
         topics=["gpu", "vulkan", "sycl", "intel", "compute"],
         stars=380, forks=44, license="Apache-2.0", risk="safe",
         quality=0.84, velocity=0.32, forgotten=0.80, freshness=0.46,
         created=460, pushed=36, ci=1, tests=1, size=6800,
         readme="""# oneAPI Vulkan Bridge

让 SYCL 内核在没有 oneAPI 运行时的机器上跑起来。

## 背景
Intel Arc 核显在 Windows 上跑 SYCL 需要完整的 oneAPI 运行时，
包括那个经常缺失、版本对不上的 `sycl8.dll`。装了半小时跑不起来很常见。

## 这个项目做什么
把 SYCL 编译产物的 SPIR-V 直接喂给 Vulkan driver，
跳过 oneAPI runtime 层。代价是丢掉了 USM 共享内存和一些高级特性，
换来"装了显卡驱动就能跑"。

## 支持
- 基础 parallel_for / local memory
- subgroup 操作
- 不支持：USM 指针、queue 的 dependency graph

## 用法
```bash
bridge-compile kernel.cpp --target vulkan -o kernel.spv
```
"""),

    dict(owner="edge-ai-cn", name="nn-on-mcu", lang="C",
         desc="手写神经网络推理库，面向 Cortex-M 与 RISC-V MCU，无操作系统依赖。",
         topics=["embedded", "tinyml", "cortex-m", "riscv"],
         stars=290, forks=36, license="MIT", risk="safe",
         quality=0.80, velocity=0.24, forgotten=0.86, freshness=0.33,
         created=410, pushed=78, ci=1, tests=1, size=2000,
         readme="""# nn-on-mcu

给 MCU 用的神经网络推理，纯 C99，全静态内存。

## 支持
- 层：Conv2D(3x3/1x1)、DepthwiseConv、FC、MaxPool、AvgPool、ReLU/ReLU6、
  Softmax、BatchNorm(融合进 Conv)
- 数据类型：int8 量化（per-tensor）+ int16 累加
- 硬件：Cortex-M4/M7（CMSIS-NN 可选后端）、RISC-V（自带 RVP 内联汇编）

## 内存
所有 buffer 在编译期由头文件里的形状声明计算出来，运行时零 malloc。
一个 20KB 参数的关键词识别模型，peak RAM 用到 34KB。

## 工具链
`nn-export` 吃 ONNX 或 TFLite，生成一个 .h + 一个 .c。
在 STM32F407 上跑 MNIST 是 0.8ms/帧。
"""),

    # ---------------- 各类"造轮子但造得好的" ----------------
    dict(owner="nano-lang", name="nanolang", lang="Rust",
         desc="A small language implementation with a real type system, written for people who want to read the whole compiler.",
         topics=["compiler", "language", "rust", "education"],
         stars=480, forks=52, license="MIT", risk="safe",
         quality=0.84, velocity=0.30, forgotten=0.79, freshness=0.48,
         created=590, pushed=42, ci=1, tests=1, size=5400,
         readme="""# nanolang

一个能读完的编译器。

## 规模
词法 300 行、语法 800 行、类型推导 1200 行、代码生成 900 行。
总共不到 4000 行，每个文件都能一口气读完。

## 不是玩具的地方
- Hindley-Milner 类型推导，带 let 泛化
- 真正的错误信息：类型不匹配时打印推导路径
- 编译到 WASM，可以直接在浏览器里试

## 示例
```nano
let id = fn(x) => x
let compose = fn(f, g) => fn(x) => f(g(x))
let add2 = fn(x) => x + 2
let r = compose(add2, id)(3)   // 5
```

## 读法建议
从 `main.rs` 往下走，每个阶段都有 `#[test]` 演示输入输出。
"""),

    dict(owner="sqlite-tools", name="litefs", lang="Go",
         desc="A distributed filesystem for SQLite that gives you read replicas without changing your app.",
         topics=["sqlite", "replication", "golang", "database"],
         stars=4300, forks=190, license="Apache-2.0", risk="safe",
         quality=0.86, velocity=0.34, forgotten=0.36, freshness=0.60,
         created=1100, pushed=40, ci=1, tests=1, size=7200,
         readme="""# LiteFS

给 SQLite 加只读副本，应用代码一行不用改。

## 怎么工作
一个 FUSE 文件系统拦截 SQLite 的写操作，把 WAL 帧发到副本节点。
副本重放这些帧，于是得到一个几乎实时的只读副本。

## 拓扑
- 单节点写：只有一个节点能写，避免冲突
- 多节点读：任意节点都能读，读的是本地文件，没有网络往返
- 故障转移：写节点挂了自动选举新写节点

## 配置
```hcl
fuse {
  dir = "/litefs"
}
exec {
  cmd = "/app/server"
}
```
"""),

    dict(owner="openobserve-cn", name="o2", lang="Rust",
         desc="Log search engine with sub-second queries over billions of rows, no indexing step required.",
         topics=["observability", "logging", "search", "rust"],
         stars=1200, forks=96, license="Apache-2.0", risk="safe",
         quality=0.83, velocity=0.44, forgotten=0.62, freshness=0.58,
         created=620, pushed=14, ci=1, tests=1, size=16000,
         readme="""# O2

日志搜索，不建索引也能秒级查。

## 与传统方案的区别
Elasticsearch 要先建索引，写入放大的同时占用大量磁盘。
O2 把日志按列存进对象存储，查询时只读需要的列做向量化扫描。

## 结果
- 写入：直写对象存储，无索引开销
- 查询：10 亿行日志，过滤+聚合 0.8 秒
- 存储：相比 ES 省 70%（列压缩 + 不存倒排）

## 用法
```bash
o2 ingest --parquet --dir logs/ --bucket s3://logs
o2 query "SELECT level, count(*) FROM logs WHERE ts:'1h' GROUP BY level"
```
"""),

    dict(owner="mach-port", name="zig-http", lang="Zig",
         desc="An HTTP/1.1 and HTTP/2 server in Zig with no allocator surprises and explicit buffer ownership.",
         topics=["http", "zig", "server", "networking"],
         stars=520, forks=39, license="MIT", risk="safe",
         quality=0.81, velocity=0.28, forgotten=0.83, freshness=0.40,
         created=300, pushed=50, ci=1, tests=1, size=2600,
         readme="""# zig-http

Zig 写的 HTTP 服务器，内存行为完全可预测。

## 设计取舍
不用全局分配器，不用隐式扩容。每个连接的 buffer 在 accept 时就申请好，
生命周期由连接状态机决定，出了作用域一定释放。

## 支持
- HTTP/1.1 keep-alive、chunked
- HTTP/2 多路复用（HPACK 完整实现）
- TLS（有 BearSSL 和 std.crypto 两种后端）

## 性能
单核 1M req/s 空响应，16KB 响应 82K req/s。
比 Go net/http 快 3 倍左右，代价是要自己管内存。

```zig
var server = try Server.init(alloc, .{ .port = 8080 });
try server.route("/", handler);
```
"""),

    dict(owner="css-anim-cn", name="motion-in-css", lang="CSS",
         desc="A curated collection of 120 pure-CSS animations with no JavaScript and careful reduced-motion fallbacks.",
         topics=["css", "animation", "frontend", "collection"],
         stars=940, forks=110, license="MIT", risk="safe",
         quality=0.82, velocity=0.26, forgotten=0.75, freshness=0.50,
         created=380, pushed=28, ci=1, tests=0, size=1200,
         readme="""# motion-in-css

120 个纯 CSS 动画，复制粘贴就能用。

## 收录标准
- 零 JavaScript
- 只用 transform / opacity 做动画（不触发重排）
- 全部带 `@media (prefers-reduced-motion)` 降级
- 每个都有 HTML + 效果预览 + 性能标注

## 分类
加载态（24）、过渡（31）、悬停反馈（28）、滚动驱动（18）、装饰（19）

## 特别收录
用 `animation-timeline: scroll()` 实现的滚动驱动动画 ——
以前这必须用 JS 监听 scroll 事件，现在纯 CSS 就行，
主线程完全不被阻塞。
"""),

    dict(owner="docs-as-code", name="arch-diagram-cli", lang="TypeScript",
         desc="Generate architecture diagrams from a plain text DSL, with diffable output and drift detection.",
         topics=["documentation", "diagram", "cli", "architecture"],
         stars=710, forks=68, license="MIT", risk="safe",
         quality=0.83, velocity=0.32, forgotten=0.77, freshness=0.47,
         created=440, pushed=33, ci=1, tests=1, size=3100,
         readme="""# arch-diagram-cli

架构图用文本写，能 diff，能检测和真实代码的漂移。

## 为什么要文本
图片格式的架构图，改一次要打开画图工具，review 时看不出改了什么，
而且三个月后一定和代码不一致。

## 功能
```
service api {
  port 8080
  depends postgres, redis
}
service worker {
  depends postgres, queue
}
```
- `arch render --out arch.svg`
- `arch diff v1.arch v2.arch` —— 文本 diff
- `arch check --source ./src` —— 扫描代码里的实际依赖，标出图里没画的

## 输出
SVG / PNG / Mermaid / PlantUML，可以塞进 CI 里，架构改动自动生成对比图。
"""),
]


def seed(reset: bool = False) -> None:
    if reset:
        from core.config import DB_PATH
        for suffix in ("", "-wal", "-shm"):
            p = str(DB_PATH) + suffix
            if Path(p).exists():
                Path(p).unlink()
        print("🗑️  已清空旧数据库")

    init_db()
    init_jieba()
    rng = random.Random(42)

    with get_conn() as conn:
        # 测试用户
        conn.execute(
            """INSERT INTO users (id, username, password_hash, display_name,
                                  account_score, is_new_user, last_active_at)
               VALUES (1, 'demo', 'x', '演示用户', 1.0, 1, datetime('now'))
               ON CONFLICT(id) DO NOTHING"""
        )

        inserted = 0
        for s in SEEDS:
            full_name = f"{s['owner']}/{s['name']}"

            # ⭐ 标签必须在这里提取并缓存。
            #    召回/排序/搜索干预全部依赖 tags_json，
            #    不填的话整条推荐链路会静默退化成"只有热门榜"。
            tags = extract_readme_tags(s["readme"], topk=50)

            cur = conn.execute(
                """INSERT INTO repos (
                       owner, name, full_name, description, readme_md, readme_len,
                       language, topics, license_spdx, license_risk,
                       stars, forks, watchers, open_issues, contributors,
                       has_ci, has_tests, size_kb,
                       created_at_gh, pushed_at_gh,
                       quality_score, velocity_score, forgotten_score, freshness_score,
                       tags_json, tags_updated_at,
                       first_seen_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(full_name) DO UPDATE SET
                       stars           = excluded.stars,
                       velocity_score  = excluded.velocity_score,
                       forgotten_score = excluded.forgotten_score,
                       quality_score   = excluded.quality_score,
                       readme_md       = excluded.readme_md,
                       tags_json       = excluded.tags_json,
                       tags_updated_at = excluded.tags_updated_at
                """,
                (
                    s["owner"], s["name"], full_name, s["desc"], s["readme"],
                    len(s["readme"]),
                    s["lang"], json.dumps(s["topics"]), s["license"], s["risk"],
                    s["stars"], s["forks"], int(s["stars"] * 0.03),
                    int(s["stars"] * 0.02), rng.randint(2, 40),
                    s["ci"], s["tests"], s["size"],
                    _days_ago(s["created"]), _days_ago(s["pushed"]),
                    s["quality"], s["velocity"], s["forgotten"], s["freshness"],
                    json.dumps(tags, ensure_ascii=False),
                    _days_ago(0),
                    _days_ago(s["created"]),
                ),
            )
            if cur.rowcount:
                inserted += 1

            row = conn.execute(
                "SELECT id FROM repos WHERE full_name = ?", (full_name,)
            ).fetchone()
            if row:
                ensure_pool(conn, row["id"])

        total = conn.execute("SELECT COUNT(*) AS c FROM repos").fetchone()["c"]
        pools = conn.execute("SELECT COUNT(*) AS c FROM repo_pools").fetchone()["c"]

    print(f"✅ 种子数据就绪：repos={total}  repo_pools={pools}  本次写入={inserted}")

    # 分类统计，直观确认三类样本都进去了
    forgotten = [s for s in SEEDS if s["forgotten"] >= 0.7]
    fresh = [s for s in SEEDS if s["freshness"] >= 0.9 and s["stars"] < 200]
    print(f"   ⭐ 遗珠仓库（forgotten≥0.7）: {len(forgotten)} 个")
    print(f"   🌱 新鲜仓库（freshness≥0.9 且 stars<200）: {len(fresh)} 个")
    print(f"   🔥 其余热门/常规仓库: {len(SEEDS) - len(forgotten) - len(fresh)} 个")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="灌入 RecoFeed 种子数据")
    ap.add_argument("--reset", action="store_true", help="先删库再灌")
    args = ap.parse_args()
    seed(reset=args.reset)
