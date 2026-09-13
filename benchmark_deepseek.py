#!/usr/bin/env python3
"""一次性流式基准测试：测量 DeepSeek 首 token 时延(TTFT) 与 生成速度(tokens/s)。
仅用于本次性能验证，不写入任何业务代码。
"""
import time, json, sys
import urllib.request

API_KEY = "sk-e41e65b207fe40e6a2073423aa0a87c3"
URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"  # DeepSeek-V4.1-Flash 在 DeepSeek API 的 slug

PROMPT = (
    "你是一名资深的 GitHub 仓库翻译专家。请将下面这段英文仓库描述翻译成中文，"
    "并用 3 到 5 句话详细总结这个仓库的核心用途、技术特点与适用场景。"
    "要求译文准确、通顺、专业。\n\n"
    "仓库名称：'langchain'\n"
    "描述：'LangChain is a framework for developing applications powered by large language models (LLMs). "
    "It provides a standard interface for chains, agents, and retrieval strategies, and integrates with "
    "hundreds of providers including OpenAI, Anthropic, Hugging Face, and local models. "
    "LangChain simplifies the construction of context-aware, reasoning LLM applications, "
    "supporting memory, tool calling, and document indexing out of the box.'"
)

payload = {
    "model": MODEL,
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": PROMPT},
    ],
    "stream": True,
    "max_tokens": 600,
    "temperature": 0.3,
    "thinking": {"type": "disabled"},
}


def main():
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
    prompt_tokens = None
    chunks = 0
    text_parts = []

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            status = resp.status
            if status != 200:
                body = resp.read().decode("utf-8", "replace")
                print(f"HTTP {status}: {body}")
                return
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line or not line.startswith("data:"):
                    continue
                payload_str = line[len("data:"):].strip()
                if payload_str == "[DONE]":
                    break
                try:
                    obj = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                # usage 通常在最后一个 chunk
                if obj.get("usage"):
                    completion_tokens = obj["usage"].get("completion_tokens")
                    prompt_tokens = obj["usage"].get("prompt_tokens")
                choices = obj.get("choices") or []
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        chunks += 1
                        now = time.perf_counter()
                        if t_first is None:
                            t_first = now
                        t_last = now
                        text_parts.append(content)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", "replace")
        print(f"HTTPError {e.code}: {err}")
        return
    except Exception as e:
        print(f"ERROR: {e}")
        return

    t_end = time.perf_counter()
    total_elapsed = t_end - t_start
    ttft = (t_first - t_start) if t_first else None
    gen_duration = (t_last - t_first) if (t_first and t_last) else None

    print("=" * 60)
    print(f"模型: {MODEL} (DeepSeek-V4.1-Flash)")
    print(f"HTTP 状态: 200")
    print("-" * 60)
    if ttft is not None:
        print(f"首 token 时延 (TTFT):   {ttft*1000:.0f} ms  ({ttft:.3f} s)")
    else:
        print("首 token 时延 (TTFT):   N/A (无内容返回)")
    if completion_tokens:
        print(f"Completion tokens:     {completion_tokens}")
    else:
        print(f"Completion chunks:     {chunks} (usage 未返回, 估算)")
    if prompt_tokens:
        print(f"Prompt tokens:         {prompt_tokens}")
    print(f"总耗时:                {total_elapsed:.3f} s")
    if completion_tokens and gen_duration:
        print(f"生成吞吐量:            {completion_tokens/gen_duration:.1f} tokens/s (生成区间)")
        print(f"端到端吞吐量:          {completion_tokens/total_elapsed:.1f} tokens/s (含TTFT)")
    if text_parts:
        full = "".join(text_parts)
        print("-" * 60)
        print(f"译文预览 (前 160 字):\n{full[:160]}")
    print("=" * 60)


if __name__ == "__main__":
    main()
