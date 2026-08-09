@echo off
REM ============================================================================
REM  RADAR DE SINAIS - painel completo (Streamlit)
REM  Clique duas vezes neste arquivo para abrir o dashboard no navegador.
REM ============================================================================
REM  Roda a partir da pasta do proprio .bat (%~dp0), entao funciona mesmo se
REM  voce criar um atalho na area de trabalho.
REM ============================================================================

cd /d "%~dp0"
title Radar de Sinais - Estrategia Ouro Robusta

echo.
echo  ============================================================
echo    RADAR DE SINAIS - Estrategia Ouro Robusta
echo  ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo  [ERRO] Ambiente virtual nao encontrado em .venv\
    echo.
    echo  Este .bat precisa estar na pasta do projeto, junto do app_v4.py.
    echo  Pasta atual: %CD%
    echo.
    pause
    exit /b 1
)

if not exist "app_v4.py" (
    echo  [ERRO] app_v4.py nao encontrado nesta pasta.
    echo  Pasta atual: %CD%
    echo.
    pause
    exit /b 1
)

echo  Iniciando o painel... (a primeira carga leva ~20s, baixando candles)
echo.
echo  O navegador abre sozinho em http://localhost:8501
echo  Para FECHAR: volte nesta janela e aperte Ctrl+C, ou apenas feche-a.
echo.

REM abre o navegador em paralelo, com um atraso pra dar tempo do servidor subir
start "" /b cmd /c "timeout /t 6 /nobreak >nul && start "" http://localhost:8501"

.venv\Scripts\python.exe -m streamlit run app_v4.py --server.headless true --server.port 8501 --browser.gatherUsageStats false

echo.
echo  Painel encerrado.
pause
