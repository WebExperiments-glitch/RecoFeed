#!/usr/bin/env python3
"""提交/开源前的密钥扫描 —— 工作区 + 全部 git 历史。

为什么要有这个脚本（2026-09-18 事故复盘）
    开源前我用一行 shell 做过扫描：
        git log --all -p | grep -oE "sk-…|ghp_…" | sort -u | head -10
    结果是**假阴性**：输出里先有 10 个 40 位 commit SHA（`sort -u` 排序后 hex 在前），
    真实密钥排在后面被 `head -10` 截断丢掉 → 我误判"历史干净"。
    两天后 GitGuardian 发来告警：benchmark_deepseek.py 里硬编码的 DeepSeek key
    已随公开仓库暴露在公网 3 天。

本脚本的三条纪律（针对那次事故）
    ① **绝不截断输出** —— 命中全部落盘到 reports/secret-scan.txt，终端只做摘要
    ② **扫内容不只扫文件名** —— 那次漏掉的正是"文件名不含 token/secret 字样"
    ③ **工作区与历史都要扫** —— 只扫工作区会漏掉"已删除但仍在历史里"的密钥

用法
    python scripts/check_secrets.py            # 扫工作区 + 历史
    python scripts/check_secrets.py --worktree # 只扫工作区（快，适合提交前）
    python scripts/check_secrets.py --history  # 只扫历史（慢，适合开源前）
退出码：0 = 干净，1 = 发现可疑密钥。
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "reports" / "secret-scan.txt"

# 只匹配"看起来像真 key"的形态，避免把文档里的占位符（sk-…）当命中
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("OpenRouter key", re.compile(r"sk-or-v1-[A-Za-z0-9]{32,}")),
    ("DeepSeek/OpenAI 风格 key", re.compile(r"sk-[a-z0-9]{24,}")),
    ("GitHub PAT (classic)", re.compile(r"ghp_[A-Za-z0-9]{30,}")),
    ("GitHub PAT (fine-grained)", re.compile(r"github_pat_[A-Za-z0-9_]{30,}")),
    ("GitHub OAuth token", re.compile(r"gho_[A-Za-z0-9]{30,}")),
    ("GitHub App token", re.compile(r"ghs_[A-Za-z0-9]{30,}")),
    ("AWS Access Key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("私钥文件头", re.compile(r"-----BEGIN (RSA|EC|OPENSSH|PGP) PRIVATE KEY-----")),
    ("硬编码赋值", re.compile(
        r"""(?:api[_-]?key|apikey|secret|passwd|password|access[_-]?token)"""
        r"""\s*[:=]\s*["'][A-Za-z0-9_\-]{16,}["']""", re.I)),
]

# 明确允许的占位/示例（避免误报干扰判断）
ALLOWLIST = re.compile(
    r"sk-\s*[.…]|sk-or-v1-\s*[.…]|your[_-]?api[_-]?key|YOUR_KEY|xxx+|placeholder|<[^>]+>",
    re.I,
)

SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pyc", ".woff",
               ".woff2", ".ttf", ".zip", ".gz", ".mp4", ".mp3", ".pdf"}
SKIP_PATH = ("frontend/node_modules/", "backend/.venv/", ".git/", "reports/")
MAX_BLOB_BYTES = 3 * 1024 * 1024      # 大文件（数据集等）跳过，避免扫描卡死


def _hits(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in text.splitlines():
        if ALLOWLIST.search(line):
            continue
        for name, pat in PATTERNS:
            m = pat.search(line)
            if m:
                out.append((name, m.group(0)))
                break
    return out


def scan_worktree() -> list[str]:
    """扫被 git 跟踪的文件内容（不扫未跟踪文件 —— 它们不会进仓库）。"""
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace").stdout.split()
    findings: list[str] = []
    for rel in files:
        if any(rel.startswith(p) or p in rel for p in SKIP_PATH):
            continue
        if Path(rel).suffix.lower() in SKIP_SUFFIX:
            continue
        p = ROOT / rel
        try:
            if p.stat().st_size > MAX_BLOB_BYTES:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, token in _hits(text):
            findings.append(f"[工作区] {rel}  → {name}: {token[:12]}…")
    return findings


def scan_history() -> list[str]:
    """扫**全部提交的全部文件内容**。用 git grep 跨历史检索（快且完整）。"""
    findings: list[str] = []
    try:
        revs = subprocess.run(["git", "rev-list", "--all"], cwd=ROOT,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout.split()
    except OSError:
        return findings
    if not revs:
        return findings

    # 逐条正则跨全部历史检索；**输出不做任何截断**
    for name, pat in PATTERNS:
        try:
            r = subprocess.run(
                ["git", "grep", "-n", "-I", "-E", pat.pattern, *revs, "--", "."],
                cwd=ROOT, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
        except OSError:
            continue
        for line in (r.stdout or "").splitlines():
            if not line.strip() or ALLOWLIST.search(line):
                continue
            # 形如：<sha>:<path>:<lineno>:<content>
            parts = line.split(":", 3)
            if len(parts) < 4:
                continue
            sha, path, lineno, content = parts
            findings.append(f"[历史] {sha[:9]} {path}:{lineno} → {name}: {content.strip()[:80]}")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description="提交/开源前密钥扫描")
    ap.add_argument("--worktree", action="store_true", help="只扫工作区")
    ap.add_argument("--history", action="store_true", help="只扫历史")
    args = ap.parse_args()
    do_wt = args.worktree or not (args.worktree or args.history)
    do_hi = args.history or not (args.worktree or args.history)

    print("密钥扫描中（工作区%s / 历史%s）…" % ("✓" if do_wt else "—",
                                                 "✓" if do_hi else "—"))
    findings: list[str] = []
    if do_wt:
        findings += scan_worktree()
    if do_hi:
        findings += scan_history()

    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text("\n".join(findings) + ("\n" if findings else ""), encoding="utf-8")

    if findings:
        print(f"\n🚨 发现 {len(findings)} 处可疑密钥（完整清单：{REPORT.relative_to(ROOT)}）")
        print("   ⚠️ 输出不截断是刻意设计：截断曾导致真实泄露被漏判\n")
        for f in findings:          # 全部打印，不做 head/切片
            print("   " + f)
        print("\n处置顺序建议：")
        print("   ① 立刻在服务商控制台**轮换/吊销**该密钥（删文件、改历史都不够，")
        print("      因为历史对象 GitHub 不回收，且可能已被第三方抓取）")
        print("   ② 把代码改成从环境变量或被 gitignore 的本地配置读取")
        print("   ③ 视需要改写历史（git filter-repo）并强推")
        return 1

    print("\n✓ 未发现可疑密钥（工作区%s / 历史%s）" %
          (" 已扫" if do_wt else " 未扫", " 已扫" if do_hi else " 未扫"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
