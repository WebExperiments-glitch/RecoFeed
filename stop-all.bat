@echo off
rem ============================================================
rem  RecoFeed 一键停止：后端 8000 + 前端（端口可能不是 5173，故扫一段）
rem  用法：双击本文件。
rem ============================================================
chcp 65001 >nul
title RecoFeed Stopper

set "FOUND="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000" ^| findstr LISTENING') do (
  taskkill /F /PID %%a >nul 2>&1
  if not errorlevel 1 echo 已停止后端 PID %%a
  set "FOUND=1"
)

rem 前端端口可能变（5173 被 Windows 保留段占用时会换到 5273 等）→ 常见端口都扫一遍
for %%P in (5173 5273 5373 5473 5573 5673 5683 5693) do (
  for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
    if not errorlevel 1 echo 已停止前端 PID %%a （端口 %%P）
    set "FOUND=1"
  )
)

if not defined FOUND echo 没有找到运行中的服务（8000 与常见前端端口均空闲）
timeout /t 4 >nul
