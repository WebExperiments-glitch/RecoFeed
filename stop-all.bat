@echo off
rem ============================================================
rem  RecoFeed 一键停止：按端口杀掉后端 8000 与前端 5173
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
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":5173" ^| findstr LISTENING') do (
  taskkill /F /PID %%a >nul 2>&1
  if not errorlevel 1 echo 已停止前端 PID %%a
  set "FOUND=1"
)

if not defined FOUND echo 没有找到运行中的服务（8000 / 5173 均空闲）
timeout /t 4 >nul
