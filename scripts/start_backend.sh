#!/usr/bin/env bash
# RecoFeed 后端一键启动（本地开发用）
#
# 用法：
#   bash scripts/start_backend.sh          # 启动（自动装依赖 + 灌种子数据）
#   bash scripts/start_backend.sh --fresh  # 清库重灌再启动
#
# 启动后：
#   API      http://127.0.0.1:8000
#   接口文档  http://127.0.0.1:8000/docs
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
cd "$BACKEND"

echo "==> 检查依赖"
python3 -c "import fastapi, uvicorn, jieba, pydantic" 2>/dev/null || {
    echo "    安装依赖中..."
    pip3 install -q -r requirements.txt
}

if [[ "${1:-}" == "--fresh" ]]; then
    echo "==> 清库重灌种子数据"
    python3 jobs/seed_data.py --reset
else
    echo "==> 确保种子数据存在（幂等）"
    python3 jobs/seed_data.py
fi

echo "==> 启动后端 → http://127.0.0.1:8000"
exec python3 app.py
