@echo off
REM ============================================================================
REM  SINAIS - checagem rapida no terminal (sem navegador)
REM  Clique duas vezes para ver os 22 sinais em ~15 segundos.
REM  Para o painel completo com graficos, use RADAR.bat
REM ============================================================================

cd /d "%~dp0"
title Sinais - Estrategia Ouro Robusta

if not exist ".venv\Scripts\python.exe" (
    echo  [ERRO] Ambiente virtual nao encontrado em .venv\
    echo  Pasta atual: %CD%
    pause
    exit /b 1
)

.venv\Scripts\python.exe radar_console.py
