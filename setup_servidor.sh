#!/usr/bin/env bash
# ============================================================================
#  SETUP DO MONITOR 24/7 -- servidor Ubuntu (Oracle Cloud, VPS, etc.)
# ============================================================================
#  Instala dependencias, pede as credenciais de e-mail, testa o envio, grava o
#  estado inicial e agenda no cron. Idempotente: pode rodar de novo sem quebrar.
#
#  Uso (dentro da pasta do projeto, ja clonada):
#      bash setup_servidor.sh
# ============================================================================

set -euo pipefail

PASTA="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PASTA"
ENV_FILE="$PASTA/monitor.env"
PY="$PASTA/.venv/bin/python"
INTERVALO_MIN="${INTERVALO_MIN:-30}"

echo "============================================================"
echo "  MONITOR DE SINAIS -- setup do servidor"
echo "  Pasta: $PASTA"
echo "============================================================"
echo

# ── 1. Dependencias do sistema ──────────────────────────────────────────────
echo "[1/6] Instalando dependencias do sistema..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3-pip python3-venv cron >/dev/null
sudo systemctl enable --now cron >/dev/null 2>&1 || true
echo "      ok"

# ── 2. Ambiente virtual ─────────────────────────────────────────────────────
echo "[2/6] Preparando ambiente Python..."
if [ ! -x "$PY" ]; then
    python3 -m venv .venv
fi
"$PASTA/.venv/bin/pip" install --quiet --upgrade pip
# lista MINIMA: o monitor nao precisa de streamlit/altair/scipy (ver
# requirements-monitor.txt). Instalar a lista completa em ARM e lento e fragil.
"$PASTA/.venv/bin/pip" install --quiet -r requirements-monitor.txt
echo "      ok ($("$PY" --version))"

# ── 3. Fuso horario (pra data do e-mail e do cron baterem com o Brasil) ─────
echo "[3/6] Ajustando fuso horario para America/Sao_Paulo..."
sudo timedatectl set-timezone America/Sao_Paulo 2>/dev/null || \
    echo "      (nao foi possivel; seguindo com o fuso atual)"
echo "      $(date '+%d/%m/%Y %H:%M %Z')"

# ── 4. Credenciais ──────────────────────────────────────────────────────────
echo "[4/6] Credenciais de e-mail"
if [ -f "$ENV_FILE" ]; then
    echo "      monitor.env ja existe -- reaproveitando."
    echo "      (apague o arquivo e rode de novo se quiser trocar)"
else
    echo
    echo "      A senha e a SENHA DE APP do Gmail (16 letras), nao a senha da conta."
    echo "      Ela fica so neste servidor, em arquivo com permissao 600."
    echo
    read -rp "      E-mail de envio (Gmail): " SMTP_USER
    read -rsp "      Senha de app (nao aparece na tela): " SMTP_PASSWORD; echo
    read -rp "      Enviar alertas para [$SMTP_USER]: " EMAIL_TO
    EMAIL_TO="${EMAIL_TO:-$SMTP_USER}"

    umask 077
    cat > "$ENV_FILE" << EOF
export SMTP_HOST="smtp.gmail.com"
export SMTP_PORT="587"
export SMTP_USER="$SMTP_USER"
export SMTP_PASSWORD="$SMTP_PASSWORD"
export EMAIL_TO="$EMAIL_TO"
EOF
    chmod 600 "$ENV_FILE"
    unset SMTP_PASSWORD
    echo "      ok -- salvo em monitor.env (permissao 600, ignorado pelo git)"
fi

# ── 5. Teste de envio + estado inicial ──────────────────────────────────────
echo "[5/6] Testando envio de e-mail..."
set +u; source "$ENV_FILE"; set -u
if ! "$PY" monitor_sinais.py --teste; then
    echo
    echo "  !! O e-mail de teste FALHOU. Corrija antes de agendar."
    echo "     Causas comuns: senha de app errada, ou verificacao em duas"
    echo "     etapas desativada na conta Google."
    exit 1
fi

echo
echo "      Gravando estado inicial (esta rodada nao dispara alerta)..."
"$PY" monitor_sinais.py

# ── 6. Agendamento ──────────────────────────────────────────────────────────
echo "[6/6] Agendando no cron (a cada ${INTERVALO_MIN} min)..."
LINHA="*/${INTERVALO_MIN} * * * * cd $PASTA && set -a && . $ENV_FILE && set +a && $PY monitor_sinais.py >> $PASTA/monitor.log 2>&1"
# remove agendamento antigo deste mesmo script antes de inserir o novo
( crontab -l 2>/dev/null | grep -v "monitor_sinais.py" ; echo "$LINHA" ) | crontab -
echo "      ok"

echo
echo "============================================================"
echo "  PRONTO. O monitor esta no ar."
echo "============================================================"
echo "  Verifica a cada ${INTERVALO_MIN} minutos e manda e-mail quando"
echo "  um sinal virar (ALTA<->BAIXA). Sem mudanca, nao envia nada."
echo
echo "  Comandos uteis:"
echo "    tail -f $PASTA/monitor.log     # acompanhar em tempo real"
echo "    crontab -l                              # ver o agendamento"
echo "    $PY monitor_sinais.py --forcar          # resumo agora, mesmo sem mudanca"
echo
