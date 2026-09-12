"""标签提取的数值验证测试。

⚠️ 本项目的历史教训：算法改动必须附带数值验证。
   见 docs/算法实现规格.md 第零章（4 个实测发现的缺陷）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tags.extractor import (  # noqa: E402
    clean_readme,
    extract_query_tags,
    extract_readme_tags,
    merge_layers,
    normalize,
)

README = """
# 高性能本地大模型推理框架

[![Build](https://img.shields.io/badge/build-passing-green)](https://ci.example.com)

本项目是一个基于 llama.cpp 的本地 AI 多模型推理服务，支持 GGUF 量化模型加载。
使用 Vulkan 后端在 Intel Arc GPU 上加速推理，相比 CPU 推理速度提升 300%。
支持多模型路由、上下文缓存、流式输出。适用于本地部署和隐私敏感场景。

## 快速开始

```bash
pip install recofeed
python -m recofeed --model gguf
```

## 特性

- 向量数据库集成
- 微服务架构设计
- 单元测试覆盖率 90%
"""


def test_llama_cpp_not_split() -> None:
    """⭐ 核心回归测试：llama.cpp 必须被识别为单个词。

    实测背景：加载自定义词典前，jieba 会输出 llama + cpp 两个词。
    """
    tags = extract_readme_tags(README)
    assert "llama.cpp" in tags, f"llama.cpp 未被正确识别，实际标签: {list(tags)[:10]}"
    assert "llama" not in tags or tags.get("llama", 0) < tags["llama.cpp"], \
        "llama 不应独立出现或权重不应高于 llama.cpp"


def test_chinese_tech_terms_recognized() -> None:
    """中文技术术语应被识别为完整词。"""
    tags = extract_readme_tags(README)
    for term in ("向量数据库", "微服务", "单元测试"):
        assert term in tags, f"未识别技术术语: {term}"


def test_code_block_removed() -> None:
    """代码块内容不应进入标签（避免 pip/分页等噪声）。"""
    clean = clean_readme(README)
    assert "pip install" not in clean
    assert "llama.cpp" in clean


def test_stopwords_filtered() -> None:
    """中文停用词应被过滤。"""
    tags = extract_readme_tags(README)
    for sw in ("的", "可以", "使用", "本项目"):
        assert sw not in tags, f"停用词未被过滤: {sw}"


def test_normalize_bounds() -> None:
    """归一化后应在 0~1，最大值为 1。"""
    raw = {"a": 9.0, "b": 4.5, "c": 0.9}
    n = normalize(raw, floor=0.0)
    assert n["a"] == 1.0
    assert n["b"] == 0.5
    assert all(0 <= v <= 1 for v in n.values())


def test_normalize_long_readme_not_dominant() -> None:
    """⭐ 归一化应消除 README 长度差异带来的分数膨胀。"""
    short = {"python": 0.5, "web": 0.3}
    long_ = {"python": 0.9, "web": 0.8, "ml": 0.7}
    ns, nl = normalize(short, 0.0), normalize(long_, 0.0)
    # 两者最高分都应是 1.0，即"相对重要性"可比
    assert ns["python"] == 1.0
    assert nl["python"] == 1.0


def test_merge_layers_stacking() -> None:
    """⭐ 同一标签出现在多层时应被叠加强化。"""
    readme = {"推理": 0.7, "模型": 0.6}
    footprint = {"推理": 0.8, "gguf": 0.7}
    issue = {"vulkan": 0.9}
    merged = merge_layers(readme, footprint, issue)

    # 推理出现在 readme(1.0) 和 footprint(0.6)，应高于只在 readme 的 模型
    assert merged["推理"] > merged.get("模型", 0), \
        "跨层叠加未能强化标签"


def test_layer_weights_correct_order() -> None:
    """验证三层权重系数生效：readme > footprint > issue。"""
    merged = merge_layers(
        readme_tags={"aaa": 1.0},
        footprint_tags={"bbb": 1.0},
        issue_tags={"ccc": 1.0},
    )
    assert merged["aaa"] > merged["bbb"] > merged["ccc"], \
        f"层权重顺序错误: {merged}"


def test_query_tags_short_input() -> None:
    """短搜索词应能提取标签（降级到分词）。"""
    tags = extract_query_tags("llm inference")
    assert tags, "短查询词提取失败"
    assert "llm" in tags or "inference" in tags


def test_query_tags_empty() -> None:
    assert extract_query_tags("") == {}
    assert extract_query_tags("   ") == {}


if __name__ == "__main__":
    import traceback

    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✅ {name}")
                passed += 1
            except AssertionError as e:
                print(f"  ❌ {name}: {e}")
                failed += 1
            except Exception:
                print(f"  💥 {name}:")
                traceback.print_exc()
                failed += 1
    print(f"\n通过 {passed} / 失败 {failed}")
    sys.exit(1 if failed else 0)
