# -*- coding: utf-8 -*-
# MONITOR DE SINAIS -- roda periodicamente e avisa por e-mail quando um sinal muda
# ----------------------------------------------------------------------------
# Resolve a limitacao do painel Streamlit: ele so calcula quando alguem ABRE a
# pagina, entao nunca avisa nada. Este script roda sozinho (cron/agendador),
# compara o estado atual com o da ultima execucao e manda e-mail SO quando
# algo muda -- ALTA->BAIXA ou BAIXA->ALTA.
#
# Fonte da verdade: estrategia_core.estado_posicao_atual com a RECEITA_ROBUSTA,
# exatamente a mesma do painel e do radar_console. Nao ha como divergir.
#
# ----------------------------------------------------------------------------
# SEGURANCA DE DADOS (importante)
# ----------------------------------------------------------------------------
# config_v4.carregar_dados cai pro cache VELHO quando a Binance falha (foi feito
# assim de proposito pro painel nao quebrar no Streamlit Cloud). Num MONITOR
# isso seria perigoso: compararia estado novo contra estado velho e poderia
# disparar alerta falso, ou pior, deixar de avisar uma mudanca real.
# Por isso aqui existe uma checagem de FRESCOR: se o ultimo candle for mais
# velho que MAX_ATRASO_HORAS, o ativo e considerado NAO CONFIAVEL, e o monitor
# avisa da falha em vez de fingir que tem sinal.
#
# ----------------------------------------------------------------------------
# CONFIGURACAO -- variaveis de ambiente (NUNCA colocar senha no codigo)
# ----------------------------------------------------------------------------
#   SMTP_HOST      ex.: smtp.gmail.com
#   SMTP_PORT      ex.: 587
#   SMTP_USER      seu e-mail de envio
#   SMTP_PASSWORD  senha de APP (Gmail: myaccount.google.com/apppasswords)
#   EMAIL_TO       para onde mandar (pode ser o mesmo do SMTP_USER)
#   ESTADO_ARQUIVO (opcional) caminho do arquivo de estado, padrao ao lado do script
#
# Como rodar:
#   .venv\Scripts\python.exe monitor_sinais.py            -> checa e avisa se mudou
#   .venv\Scripts\python.exe monitor_sinais.py --teste    -> manda e-mail de teste
#   .venv\Scripts\python.exe monitor_sinais.py --forcar   -> manda resumo mesmo sem mudanca

import os
import sys
import json
import smtplib
import traceback
from datetime import datetime, timezone
from email.message import EmailMessage

import pandas as pd

from config_v4 import (
    ATIVOS_PORTFOLIO_V4, RECEITA_ROBUSTA, grupo_ouro, carregar_dados, CANDLES_POR_DIA,
)
from estrategia_core import estado_posicao_atual

AQUI = os.path.dirname(os.path.abspath(__file__))
ESTADO_ARQUIVO = os.environ.get("ESTADO_ARQUIVO", os.path.join(AQUI, "monitor_estado.json"))
BOOTSTRAP_CSV = os.path.join(AQUI, "walkforward_robusta_v4_resumo.csv")

# Um candle de 12h pode legitimamente ter algumas horas de "atraso". Damos folga
# de 2 candles do maior timeframe usado (12h) + margem.
MAX_ATRASO_HORAS = 30


# ─────────────────────────────────────────────────────────────────────────────
# Coleta de sinais
# ─────────────────────────────────────────────────────────────────────────────

def montar_df_fast(df, params):
    d = {
        "abertura": df["abertura"].values,
        "minima": df["minima"].values,
        "fechamento": df["fechamento"].values,
        "t_abert": df["t_abert"].values,
    }
    d[f"ma_{params['media_rapida']}"] = df["fechamento"].rolling(params["media_rapida"]).mean().values
    d[f"ma_{params['media_lenta']}"] = df["fechamento"].rolling(params["media_lenta"]).mean().values
    d[f"ma_f_{params['media_filtro']}"] = df["fechamento"].rolling(params["media_filtro"]).mean().values
    tr = pd.concat([
        df["maxima"] - df["minima"],
        (df["maxima"] - df["fechamento"].shift()).abs(),
        (df["minima"] - df["fechamento"].shift()).abs(),
    ], axis=1).max(axis=1)
    d[f"atr_{params['atr_periodo']}"] = tr.rolling(params["atr_periodo"]).mean().values
    return d


def coletar_sinais():
    """Devolve (sinais, falhas). `sinais` so contem ativos com dado FRESCO --
    ativo com dado velho vai pra `falhas` e NAO entra na comparacao, pra nunca
    gerar alerta a partir de cache desatualizado."""
    sinais, falhas = {}, []
    agora = datetime.now(timezone.utc).replace(tzinfo=None)

    for ativo, interval in ATIVOS_PORTFOLIO_V4.items():
        try:
            df = carregar_dados(ativo, interval)
            if df is None or df.empty:
                falhas.append((ativo, "sem dados"))
                continue

            ultimo = pd.Timestamp(df["t_abert"].iloc[-1])
            atraso_h = (agora - ultimo).total_seconds() / 3600
            if atraso_h > MAX_ATRASO_HORAS:
                falhas.append((ativo, f"dado desatualizado ({atraso_h:.0f}h de atraso)"))
                continue

            params = RECEITA_ROBUSTA[grupo_ouro(ativo)]
            est = estado_posicao_atual(montar_df_fast(df, params), params, slippage=0.0)
            sinais[ativo] = {
                "posicionado": bool(est["posicionado"]),
                "preco": float(est["preco_atual"]) if est["preco_atual"] is not None else None,
                "stop": float(est["stop_atual"]) if est["stop_atual"] is not None else None,
                "preco_sinal": float(est["preco_sinal"]) if est["preco_sinal"] else None,
                "data_sinal": (pd.Timestamp(est["data_mudanca"]).strftime("%d/%m/%Y %H:%M")
                                if est["data_mudanca"] is not None else None),
                "interval": interval,
            }
        except Exception as e:
            falhas.append((ativo, f"erro: {e}"))
    return sinais, falhas


def carregar_estado():
    if not os.path.exists(ESTADO_ARQUIVO):
        return None
    try:
        with open(ESTADO_ARQUIVO, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def salvar_estado(sinais):
    tmp = ESTADO_ARQUIVO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"atualizado_em": datetime.now().isoformat(timespec="seconds"),
                    "sinais": sinais}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ESTADO_ARQUIVO)  # escrita atomica: nao corrompe se cair no meio


def detectar_mudancas(anterior, atual):
    """Lista de (ativo, de, para) apenas pra ativos presentes NOS DOIS estados.
    Ativo novo (primeira vez visto) nao gera alerta -- nao ha 'mudanca'."""
    if not anterior:
        return []
    ant = anterior.get("sinais", {})
    mudancas = []
    for ativo, dados in atual.items():
        if ativo not in ant:
            continue
        antes = ant[ativo].get("posicionado")
        agora = dados["posicionado"]
        if antes is not None and antes != agora:
            mudancas.append((ativo, antes, agora))
    return mudancas


# ─────────────────────────────────────────────────────────────────────────────
# E-mail
# ─────────────────────────────────────────────────────────────────────────────

def config_email():
    cfg = {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER"),
        "password": os.environ.get("SMTP_PASSWORD"),
        "to": os.environ.get("EMAIL_TO") or os.environ.get("SMTP_USER"),
    }
    faltando = [k for k in ("user", "password", "to") if not cfg[k]]
    if faltando:
        raise RuntimeError(
            "Credenciais de e-mail ausentes: " + ", ".join(f"SMTP_{k.upper()}" for k in faltando)
            + "\nDefina as variaveis de ambiente antes de rodar (ver MONITOR_DEPLOY.md)."
        )
    return cfg


def fmt_preco(v):
    if v is None:
        return "-"
    if v >= 1:
        return f"{v:,.2f}"
    if v >= 0.01:
        return f"{v:.4f}"
    return f"{v:.8f}"


def montar_html(mudancas, sinais, falhas, sig_map):
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    linhas = []
    for ativo, antes, depois in mudancas:
        d = sinais[ativo]
        cor = "#22c55e" if depois else "#ef4444"
        rotulo = "COMPRA (entrou)" if depois else "VENDA (saiu)"
        seta = "BAIXA &rarr; ALTA" if depois else "ALTA &rarr; BAIXA"
        stop_txt = ""
        if depois and d["stop"] and d["preco"]:
            dist = abs(d["stop"] / d["preco"] - 1) * 100
            stop_txt = (f"<div style='color:#475569;font-size:13px;margin-top:4px'>"
                        f"Stop: <b>{fmt_preco(d['stop'])}</b> "
                        f"({dist:.2f}% abaixo do preco atual)</div>")
        sig = sig_map.get(ativo, "")
        sig_txt = (f"<div style='color:#64748b;font-size:12px;margin-top:4px'>{sig}</div>"
                   if sig else "")
        linhas.append(f"""
        <div style="border-left:4px solid {cor};background:#f8fafc;padding:12px 14px;margin:10px 0;border-radius:6px">
          <div style="font-size:17px;font-weight:700;color:#0f172a">{ativo}
            <span style="color:{cor};font-size:14px">&nbsp;{rotulo}</span></div>
          <div style="color:#334155;font-size:14px;margin-top:6px">{seta} &middot;
            preco <b>{fmt_preco(d['preco'])}</b> &middot; {d['interval']}</div>
          {stop_txt}{sig_txt}
        </div>""")

    n_alta = sum(1 for d in sinais.values() if d["posicionado"])
    aviso_falhas = ""
    if falhas:
        itens = "".join(f"<li>{a}: {m}</li>" for a, m in falhas)
        aviso_falhas = f"""
        <div style="background:#fef3c7;border:1px solid #fcd34d;padding:10px 14px;border-radius:6px;margin-top:16px">
          <b style="color:#92400e">Atencao &mdash; {len(falhas)} ativo(s) nao verificados:</b>
          <ul style="color:#92400e;font-size:13px;margin:6px 0 0 0">{itens}</ul>
          <div style="color:#92400e;font-size:12px;margin-top:6px">
            Estes ficaram de fora da checagem (dado indisponivel ou desatualizado) &mdash;
            nao houve alerta sobre eles, o que nao significa que nao mudaram.</div>
        </div>"""

    return f"""<html><body style="font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;
      background:#ffffff;color:#0f172a;max-width:640px;margin:0 auto;padding:20px">
      <h2 style="margin:0 0 4px 0">Mudanca de sinal detectada</h2>
      <div style="color:#64748b;font-size:13px;margin-bottom:16px">{agora} &middot;
        Estrategia Ouro Robusta</div>
      {''.join(linhas)}
      <div style="border-top:1px solid #e2e8f0;margin-top:20px;padding-top:12px;
        color:#475569;font-size:13px">
        Situacao geral: <b>{n_alta} de {len(sinais)}</b> ativos posicionados (ALTA).
      </div>
      {aviso_falhas}
      <div style="color:#94a3b8;font-size:11px;margin-top:18px;border-top:1px solid #e2e8f0;padding-top:10px">
        Sinal gerado pela RECEITA_ROBUSTA (cruzamento de medias + filtro de tendencia,
        saida por stop ATR ou cruzamento contrario). Isto e um alerta automatico do seu
        proprio sistema, nao uma recomendacao de investimento.
      </div>
    </body></html>"""


def enviar_email(assunto, html, texto_puro):
    cfg = config_email()
    msg = EmailMessage()
    msg["Subject"] = assunto
    msg["From"] = cfg["user"]
    msg["To"] = cfg["to"]
    msg.set_content(texto_puro)
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as s:
        s.starttls()
        s.login(cfg["user"], cfg["password"])
        s.send_message(msg)
    return cfg["to"]


def carregar_significancia():
    try:
        b = pd.read_csv(BOOTSTRAP_CSV).set_index("Ativo")
    except Exception:
        return {}
    out = {}
    for ativo in b.index:
        p, nj = b.loc[ativo].get("P_value_boot"), b.loc[ativo].get("N_Janelas")
        if pd.isna(p):
            continue
        if pd.notna(nj) and int(nj) < 4:
            out[ativo] = f"Historico curto ({int(nj)} janelas) - sem lastro estatistico"
        elif p < 0.05:
            out[ativo] = f"Edge confirmado (p={p:.3f})"
        else:
            out[ativo] = f"Edge NAO confirmado (p={p:.3f}) - cautela"
    return out


# ─────────────────────────────────────────────────────────────────────────────

def main():
    modo_teste = "--teste" in sys.argv
    forcar = "--forcar" in sys.argv
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if modo_teste:
        try:
            destino = enviar_email(
                "[Radar Cripto] E-mail de teste",
                "<html><body style='font-family:sans-serif'><h2>Funcionou!</h2>"
                "<p>O monitor consegue te enviar e-mails. Agora e so deixar rodando.</p>"
                "</body></html>",
                "Funcionou! O monitor consegue te enviar e-mails.",
            )
            print(f"[{ts}] E-mail de teste enviado para {destino}")
            return 0
        except Exception as e:
            print(f"[{ts}] FALHA ao enviar e-mail de teste: {e}")
            return 1

    print(f"[{ts}] Coletando sinais...")
    try:
        sinais, falhas = coletar_sinais()
    except Exception:
        print(f"[{ts}] ERRO na coleta:\n{traceback.format_exc()}")
        return 1

    if not sinais:
        print(f"[{ts}] Nenhum ativo com dado confiavel -- nada a comparar. "
              f"Falhas: {len(falhas)}")
        return 1

    anterior = carregar_estado()
    mudancas = detectar_mudancas(anterior, sinais)
    n_alta = sum(1 for d in sinais.values() if d["posicionado"])

    if anterior is None:
        salvar_estado(sinais)
        print(f"[{ts}] Primeira execucao -- estado inicial salvo "
              f"({len(sinais)} ativos, {n_alta} em ALTA). Sem alerta nesta rodada.")
        return 0

    if not mudancas and not forcar:
        print(f"[{ts}] Sem mudancas. {n_alta}/{len(sinais)} em ALTA. "
              f"{len(falhas)} falha(s).")
        salvar_estado(sinais)
        return 0

    sig_map = carregar_significancia()
    if mudancas:
        nomes = ", ".join(f"{a} {'COMPRA' if d else 'VENDA'}" for a, _b, d in mudancas)
        assunto = f"[Radar Cripto] {len(mudancas)} novo(s) sinal(is): {nomes}"[:180]
    else:
        assunto = f"[Radar Cripto] Resumo forcado - {n_alta}/{len(sinais)} em ALTA"

    texto = "\n".join(
        f"{a}: {'BAIXA -> ALTA (COMPRA)' if d else 'ALTA -> BAIXA (VENDA)'} "
        f"| preco {fmt_preco(sinais[a]['preco'])}"
        for a, _b, d in mudancas
    ) or f"{n_alta}/{len(sinais)} ativos em ALTA."

    try:
        destino = enviar_email(assunto, montar_html(mudancas, sinais, falhas, sig_map), texto)
        print(f"[{ts}] {len(mudancas)} mudanca(s) -> e-mail enviado para {destino}")
        for a, _b, d in mudancas:
            print(f"    {a}: {'COMPRA' if d else 'VENDA'}")
    except Exception as e:
        # NAO salva o estado se o e-mail falhou -- assim a proxima execucao
        # tenta avisar de novo, em vez de engolir a mudanca silenciosamente
        print(f"[{ts}] Mudancas detectadas MAS o e-mail falhou: {e}")
        print(f"[{ts}] Estado NAO foi salvo -- proxima rodada tentara avisar de novo.")
        return 1

    salvar_estado(sinais)
    return 0


if __name__ == "__main__":
    sys.exit(main())
