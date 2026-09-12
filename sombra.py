# -*- coding: utf-8 -*-
"""Modo sombra (Fase 4 do PLANO_EXECUCAO_AUTOMATICA.md).

O bot decide de verdade, com sinais de verdade, e **registra a ordem que teria
enviado sem enviar nada**. Nenhuma chave de API é usada: só endpoints públicos
da Binance (preço e filtros dos pares). É impossível este arquivo mover dinheiro.

Para que serve: medir, com o mercado real, as discrepâncias que o backtest não
enxerga (Seção 3 do plano). A cada decisão ele grava lado a lado:

  - `preco_backtest`  — a abertura do candle que gerou o sinal, que é o preço
                        pelo qual o backtest assume que a ordem foi executada;
  - `preco_agora`     — o preço no instante em que o bot realmente decidiria;
  - `atraso_h`        — quanto tempo passou entre um e outro.

A diferença entre essas duas colunas, acumulada ao longo de semanas, é a
resposta para "o backtest mente sobre o preço de entrada?" — a discrepância 3.2.
Junto vão o stop vigente (para a 3.1), o tamanho que caberia nos filtros reais
da Binance (3.4) e o veredito dos guardrails.

Segue as mesmas regras do executor: a decisão vem de
`estrategia_core.estado_posicao_atual`, nunca de lógica paralela; e a carteira
começa ZERADA, entrando só em sinais que nascerem depois de ligar (3.5) —
comprar tudo o que já estava em ALTA seria entrar atrasado em tudo ao mesmo
tempo, e sujaria a medição logo no primeiro dia.
"""
from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

import pandas as pd

from config_v4 import (
    ATIVOS_PORTFOLIO_V4, RECEITA_ROBUSTA, grupo_ouro, carregar_dados,
)
from estrategia_core import estado_posicao_atual
from executor_ordens import COMPRA, VENDA, calcular_quantidade, extrair_filtros
import guardrails
from monitor_sinais import montar_df_fast

PASTA = os.path.dirname(os.path.abspath(__file__))
ESTADO_ARQUIVO = os.path.join(PASTA, "sombra_estado.json")
REGISTRO_CSV = os.path.join(PASTA, "sombra_registro.csv")
PESOS_CSV = os.path.join(PASTA, "portfolio_v4_pesos.csv")

CAPITAL = Decimal("500")        # o capital que a Fase 5 usaria de verdade

COLUNAS = [
    "data_hora", "ativo", "interval", "acao", "candle_sinal",
    "preco_backtest", "preco_agora", "desvio_pct", "atraso_h",
    "qty", "valor_usd", "stop", "status", "motivo", "exposicao_pct",
]


# ---------------------------------------------------------------------------
# pesos
# ---------------------------------------------------------------------------
def carregar_pesos(caminho: str = PESOS_CSV) -> dict:
    """Peso de cada ativo no portfólio (volatilidade inversa).

    Se o CSV faltar, cai para peso igual — melhor medir com peso aproximado do
    que não medir nada; o que a Fase 4 avalia é o desvio de PREÇO, que não
    depende do tamanho da posição."""
    try:
        df = pd.read_csv(caminho)
        return {r["Ativo"]: Decimal(str(r["Peso_Portfolio_%"])) / 100
                for _, r in df.iterrows()}
    except Exception:
        n = len(ATIVOS_PORTFOLIO_V4)
        return {a: Decimal(1) / n for a in ATIVOS_PORTFOLIO_V4}


# ---------------------------------------------------------------------------
# estado
# ---------------------------------------------------------------------------
def carregar_estado(caminho: str = ESTADO_ARQUIVO) -> dict:
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"posicoes": {}, "vistos": {}}


def salvar_estado(estado: dict, caminho: str = ESTADO_ARQUIVO) -> None:
    """Escrita atômica — mesmo padrão do monitor."""
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, caminho)


# ---------------------------------------------------------------------------
# decisão (puro: sem rede, sem disco — é o que os testes exercitam)
# ---------------------------------------------------------------------------
@dataclass
class Leitura:
    """O que se sabe de um ativo numa rodada."""
    ativo: str
    interval: str
    posicionado: bool
    preco_agora: Decimal
    preco_sinal: Optional[Decimal]      # abertura do candle do sinal (backtest)
    candle_sinal: Optional[str]
    stop: Optional[Decimal]
    filtros: object
    peso: Decimal


def decidir(estado: dict, leituras: list, capital: Decimal = CAPITAL,
            agora: Optional[datetime] = None,
            caminho_parar: Optional[str] = None) -> tuple:
    """Devolve (linhas_do_registro, estado_novo).

    `estado["vistos"]` guarda o último sinal conhecido de cada ativo. Ativo visto
    pela primeira vez é só anotado, sem ordem: é a regra "começa zerado" (3.5).
    """
    agora = agora or datetime.now(timezone.utc).replace(tzinfo=None)
    caminho_parar = caminho_parar or os.path.join(PASTA, "PARAR")
    estado = {"posicoes": dict(estado.get("posicoes", {})),
              "vistos": dict(estado.get("vistos", {}))}
    linhas = []

    # exposição já aberta, para o guardrail de 100% (Spot, sem margem)
    exposicao = sum(Decimal(str(p["valor_usd"])) for p in estado["posicoes"].values())

    for l in leituras:
        anterior = estado["vistos"].get(l.ativo)
        estado["vistos"][l.ativo] = l.posicionado
        if anterior is None or anterior == l.posicionado:
            continue                       # primeira vez, ou nada mudou

        acao = COMPRA if l.posicionado else VENDA
        atraso = None
        if l.candle_sinal:
            atraso = round((agora - pd.Timestamp(l.candle_sinal).to_pydatetime()).total_seconds() / 3600, 2)
        desvio = None
        if l.preco_sinal:
            desvio = round(float((l.preco_agora / l.preco_sinal - 1) * 100), 4)

        linha = {
            "data_hora": agora.strftime("%Y-%m-%d %H:%M:%S"),
            "ativo": l.ativo, "interval": l.interval, "acao": acao,
            "candle_sinal": l.candle_sinal,
            "preco_backtest": l.preco_sinal, "preco_agora": l.preco_agora,
            "desvio_pct": desvio, "atraso_h": atraso,
            "stop": l.stop, "qty": None, "valor_usd": None,
            "status": "", "motivo": "", "exposicao_pct": None,
        }

        if acao == VENDA:
            pos = estado["posicoes"].pop(l.ativo, None)
            if pos is None:
                linha["status"] = "ignorada"
                linha["motivo"] = "sem posicao aberta na sombra"
            else:
                linha["qty"] = pos["qty"]
                linha["valor_usd"] = round(float(Decimal(str(pos["qty"])) * l.preco_agora), 2)
                linha["status"] = "registrada"
                exposicao -= Decimal(str(pos["valor_usd"]))
            linhas.append(linha)
            continue

        # COMPRA — tamanho pelos filtros REAIS da Binance
        alvo = capital * l.peso
        q = calcular_quantidade(alvo, l.preco_agora, l.filtros)
        if not q.aceita:
            linha["status"] = "recusada"
            linha["motivo"] = q.motivo
            linhas.append(linha)
            continue

        valor = q.qty * l.preco_agora
        # A trava de "preco absurdo" NAO entra aqui de proposito. Ela existe para
        # cruzar duas fontes independentes (ticker da corretora x candle) e pegar
        # dado corrompido. Na sombra so ha UMA fonte de preco, e usar o preco do
        # candle do sinal como referencia bloquearia, como se fosse erro de API,
        # todo sinal de um ativo que andou mais de 10% desde o cruzamento --
        # coisa rotineira em memecoin. Ela volta no executor real, com o ticker.
        v = guardrails.avaliar(guardrails.Contexto(
            valor_ordem=valor, teto_por_ordem=capital,
            valor_posicoes=exposicao + valor, capital=capital,
            caminho_parar=caminho_parar,
        ))
        linha["qty"] = float(q.qty)
        linha["valor_usd"] = round(float(valor), 2)
        linha["exposicao_pct"] = round(float((exposicao + valor) / capital * 100), 2)
        if not v.permitido:
            linha["status"] = "bloqueada"
            linha["motivo"] = f"{v.guardrail}: {v.motivo}"
        else:
            linha["status"] = "registrada"
            estado["posicoes"][l.ativo] = {
                "qty": float(q.qty), "preco": float(l.preco_agora),
                "valor_usd": round(float(valor), 2),
                "desde": linha["data_hora"],
            }
            exposicao += valor
        linhas.append(linha)

    return linhas, estado


# ---------------------------------------------------------------------------
# registro
# ---------------------------------------------------------------------------
def gravar(linhas: list, caminho: str = REGISTRO_CSV) -> None:
    if not linhas:
        return
    novo = not os.path.exists(caminho)
    with open(caminho, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUNAS)
        if novo:
            w.writeheader()
        for l in linhas:
            w.writerow({c: l.get(c) for c in COLUNAS})


# ---------------------------------------------------------------------------
# coleta (a parte que fala com a rede)
# ---------------------------------------------------------------------------
def coletar() -> tuple:
    """Lê os 22 ativos e devolve (leituras, falhas). Só endpoints públicos."""
    from binance.client import Client

    cliente = Client(ping=False)
    pesos = carregar_pesos()
    leituras, falhas = [], []
    agora = datetime.now(timezone.utc).replace(tzinfo=None)

    for ativo, interval in ATIVOS_PORTFOLIO_V4.items():
        try:
            df = carregar_dados(ativo, interval, max_idade_horas=0)
            if df is None or df.empty:
                falhas.append((ativo, "sem dados"))
                continue

            ultimo = pd.Timestamp(df["t_abert"].iloc[-1]).to_pydatetime()
            fresco = guardrails.dado_fresco(ultimo, agora)
            if not fresco.permitido:
                falhas.append((ativo, fresco.motivo))
                continue

            params = RECEITA_ROBUSTA[grupo_ouro(ativo)]
            est = estado_posicao_atual(montar_df_fast(df, params), params, slippage=0.0)
            info = cliente.get_symbol_info(ativo)
            leituras.append(Leitura(
                ativo=ativo, interval=interval,
                posicionado=bool(est["posicionado"]),
                preco_agora=Decimal(str(est["preco_atual"])),
                preco_sinal=Decimal(str(est["preco_sinal"])) if est["preco_sinal"] else None,
                candle_sinal=(str(pd.Timestamp(est["data_mudanca"]))
                              if est["data_mudanca"] is not None else None),
                stop=float(est["stop_atual"]) if est["stop_atual"] is not None else None,
                filtros=extrair_filtros(info),
                peso=pesos.get(ativo, Decimal("0.045")),
            ))
        except Exception as e:
            falhas.append((ativo, f"{type(e).__name__}: {e}"))
    return leituras, falhas


def main() -> int:
    carimbo = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    leituras, falhas = coletar()
    print(f"[{carimbo}] sombra: {len(leituras)} ativos lidos, {len(falhas)} falha(s)")
    for a, m in falhas:
        print(f"    {a}: {m}")

    estado = carregar_estado()
    primeira = not estado.get("vistos")
    linhas, estado = decidir(estado, leituras)
    gravar(linhas)
    salvar_estado(estado)

    if primeira:
        print("    primeira rodada: carteira comeca zerada, so sinais novos contam")
    for l in linhas:
        extra = f" ({l['motivo']})" if l["motivo"] else ""
        print(f"    {l['acao']:<5} {l['ativo']:<16} {l['status']:<11}"
              f" desvio {l['desvio_pct']}%{extra}")
    if not linhas:
        print("    nenhuma mudanca de sinal nesta rodada")
    print(f"    posicoes abertas na sombra: {len(estado['posicoes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
