@echo off
REM ============================================================================
REM  ESCRITORIO OMEGA - sala com 1 especialista por cripto (Canvas, local)
REM  Clique duas vezes neste arquivo para abrir a sala no navegador.
REM ============================================================================
REM  O arquivo escritorio_omega.html eh gerado toda hora pelo agendamento do
REM  monitor (junto com o e-mail) -- este atalho so ABRE o que ja existe.
REM  Se ainda nao existir, gera na hora.
REM ============================================================================

cd /d "%~dp0"
title Escritorio Omega

if not exist "escritorio_omega.html" (
    echo  Ainda nao existe um retrato gerado -- criando um agora...
    if not exist ".venv\Scripts\python.exe" (
        echo  [ERRO] Ambiente virtual nao encontrado em .venv\
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" escritorio_gerar.py
    if errorlevel 1 (
        echo  [ERRO] Nao consegui gerar a sala. Veja a mensagem acima.
        pause
        exit /b 1
    )
)

start "" "escritorio_omega.html"
