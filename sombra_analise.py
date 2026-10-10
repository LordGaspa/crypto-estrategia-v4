# -*- coding: utf-8 -*-
"""Leitura e análise do modo sombra (Fase 4) — só LÊ os arquivos que
`sombra.py` e `monitor_agendado.ps1` já gravam; não decide nem envia nada.

Responde às perguntas que decidem a Fase 5:
  - quanto teria rendido (trades fechados + abertos, já com custo);
  - o PC ficou ligado? (rodadas horárias que aconteceram vs esperadas);
  - a execução bate com o backtest? (desvio de preço e atraso por decisão);
  - trade a trade, quanto a sombra diferiu do que o backtest assumiu.

Fusos: `sombra_registro.csv` grava em UTC (o mesmo dos candles); `monitor.log`
grava no relógio do PC (Brasília). Cada análise usa um só dos dois.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta

import pandas as pd

from config_v4 import classificar_liquidez

PASTA = os.path.dirname(os.path.abspath(__file__))
REGISTRO_CSV = os.path.join(PASTA, "sombra_registro.csv")
ESTADO_JSON = os.path.join(PASTA, "sombra_estado.json")
MONITOR_LOG = os.path.join(PASTA, "monitor.log")
MONITOR_ESTADO = os.path.join(PASTA, "monitor_estado.json")

_RODADA = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d):\d\d:\d\d\].*sombra: \d+ ativos lidos")


def disponivel(pasta: str = PASTA) -> bool:
    """Os arquivos da sombra só existem no PC onde ela roda (são gitignored)."""
    return os.path.exists(os.path.join(pasta, "sombra_registro.csv"))


def custo_por_lado(ativo: str) -> float:
    c = classificar_liquidez(ativo)
    return c["taxa"] + c["slippage"]


def carregar_registro(caminho: str = REGISTRO_CSV) -> pd.DataFrame:
    """Só as decisões efetivamente registradas (descarta as 'ignoradas')."""
    reg = pd.read_csv(caminho, parse_dates=["data_hora", "candle_sinal"])
    return reg[reg["status"] == "registrada"].reset_index(drop=True)


def precos_atuais(caminho: str = MONITOR_ESTADO) -> dict:
    with open(caminho, encoding="utf-8") as f:
        return {a: s["preco"] for a, s in json.load(f)["sinais"].items()}


def montar_trades(reg: pd.DataFrame, precos: dict) -> tuple[list, list]:
    """Pareia BUY→SELL por ativo. Devolve (fechados, abertos). Cada trade traz o
    resultado da sombra (preço real da decisão) e o que o backtest assumiu
    (abertura do candle do sinal), lado a lado."""
    fechados, abertos_por_ativo = [], {}
    for _, r in reg.iterrows():
        if r["acao"] == "BUY":
            abertos_por_ativo[r["ativo"]] = r
            continue
        b = abertos_por_ativo.pop(r["ativo"], None)
        if b is None:
            continue
        c = custo_por_lado(r["ativo"])
        bruto = r["preco_agora"] / b["preco_agora"] - 1
        bruto_bt = r["preco_backtest"] / b["preco_backtest"] - 1
        fechados.append({
            "ativo": r["ativo"], "compra": b["data_hora"], "venda": r["data_hora"],
            "valor_usd": float(b["valor_usd"]),
            "preco_compra": float(b["preco_agora"]), "preco_venda": float(r["preco_agora"]),
            "preco_compra_bt": float(b["preco_backtest"]), "preco_venda_bt": float(r["preco_backtest"]),
            "resultado_pct": (bruto - 2 * c) * 100,
            "resultado_bt_pct": (bruto_bt - 2 * c) * 100,
            "resultado_usd": float(b["valor_usd"]) * (bruto - 2 * c),
        })
    abertos = []
    for ativo, b in abertos_por_ativo.items():
        p = precos.get(ativo)
        if p is None:
            continue
        c = custo_por_lado(ativo)
        bruto = p / b["preco_agora"] - 1
        abertos.append({
            "ativo": ativo, "compra": b["data_hora"], "valor_usd": float(b["valor_usd"]),
            "preco_compra": float(b["preco_agora"]), "preco_atual": float(p),
            "stop": float(b["stop"]) if pd.notna(b["stop"]) else None,
            "resultado_pct": (bruto - 2 * c) * 100,
            "resultado_usd": float(b["valor_usd"]) * (bruto - 2 * c),
        })
    return fechados, abertos


def disponibilidade(caminho_log: str = MONITOR_LOG, agora: datetime | None = None) -> dict | None:
    """Rodadas horárias da sombra que aconteceram vs as esperadas, desde a
    primeira. Horas no relógio do PC. A hora corrente só conta depois do
    minuto 7 (a tarefa roda em hh:05)."""
    horas = set()
    with open(caminho_log, encoding="utf-8-sig", errors="replace") as f:
        for linha in f:
            m = _RODADA.match(linha)
            if m:
                horas.add(datetime.strptime(m.group(1), "%Y-%m-%d %H"))
    if not horas:
        return None
    agora = agora or datetime.now()
    fim = agora.replace(minute=0, second=0, microsecond=0)
    if agora.minute < 7:
        fim -= timedelta(hours=1)
    ini = min(horas)
    esperadas = []
    h = ini
    while h <= fim:
        esperadas.append(h)
        h += timedelta(hours=1)
    faltando = [h for h in esperadas if h not in horas]
    buracos, atual = [], []
    for h in faltando:
        if atual and h - atual[-1] > timedelta(hours=1):
            buracos.append(atual)
            atual = []
        atual.append(h)
    if atual:
        buracos.append(atual)
    return {
        "inicio": ini, "fim": fim,
        "rodadas": len([h for h in horas if ini <= h <= fim]),
        "esperadas": len(esperadas),
        "pct": len([h for h in horas if ini <= h <= fim]) / len(esperadas) if esperadas else 0.0,
        "buracos": [{"de": b[0], "ate": b[-1], "horas": len(b)} for b in buracos],
        "horas_com_rodada": sorted(horas),
    }


def qualidade_execucao(reg: pd.DataFrame, limite_atraso_h: float = 1.0) -> dict:
    """Desvio entre o preço real da decisão e o preço que o backtest assume.
    Positivo = pior para COMPRA (pagou mais caro) e melhor para VENDA."""
    if reg.empty:
        return {"n": 0}
    contra = reg.apply(lambda r: r["desvio_pct"] if r["acao"] == "BUY" else -r["desvio_pct"], axis=1)
    no_horario = reg[reg["atraso_h"] <= limite_atraso_h]
    return {
        "n": len(reg),
        "atraso_mediano_h": float(reg["atraso_h"].median()),
        "desvio_medio_pct": float(reg["desvio_pct"].mean()),
        "custo_execucao_medio_pct": float(contra.mean()),
        "custo_execucao_no_horario_pct": float(contra[no_horario.index].mean()) if len(no_horario) else None,
        "pior_desvio_pct": float(reg["desvio_pct"].abs().max()),
        "atrasadas": int((reg["atraso_h"] > limite_atraso_h).sum()),
        "exposicao_max_pct": float(reg["exposicao_pct"].max()) if reg["exposicao_pct"].notna().any() else None,
    }


def resumo(capital: float = 500.0, pasta: str = PASTA) -> dict | None:
    """Tudo de uma vez, pronto para exibir. None se a sombra não roda aqui."""
    if not disponivel(pasta):
        return None
    reg = carregar_registro(os.path.join(pasta, "sombra_registro.csv"))
    precos = precos_atuais(os.path.join(pasta, "monitor_estado.json"))
    fechados, abertos = montar_trades(reg, precos)
    total_f = sum(t["resultado_usd"] for t in fechados)
    total_a = sum(t["resultado_usd"] for t in abertos)
    log = os.path.join(pasta, "monitor.log")
    return {
        "capital": capital,
        "registro": reg,
        "fechados": fechados,
        "abertos": abertos,
        "resultado_fechados_usd": total_f,
        "resultado_abertos_usd": total_a,
        "resultado_usd": total_f + total_a,
        "resultado_pct": (total_f + total_a) / capital * 100,
        "investido_aberto_usd": sum(t["valor_usd"] for t in abertos),
        "acertos": sum(1 for t in fechados if t["resultado_pct"] > 0),
        "execucao": qualidade_execucao(reg),
        "disponibilidade": disponibilidade(log) if os.path.exists(log) else None,
        "inicio": reg["data_hora"].min() if not reg.empty else None,
    }
