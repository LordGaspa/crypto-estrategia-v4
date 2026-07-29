# -*- coding: utf-8 -*-
# BACKTEST - BREAKOUT DE VOLUME (Scalping) - v1.0
# ----------------------------------------------------------------------------
# Objetivo: testar a lógica de "spike de volume + rompimento de faixa" em
# timeframes curtos, com simulação REALISTA de spread/slippage e taxa,
# para os ativos mais líquidos do portfólio.
#
# Como rodar:
#   pip install python-binance pandas numpy
#   python backtest_breakout_volume.py
#
# O script salva um CSV de ranking por ativo/timeframe/parâmetros, no mesmo
# espírito do seu "Código Ômega - Otimizador", para você me mandar de volta.

import warnings
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from binance.client import Client
from itertools import product

warnings.simplefilter(action="ignore", category=FutureWarning)

# ----------------------------------------------------------------------------
# CONFIGURAÇÃO
# ----------------------------------------------------------------------------
CAPITAL_INICIAL = 1000.0
TAXA_CORRETAGEM = 0.001  # 0.1% por lado (igual ao seu Ômega)
ANOS_DE_DADOS = 2  # timeframe curto não precisa de 8 anos de histórico
# (o mercado muda de regime rápido demais pra isso valer)

# SLIPPAGE REALISTA: em timeframes curtos, o preço de execução real quase
# nunca é igual ao "fechamento" ou "abertura" do candle. Modelamos um
# slippage percentual adicional em cada entrada/saída, pra não inflar
# o resultado como discutimos.
SLIPPAGE_PCT = 0.0005  # 0.05% por operação (ajustável — depende do ativo/exchange)

ATIVOS_LIQUIDOS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

TIMEFRAMES = {
    "1m": Client.KLINE_INTERVAL_1MINUTE,
    "5m": Client.KLINE_INTERVAL_5MINUTE,
    "15m": Client.KLINE_INTERVAL_15MINUTE,
}

# Espaço de parâmetros a testar
PARAMS_GRID = {
    "vol_ma_periodo": [10, 20, 30],  # período da média de volume
    "vol_multiplicador": [
        2.0,
        3.0,
        4.0,
        5.0,
    ],  # quantas vezes acima da média conta como "spike"
    "rompimento_periodo": [
        5,
        10,
        20,
    ],  # candles pra trás pra definir máxima/mínima recente
    "stop_pct": [
        0.003,
        0.005,
        0.008,
        0.01,
    ],  # stop fixo em % (mais previsível que ATR em timeframe curto)
    "alvo_pct": [0.005, 0.01, 0.015, 0.02],  # take-profit fixo em %
}


def carregar_dados(symbol: str, interval: str, anos: int) -> pd.DataFrame:
    client = Client()
    start_date = (datetime.now() - timedelta(days=anos * 365)).strftime("%d %b, %Y")
    try:
        klines = client.get_historical_klines(symbol, interval, start_date)
        if not klines:
            print(f"[AVISO] Sem dados para {symbol} {interval}")
            return pd.DataFrame()
        df = pd.DataFrame(
            klines,
            columns=[
                "t_abert",
                "abertura",
                "maxima",
                "minima",
                "fechamento",
                "volume",
                "t_fech",
                "q_vol",
                "n_trades",
                "tk_b",
                "tk_q",
                "ignore",
            ],
        )
        for col in ["abertura", "maxima", "minima", "fechamento", "volume"]:
            df[col] = pd.to_numeric(df[col])
        df["t_abert"] = pd.to_datetime(df["t_abert"], unit="ms")
        return df[["t_abert", "abertura", "maxima", "minima", "fechamento", "volume"]]
    except Exception as e:
        print(f"[ERRO] {symbol} {interval}: {e}")
        return pd.DataFrame()


def preparar_indicadores(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    d = df.copy()
    d["vol_ma"] = d["volume"].rolling(p["vol_ma_periodo"]).mean()
    d["maxima_recente"] = d["maxima"].rolling(p["rompimento_periodo"]).max().shift(1)
    d["minima_recente"] = d["minima"].rolling(p["rompimento_periodo"]).min().shift(1)
    return d.dropna().reset_index(drop=True)


def executar_backtest(d: pd.DataFrame, p: dict) -> dict:
    """
    Lógica:
    - Spike de volume: volume do candle > vol_multiplicador * media de volume
    - Rompimento de alta: fechamento > máxima dos últimos N candles -> COMPRA
    - Rompimento de baixa: fechamento < mínima dos últimos N candles -> VENDA (short) [não implementado aqui,
      só operamos comprado, igual ao seu sistema atual, pra manter consistência]
    - Stop e alvo em % fixo (mais robusto em timeframe curto que ATR, que fica instável)
    - Slippage aplicado em toda entrada/saída
    """
    volume = d["volume"].values
    vol_ma = d["vol_ma"].values
    fechamento = d["fechamento"].values
    maxima = d["maxima"].values
    minima = d["minima"].values
    maxima_recente = d["maxima_recente"].values
    abertura = d["abertura"].values

    capital = CAPITAL_INICIAL
    posicionado = False
    preco_entrada = 0.0
    stop_price = 0.0
    alvo_price = 0.0
    quantidade = 0.0
    trades = []
    max_capital = CAPITAL_INICIAL
    max_dd = 0.0

    for i in range(1, len(d)):
        if not posicionado:
            spike = volume[i] > (p["vol_multiplicador"] * vol_ma[i])
            rompimento_alta = fechamento[i] > maxima_recente[i]
            if spike and rompimento_alta:
                preco_bruto = (
                    abertura[i] if i + 1 >= len(d) else abertura[min(i + 1, len(d) - 1)]
                )
                preco_entrada = preco_bruto * (1 + SLIPPAGE_PCT)
                quantidade = (capital / preco_entrada) * (1 - TAXA_CORRETAGEM)
                stop_price = preco_entrada * (1 - p["stop_pct"])
                alvo_price = preco_entrada * (1 + p["alvo_pct"])
                posicionado = True
        else:
            idx_exec = min(i + 1, len(d) - 1)
            bateu_stop = minima[i] < stop_price
            bateu_alvo = maxima[i] > alvo_price
            if bateu_stop or bateu_alvo:
                if bateu_stop:
                    preco_saida = stop_price * (1 - SLIPPAGE_PCT)
                else:
                    preco_saida = alvo_price * (1 - SLIPPAGE_PCT)
                capital = (quantidade * preco_saida) * (1 - TAXA_CORRETAGEM)
                trades.append(
                    {
                        "entrada": preco_entrada,
                        "saida": preco_saida,
                        "resultado_pct": (preco_saida - preco_entrada)
                        / preco_entrada
                        * 100.0,
                    }
                )
                posicionado = False

        equity = capital if not posicionado else (quantidade * fechamento[i])
        if equity > max_capital:
            max_capital = equity
        dd = (max_capital - equity) / max_capital if max_capital > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    capital_final = capital if not posicionado else (quantidade * fechamento[-1])
    retorno = ((capital_final - CAPITAL_INICIAL) / CAPITAL_INICIAL) * 100.0
    num_trades = len(trades)
    win_rate = (
        (sum(1 for t in trades if t["resultado_pct"] > 0) / num_trades * 100.0)
        if num_trades > 0
        else 0.0
    )

    # trades por dia (aproximado, baseado no número de candles e timeframe)
    return {
        "retorno_pct": round(retorno, 2),
        "drawdown_pct": round(max_dd * 100.0, 2),
        "num_trades": num_trades,
        "win_rate_pct": round(win_rate, 2),
    }


def main():
    resultados = []
    combinacoes = [
        dict(zip(PARAMS_GRID.keys(), vals)) for vals in product(*PARAMS_GRID.values())
    ]
    print(f"Total de combinações por ativo/timeframe: {len(combinacoes)}")

    for ativo in ATIVOS_LIQUIDOS:
        for tf_str, tf_val in TIMEFRAMES.items():
            print(f"\n>>> Carregando {ativo} {tf_str} ...")
            df_raw = carregar_dados(ativo, tf_val, ANOS_DE_DADOS)
            if df_raw.empty:
                continue

            for idx, p in enumerate(combinacoes):
                d = preparar_indicadores(df_raw, p)
                if len(d) < 50:
                    continue
                res = executar_backtest(d, p)
                # ignora combinações com poucos trades (pouco significativas estatisticamente)
                if res["num_trades"] < 20:
                    continue
                resultados.append(
                    {
                        "ativo": ativo,
                        "timeframe": tf_str,
                        **p,
                        **res,
                    }
                )
                if (idx + 1) % 50 == 0:
                    print(f"   {idx+1}/{len(combinacoes)} combinações testadas...")

    df_resultados = pd.DataFrame(resultados)
    if df_resultados.empty:
        print("Nenhum resultado gerado — confira os dados/parâmetros.")
        return

    df_resultados = df_resultados.sort_values("retorno_pct", ascending=False)
    df_resultados.to_csv("resultados_breakout_volume.csv", index=False)
    print("\n✅ Concluído! Resultados salvos em resultados_breakout_volume.csv")
    print(df_resultados.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
