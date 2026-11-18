#!/usr/bin/env python3
"""RecoFeed 质量闸门 —— 本地与 CI 共用同一套命令。

用法：
    python scripts/ci/gate.py backend     # ruff + pytest
    python scripts/ci/gate.py frontend    # pnpm install / typecheck / build
    python scripts/ci/gate.py security    # 密钥扫描 + pip/pnpm 依赖审计
    python scripts/ci/gate.py data        # 数据集快照完整性
    python scripts/ci/gate.py all         # 串行跑全部闸门（CI 最终闸门）

CI 的调度方式：
    paths-filter 判断改了哪块 → 只跑对应闸门 → 全部通过后再跑 all。
    所以"改后端只跑后端闸门、改前端只跑前端闸门"在本地也能 1:1 复现。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
SEED_DB = ROOT / "backend" / "data" / "recofeed.seed.db"
NPM_AUDIT_REGISTRY = "https://registry.npmjs.org"


class GateError(RuntimeError):
    """闸门失败。"""


def _run(cmd: list[str], cwd: Path | None = None,
         *, capture: bool = False) -> subprocess.CompletedProcess:
    cwd = cwd or ROOT
    where = cwd.relative_to(ROOT) if cwd != ROOT else Path(".")
    print(f"\n▶ {' '.join(cmd)}   (cwd={where})", flush=True)
    # Windows 上 pnpm / npm 是 .cmd 垫片，subprocess 直接执行会 FileNotFoundError，
    # 必须交给 shell 解析；其他命令仍走 list 形式，避免转义问题。
    shell = os.name == "nt"
    proc = subprocess.run(
        subprocess.list2cmdline(cmd) if shell else cmd,
        cwd=cwd, text=True, capture_output=capture, shell=shell,
    )
    if capture:
        out = (proc.stdout or "") + (proc.stderr or "")
        if out.strip():
            print(out.strip())
    if proc.returncode != 0:
        raise GateError(f"命令失败（exit {proc.returncode}）：{' '.join(cmd)}")
    return proc


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise GateError(f"缺少命令：{name}")


def _require_module(name: str, hint: str) -> None:
    proc = subprocess.run([sys.executable, "-c", f"import {name}"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise GateError(f"缺少 Python 模块：{name}（{hint}）")


# ------------------------------------------------------------------ 分区闸门

def gate_backend() -> None:
    """后端：静态检查 + 全部单测/集成测试。"""
    _require_module("ruff", "pip install -r backend/requirements-dev.txt")
    _require_module("pytest", "pip install -r backend/requirements-dev.txt")
    _run([sys.executable, "-m", "ruff", "check", "backend", "scripts"])
    _run([sys.executable, "-m", "pytest", "backend", "-q"])


def gate_frontend() -> None:
    """前端：锁文件安装 → 类型检查 → 生产构建。"""
    _require_tool("pnpm")
    _run(["pnpm", "install", "--frozen-lockfile"], cwd=FRONTEND)
    _run(["pnpm", "typecheck"], cwd=FRONTEND)
    _run(["pnpm", "build"], cwd=FRONTEND)


def gate_security() -> None:
    """安全：密钥扫描（工作区 + 全部历史）+ 依赖漏洞审计。"""
    _run([sys.executable, "scripts/check_secrets.py"])

    _require_module("pip_audit", "pip install -r backend/requirements-dev.txt")
    _run([sys.executable, "-m", "pip_audit",
          "-r", "backend/requirements.txt"])

    if os.getenv("RECOFEED_SKIP_AUDIT") == "1":
        print("⚠ RECOFEED_SKIP_AUDIT=1，跳过 npm 依赖审计")
    else:
        _require_tool("pnpm")
        # 只审计生产依赖：tailwind 等构建期传递依赖的告警不阻塞发版，
        # 但仍会在全量闸门里由 Dependabot 跟踪升级。
        _run(["pnpm", "audit", "--prod",
              "--registry", NPM_AUDIT_REGISTRY,
              "--audit-level", "high"], cwd=FRONTEND)


def gate_data() -> None:
    """数据：快照完整性 + 确认运行库没有被误提交。"""
    _require_module("pytest", "pip install -r backend/requirements-dev.txt")
    _run([sys.executable, "-m", "pytest",
          "backend/tests/test_dataset.py", "-q"])

    tracked = subprocess.run(
        ["git", "ls-files", "backend/data"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if tracked.returncode != 0:
        raise GateError("git ls-files 执行失败（data 闸门需要 git 仓库）")
    files = [line.strip() for line in tracked.stdout.splitlines() if line.strip()]
    unexpected = [f for f in files if f != "backend/data/recofeed.seed.db"]
    if unexpected:
        raise GateError(f"backend/data 下只能提交脱敏快照，发现：{unexpected}")


GATES = {
    "backend": gate_backend,
    "frontend": gate_frontend,
    "security": gate_security,
    "data": gate_data,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="RecoFeed 质量闸门")
    ap.add_argument("area", choices=[*GATES, "all"])
    args = ap.parse_args()

    try:
        if args.area == "all":
            for name, fn in GATES.items():
                print(f"\n{'=' * 64}\n闸门：{name}\n{'=' * 64}", flush=True)
                fn()
            print("\n✅ 全量闸门通过")
        else:
            GATES[args.area]()
            print(f"\n✅ {args.area} 闸门通过")
    except GateError as exc:
        print(f"\n❌ 闸门未通过：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
