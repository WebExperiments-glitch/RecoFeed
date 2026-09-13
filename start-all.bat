@echo off
rem ============================================================
rem  RecoFeed 一键启动：后端 8000 + 前端 5173
rem  用法：双击本文件。两个服务分别在独立窗口运行，关窗口即停。
rem  说明：用 %~dp0 定位自身目录，避免中文路径写死在脚本里导致乱码。
rem ============================================================
chcp 65001 >nul
title RecoFeed Launcher

set "PY=C:\Users\30500\.workbuddy\binaries\python\envs\default\Scripts\python.exe"

if not exist "%PY%" (
  echo [错误] 找不到 Python：%PY%
  pause
  exit /b 1
)

echo 正在启动后端 (http://127.0.0.1:8000) ...
start "RecoFeed Backend" cmd /k "cd /d %~dp0backend && "%PY%" app.py"

timeout /t 2 >nul

echo 正在启动前端 (http://localhost:5173) ...
start "RecoFeed Frontend" cmd /k "cd /d %~dp0frontend && pnpm dev"

echo.
echo 两个服务已在新窗口启动。浏览器打开 http://localhost:5173 即可使用。
echo 需要停止时，双击 stop-all.bat，或直接关闭那两个黑窗口。
timeout /t 5 >nul
