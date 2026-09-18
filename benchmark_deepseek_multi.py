#!/usr/bin/env python3
"""DeepSeek 多次流式基准测试，输出每次 TTFT / tokens/s 及汇总均值。"""
import time, json
import urllib.request

# ⚠️ 密钥绝不硬编码：优先读环境变量，其次读 backend/core/llm_local.json（已 gitignore）。
#    2026-09-18 教训：本脚本曾在 9/12 把真实的 DeepSeek key 提交进 git，
#    9/15 仓库转公开后被 GitGuardian 扫到并告警（key 已在公网 3 天）——
#    唯一有效的补救是轮换密钥，删文件/改历史都不够（历史里的对象 GitHub 不回收）。
def _load_api_key() -> str:
    import json as _json
    import os
    import pathlib as _pathlib
    env = os.getenv("DEEPSEEK_API_KEY")
    if env:
        return env.strip()
    cfg = _pathlib.Path(__file__).resolve().parent / "backend" / "core" / "llm_local.json"
    if cfg.exists():
        try:
            data = _json.loads(cfg.read_text(encoding="utf-8"))
            key = (data.get("deepseek_api_key") or "").strip()
            if key:
                return key
        except Exception:
            pass
    raise SystemExit(
        "未找到 DeepSeek API Key。请设置环境变量 DEEPSEEK_API_KEY，"
        "或填 backend/core/llm_local.json 的 deepseek_api_key 字段（该文件已被 gitignore）。"
    )


API_KEY = _load_api_key()
URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"

PROMPTS = [
    "将以下 GitHub 仓库描述翻译成中文，并用 3 句话总结用途：'vite - Next generation frontend tooling. "
    "It is a build tool that aims to provide a faster and leaner development experience for modern web projects, "
    "with native ES modules, lightning-fast HMR, and optimized production builds.'",
    "将以下 GitHub 仓库描述翻译成中文，并用 3 句话总结用途：'transformers - State-of-the-art Machine Learning "
    "for PyTorch, JAX, and TensorFlow. A library of pretrained models for natural language processing, "
    "computer vision, and audio, with thousands of community checkpoints.'",
    "将以下 GitHub 仓库描述翻译成中文，并用 3 句话总结用途：'redis - Redis is an in-memory database that "
    "persists on disk. It supports various data structures such as strings, hashes, lists, sets, and streams, "
    "and is often used as a cache, message broker, or primary database.'",
]


def one_call(prompt: str):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
        "max_tokens": 400,
        "temperature": 0.3,
        "thinking": {"type": "disabled"},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(URL, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {API_KEY}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")
    req.add_header("Accept-Encoding", "identity")

    t_start = time.perf_counter()
    t_first = None
    t_last = None
    completion_tokens = None
    chunks = 0
    with urllib.request.urlopen(req, timeout=120) as resp:
        if resp.status != 200:
            return {"error": resp.read().decode("utf-8", "replace")}
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            ps = line[len("data:"):].strip()
            if ps == "[DONE]":
                break
            try:
                obj = json.loads(ps)
            except json.JSONDecodeError:
                continue
            if obj.get("usage"):
                completion_tokens = obj["usage"].get("completion_tokens")
            c = (obj.get("choices") or [{}])[0].get("delta", {}).get("content")
            if c:
                chunks += 1
                now = time.perf_counter()
                if t_first is None:
                    t_first = now
                t_last = now
    t_end = time.perf_counter()
    ttft = (t_first - t_start) if t_first else None
    gen_dur = (t_last - t_first) if (t_first and t_last) else None
    toks = completion_tokens or chunks
    return {
        "ttft_ms": ttft * 1000 if ttft else None,
        "tokens": toks,
        "total_s": t_end - t_start,
        "gen_tps": toks / gen_dur if gen_dur else None,
        "e2e_tps": toks / (t_end - t_start) if (t_end - t_start) else None,
    }


def main():
    results = []
    for i, p in enumerate(PROMPTS):
        r = one_call(p)
        results.append(r)
        if "error" in r:
            print(f"第 {i+1} 次: 错误 -> {r['error']}")
            continue
        print(f"第 {i+1} 次: TTFT={r['ttft_ms']:.0f}ms | tokens={r['tokens']} | "
              f"生成={r['gen_tps']:.1f} tok/s | 端到端={r['e2e_tps']:.1f} tok/s | 总耗时={r['total_s']:.2f}s")
        time.sleep(1.5)
    ok = [r for r in results if "error" not in r]
    if ok:
        n = len(ok)
        avg_ttft = sum(r["ttft_ms"] for r in ok) / n
        avg_gen = sum(r["gen_tps"] for r in ok) / n
        avg_e2e = sum(r["e2e_tps"] for r in ok) / n
        print("-" * 60)
        print(f"汇总 ({n} 次成功):")
        print(f"  平均 TTFT:        {avg_ttft:.0f} ms")
        print(f"  平均生成速度:     {avg_gen:.1f} tokens/s")
        print(f"  平均端到端速度:   {avg_e2e:.1f} tokens/s")


if __name__ == "__main__":
    main()
