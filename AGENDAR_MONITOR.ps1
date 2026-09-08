# =============================================================================
#  Agenda o monitor de sinais no Agendador de Tarefas do Windows.
# =============================================================================
#  Rode UMA VEZ. Depois disso o Windows cuida sozinho.
#
#      powershell -ExecutionPolicy Bypass -File AGENDAR_MONITOR.ps1
#
#  Para desagendar:
#      powershell -ExecutionPolicy Bypass -File AGENDAR_MONITOR.ps1 -Remover
# =============================================================================

param(
    [int]$IntervaloMinutos = 30,
    [switch]$Remover
)

$ErrorActionPreference = "Stop"
$pasta = Split-Path -Parent $MyInvocation.MyCommand.Path
$nomeTarefa = "Radar Cripto - Monitor de Sinais"
$script = Join-Path $pasta "monitor_agendado.ps1"

if ($Remover) {
    try {
        Unregister-ScheduledTask -TaskName $nomeTarefa -Confirm:$false
        Write-Output "Tarefa removida. O monitor nao vai mais rodar sozinho."
    } catch {
        Write-Output "Nao havia tarefa agendada com esse nome."
    }
    exit 0
}

Write-Output "============================================================"
Write-Output "  Agendando o monitor de sinais"
Write-Output "============================================================"

# --- checagens antes de agendar ---------------------------------------------
if (-not (Test-Path (Join-Path $pasta "monitor.env"))) {
    Write-Output ""
    Write-Output "  [X] Falta o arquivo monitor.env com suas credenciais."
    Write-Output ""
    Write-Output "      Crie na pasta do projeto, com este conteudo"
    Write-Output "      (troque pela SENHA DE APP de 16 letras do Gmail):"
    Write-Output ""
    Write-Output '          SMTP_HOST=smtp.gmail.com'
    Write-Output '          SMTP_PORT=587'
    Write-Output '          SMTP_USER=seuemail@gmail.com'
    Write-Output '          SMTP_PASSWORD=as16letrasdasenhadeapp'
    Write-Output '          EMAIL_TO=seuemail@gmail.com'
    Write-Output ""
    Write-Output "      O arquivo ja esta bloqueado no .gitignore -- nao vai pro GitHub."
    exit 1
}

if (-not (Test-Path $script)) {
    Write-Output "  [X] monitor_agendado.ps1 nao encontrado."
    exit 1
}

# --- teste antes de agendar --------------------------------------------------
# Agendar algo que nao funciona so adia a descoberta do problema.
Write-Output ""
Write-Output "[1/2] Testando uma execucao antes de agendar..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Output "      [X] A execucao de teste FALHOU. Veja o motivo em monitor.log:"
    Write-Output ""
    Get-Content (Join-Path $pasta "monitor.log") -Tail 5 | ForEach-Object { Write-Output "          $_" }
    Write-Output ""
    Write-Output "      Corrija antes de agendar."
    exit 1
}
Write-Output "      ok"

# --- agendamento -------------------------------------------------------------
Write-Output ""
Write-Output "[2/2] Registrando a tarefa (a cada $IntervaloMinutos min)..."

$acao = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $pasta

# -Once + RepetitionInterval com duracao maxima = repete indefinidamente.
$gatilho = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervaloMinutos)

# StartWhenAvailable e o ajuste que mais importa aqui: se o PC estiver
# desligado na hora marcada, a tarefa roda assim que ele voltar, em vez de
# simplesmente pular aquela janela. Nao substitui um servidor 24/7, mas evita
# perder o sinal so porque a maquina estava fora do ar.
$config = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $nomeTarefa `
    -Action $acao -Trigger $gatilho -Settings $config `
    -Description "Verifica os sinais do Codigo Omega e manda e-mail quando um vira (ALTA<->BAIXA)." `
    -Force | Out-Null

Write-Output "      ok"
Write-Output ""
Write-Output "============================================================"
Write-Output "  PRONTO. O monitor roda a cada $IntervaloMinutos minutos."
Write-Output "============================================================"
Write-Output "  Manda e-mail SO quando um sinal muda. Sem mudanca, silencio."
Write-Output ""
Write-Output "  Comandos uteis (nesta pasta):"
Write-Output "    Get-Content monitor.log -Tail 20        # ver o que aconteceu"
Write-Output "    Get-ScheduledTask '$nomeTarefa'         # conferir o agendamento"
Write-Output "    .\.venv\Scripts\python.exe monitor_sinais.py --forcar   # resumo agora"
Write-Output ""
Write-Output "  Para desagendar:"
Write-Output "    powershell -ExecutionPolicy Bypass -File AGENDAR_MONITOR.ps1 -Remover"
Write-Output ""
