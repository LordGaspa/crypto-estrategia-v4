# Monitor de sinais 24/7 — alertas por e-mail

Resolve a limitação do painel: o Streamlit Cloud só calcula quando alguém **abre** a página, então
nunca avisa nada. O `monitor_sinais.py` roda sozinho de tempos em tempos, compara com a última
execução e manda e-mail **só quando um sinal muda** (ALTA↔BAIXA).

---

## Antes de tudo: a restrição que define onde hospedar

**A Binance bloqueia IPs dos Estados Unidos.** Isso já derrubou o app no Streamlit Cloud, e elimina
a maior parte dos serviços gratuitos de automação (GitHub Actions, Heroku, Replit e Render rodam
majoritariamente em datacenters americanos).

O servidor precisa estar **fora dos EUA** — de preferência no Brasil ou Europa.

---

## Passo 1 — Criar a senha de app do Gmail (2 minutos)

O Gmail não aceita mais a senha normal da conta em programas. Você precisa de uma "senha de app":

1. Acesse **myaccount.google.com/security**
2. Ative a **verificação em duas etapas** (obrigatório para o próximo passo)
3. Acesse **myaccount.google.com/apppasswords**
4. Nome do app: `Radar Cripto` → **Criar**
5. Copie a senha de 16 letras que aparecer (ex: `abcd efgh ijkl mnop`)

> ⚠️ Essa senha dá acesso ao envio de e-mails da sua conta. Guarde como senha normal, **não
> comite no Git** e não cole em conversas. O `.gitignore` já bloqueia o arquivo `monitor.env`.

---

## Passo 2 — Testar no seu PC primeiro

Antes de subir pra nuvem, confirme que o e-mail funciona. No **PowerShell**, dentro da pasta do
projeto:

```powershell
$env:SMTP_USER="seuemail@gmail.com"
$env:SMTP_PASSWORD="a senha de app de 16 letras"
$env:EMAIL_TO="seuemail@gmail.com"
.venv\Scripts\python.exe monitor_sinais.py --teste
```

Se chegar um e-mail com "Funcionou!", está tudo certo. Depois rode sem o `--teste` para ele
gravar o estado inicial:

```powershell
.venv\Scripts\python.exe monitor_sinais.py
```

A primeira execução **nunca** manda alerta — ela só fotografa a situação atual. A partir da
segunda, qualquer mudança gera e-mail.

---

## Passo 3 — Escolher onde hospedar

### Opção A — Oracle Cloud Free Tier (grátis para sempre, região São Paulo)

A melhor relação custo/benefício: a Oracle tem um "always free" real, com região em São Paulo
(sem bloqueio da Binance).

1. Crie conta em **cloud.oracle.com** → escolha a região **Brazil East (São Paulo)**
2. Crie uma instância **VM.Standard.A1.Flex** (ARM, always free) com **Ubuntu 22.04**
3. Conecte por SSH e rode:

```bash
sudo apt update && sudo apt install -y python3-pip python3-venv git
git clone https://github.com/LordGaspa/crypto-estrategia-v4.git
cd crypto-estrategia-v4
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

4. Crie o arquivo de credenciais `monitor.env` (só no servidor, nunca no Git):

```bash
cat > monitor.env << 'EOF'
export SMTP_USER="seuemail@gmail.com"
export SMTP_PASSWORD="senha de app de 16 letras"
export EMAIL_TO="seuemail@gmail.com"
EOF
chmod 600 monitor.env
```

5. Teste e agende:

```bash
source monitor.env && .venv/bin/python monitor_sinais.py --teste
source monitor.env && .venv/bin/python monitor_sinais.py   # grava estado inicial
crontab -e
```

Adicione a linha (checa a cada 30 minutos):

```
*/30 * * * * cd ~/crypto-estrategia-v4 && source monitor.env && .venv/bin/python monitor_sinais.py >> monitor.log 2>&1
```

**Nota sobre a Oracle:** o cadastro exige cartão para verificação (não cobra), e às vezes a
capacidade ARM em São Paulo fica esgotada — se acontecer, tente de novo em outro horário ou use a
opção B.

### Opção B — VPS pago simples (~R$ 25–35/mês)

Se não quiser lidar com o cadastro da Oracle: **Contabo**, **Hostinger** ou **DigitalOcean**,
escolhendo região **Brasil ou Europa** (nunca EUA). O passo a passo depois de criar a máquina é
idêntico ao da Opção A, a partir do `sudo apt update`.

### Opção C — Seu próprio PC, agendado

Funciona e é grátis, mas só verifica com o PC ligado. Abra o **Agendador de Tarefas** do Windows →
*Criar Tarefa Básica* → repetir a cada 30 min → ação: iniciar programa
`C:\backup dan e caca\dan\CRYPTO\claude\.venv\Scripts\python.exe`, argumento `monitor_sinais.py`,
iniciar em `C:\backup dan e caca\dan\CRYPTO\claude`. As variáveis de ambiente precisam estar
definidas no nível do sistema (Painel de Controle → Variáveis de Ambiente).

---

## Qual frequência usar

Os timeframes da estratégia são de 4h a 12h, então **checar a cada 30 minutos é mais que
suficiente** — um sinal novo só pode aparecer quando um candle fecha. Checar de minuto em minuto
só gastaria API à toa, sem detectar nada mais cedo.

---

## Decisões de segurança embutidas no script

Três comportamentos que existem para o monitor não te enganar:

1. **Não confia em dado velho.** O `carregar_dados` cai pro cache antigo quando a Binance falha
   (feito assim para o painel não quebrar). Num monitor isso seria perigoso — poderia comparar
   estado novo contra dado velho e disparar alerta falso. Se o último candle estiver com mais de
   30h de atraso, o ativo é marcado como **não verificado** e sai da comparação, e o e-mail avisa
   quais ficaram de fora.

2. **Se o e-mail falhar, o estado não é salvo.** Assim a próxima execução tenta avisar de novo, em
   vez de engolir a mudança silenciosamente.

3. **A primeira execução nunca alerta.** Ela só fotografa a situação — evita receber 22 e-mails de
   "novo sinal" logo na estreia.

---

## Comandos úteis

```bash
.venv/bin/python monitor_sinais.py --teste    # só testa o envio de e-mail
.venv/bin/python monitor_sinais.py --forcar   # manda resumo mesmo sem mudança
tail -f monitor.log                            # acompanha o que o cron está fazendo
```

---

**Aviso:** os e-mails são alertas automáticos do seu próprio sistema, baseados na
`RECEITA_ROBUSTA`. Não são recomendação de investimento, e o monitor não executa nenhuma ordem —
ele só avisa.
