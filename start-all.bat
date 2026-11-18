@echo off
rem ============================================================
rem  RecoFeed 一键启动：后端 8000 + 前端（默认 5173，被系统保留时自动往后找）
rem  用法：双击本文件。两个服务分别在独立窗口运行，关窗口即停。
rem  说明：用 %~dp0 定位自身目录，避免中文路径写死在脚本里导致乱码。
rem ============================================================
chcp 65001 >nul
title RecoFeed Launcher

rem 自动探测 Python：优先项目内 venv，其次 PATH 里已装好后端依赖的 python / py。
rem （不要再写死某台机器的绝对路径 —— 换机器会直接启动失败）
set "PY="
if exist "%~dp0backend\.venv\Scripts\python.exe" set "PY=%~dp0backend\.venv\Scripts\python.exe"
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY (
  "%%i" -c "import fastapi" >nul 2>nul && set "PY=%%i"
)
if not defined PY for /f "delims=" %%i in ('where py 2^>nul') do if not defined PY (
  "%%i" -c "import fastapi" >nul 2>nul && set "PY=%%i"
)

if not defined PY (
  echo [错误] 找不到可用的 Python（需要 3.10+ 且已装 backend\requirements.txt）。
  echo        请先在 backend 目录创建 .venv 并执行 pip install -r requirements.txt。
  pause
  exit /b 1
)
echo 使用 Python：%PY%

rem 前端端口：可用环境变量 VITE_PORT 覆盖。
rem ⚠️ 5173 有时会落在 Windows 保留段（Hyper-V/WSL 动态保留，开代理/加速器后常见），
rem    此时 vite 会报 EACCES 起不来。被占用时就换下面列表里的下一个端口。
set "FRONTEND_PORT=%VITE_PORT%"
if "%FRONTEND_PORT%"=="" set "FRONTEND_PORT=5173"

where pnpm >nul 2>nul
if errorlevel 1 (
  echo [错误] 找不到 pnpm。请先执行：npm install -g pnpm
  pause
  exit /b 1
)

echo 正在启动后端 (http://127.0.0.1:8000) ...
start "RecoFeed Backend" cmd /k "cd /d "%~dp0backend" && "%PY%" app.py"

ping -n 3 127.0.0.1 >nul

echo 正在启动前端 (http://localhost:%FRONTEND_PORT%) ...
start "RecoFeed Frontend" cmd /k "cd /d "%~dp0frontend" && set VITE_PORT=%FRONTEND_PORT% && pnpm dev"

echo.
echo 两个服务已在新窗口启动。浏览器打开 http://localhost:%FRONTEND_PORT% 即可使用。
echo 如果前端窗口里 Vite 打印的端口与此不同（端口被占时它会自动往后找），以窗口输出为准。
echo 登录的回跳地址会自动跟随实际端口，不需要改任何配置。
echo 需要停止时，双击 stop-all.bat，或直接关闭那两个黑窗口。
ping -n 7 127.0.0.1 >nul
