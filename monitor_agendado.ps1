# =============================================================================
#  Lancador do monitor de sinais para o Agendador de Tarefas do Windows.
# =============================================================================
#  Le as credenciais do arquivo monitor.env (que NAO vai pro Git), define as
#  variaveis de ambiente e roda o monitor, gravando tudo em monitor.log.
#
#  Nao rode este arquivo na mao para configurar -- use AGENDAR_MONITOR.ps1.
#  Para testar agora:
#      powershell -ExecutionPolicy Bypass -File monitor_agendado.ps1
# =============================================================================

param([string[]]$ArgsMonitor = @())

$ErrorActionPreference = "Stop"
$pasta = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $pasta

$envFile = Join-Path $pasta "monitor.env"
$python   = Join-Path $pasta ".venv\Scripts\python.exe"
$log      = Join-Path $pasta "monitor.log"

function Registrar($texto) {
    $carimbo = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $log -Value "[$carimbo] $texto" -Encoding utf8
}

# --- credenciais -------------------------------------------------------------
if (-not (Test-Path $envFile)) {
    Registrar "ERRO: monitor.env nao encontrado. Crie o arquivo (ver MONITOR_DEPLOY.md)."
    exit 1
}

# Aceita tanto 'export CHAVE="valor"' (formato do servidor Linux) quanto
# 'CHAVE=valor' -- assim o MESMO arquivo serve nos dois lugares e ninguem
# precisa manter duas copias das credenciais.
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $chave  = $Matches[1]
        $valor  = $Matches[2].Trim()
        $valor  = $valor -replace '^"(.*)"$', '$1'      # tira aspas duplas
        $valor  = $valor -replace "^'(.*)'$", '$1'      # tira aspas simples
        Set-Item -Path "env:$chave" -Value $valor
    }
}

if (-not $env:SMTP_USER -or -not $env:SMTP_PASSWORD) {
    Registrar "ERRO: SMTP_USER ou SMTP_PASSWORD ausentes em monitor.env."
    exit 1
}

# --- execucao ----------------------------------------------------------------
if (-not (Test-Path $python)) {
    Registrar "ERRO: python do projeto nao encontrado em .venv\Scripts\python.exe"
    exit 1
}

# stderr e capturado junto: se algo quebrar, o motivo fica no log em vez de
# sumir numa janela que ninguem ve (a tarefa roda escondida).
$saida = & $python "monitor_sinais.py" @ArgsMonitor 2>&1
$codigo = $LASTEXITCODE

foreach ($linha in $saida) { Registrar $linha }
if ($codigo -ne 0) { Registrar "monitor_sinais.py terminou com codigo $codigo" }

# --- modo sombra (Fase 4) ----------------------------------------------------
# Roda DEPOIS do monitor e so na execucao normal (nao no --teste). A ordem
# importa: o e-mail e o que voce depende hoje; se a sombra quebrar, o alerta ja
# saiu. E ela nao envia ordem nenhuma -- so registra o que teria feito.
if ($ArgsMonitor.Count -eq 0) {
    $saidaSombra = & $python "sombra.py" 2>&1
    foreach ($linha in $saidaSombra) { Registrar $linha }
    if ($LASTEXITCODE -ne 0) { Registrar "sombra.py terminou com codigo $LASTEXITCODE" }
}

# --- escritorio omega (visual) ------------------------------------------------
# Regenera escritorio_omega.html com os dados desta rodada -- so na execucao
# normal, depois do monitor e da sombra ja terem escrito monitor_estado.json.
# Se isto falhar, nao afeta o e-mail nem a sombra: eles ja rodaram.
if ($ArgsMonitor.Count -eq 0) {
    $saidaEscritorio = & $python "escritorio_gerar.py" 2>&1
    foreach ($linha in $saidaEscritorio) { Registrar $linha }
    if ($LASTEXITCODE -ne 0) { Registrar "escritorio_gerar.py terminou com codigo $LASTEXITCODE" }
}

exit $codigo
