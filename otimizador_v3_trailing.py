# -*- coding: utf-8 -*-
# OTIMIZADOR V3 - TRAILING STOP (CHANDELIER) NA SAÍDA
# ----------------------------------------------------------------------------
# Evolução do otimizador_v2_robustez.py. Não altera o v2 — este é um arquivo
# novo e independente. Também não toca em nada do Código Ômega original.
#
# O que muda em relação ao v2:
#   - Entrada: igual (cruzamento de médias rápida/lenta + filtro de tendência).
#   - Stop inicial: igual (ATR do candle anterior à entrada × atr_multiplicador).
#   - Saída por cruzamento de médias contrário: REMOVIDA. O cruzamento
#     contrário não fecha mais a posição — ele só continua relevante como
#     parte do próprio gatilho de entrada (que já exige um cruzamento de alta
#     recente, então naturalmente não entra "contra a tendência" enquanto a
#     média rápida seguir abaixo da lenta).
#   - Saída por stop: agora tem 2 fases.
#       Fase 1 (antes de 1x o risco inicial de lucro): stop fixo, igual ao v2.
#       Fase 2 (a partir de 1x o risco inicial de lucro flutuante, medido pela
#       máxima atingida desde a entrada): stop viradas trailing (chandelier),
#       recalculado a cada candle como
#           máxima atingida desde a entrada  -  (ATR do candle anterior × multiplicador_trailing)
#       O stop só pode subir, nunca descer.
#   - Novo parâmetro no grid: multiplicador_trailing.
#
# Roda a mesma varredura + score de robustez do v2, no histórico completo
# (sem split treino/teste — para isso, use o walkforward_v3_vs_v2.py).
#
# Como rodar:
#   pip install python-binance pandas numpy pyarrow
#   python otimizador_v3_trailing.py

import os
import sys
import warnings
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from binance.client import Client
from itertools import product
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

warnings.simplefilter(action="ignore", category=FutureWarning)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

CAPITAL_INICIAL = 1000.0
TAXA_CORRETAGEM = 0.001
ANOS_DE_DADOS_BACKTEST = 8
CACHE_DIR = "cache_dados"
CACHE_VALIDADE_HORAS = 24

ATIVOS = ["FETUSDT"]  # ajuste aqui
TIMEFRAMES_A_TESTAR = ["4h"]

TIMEFRAME_MAP = {
    "4h": Client.KLINE_INTERVAL_4HOUR,
    "6h": Client.KLINE_INTERVAL_6HOUR,
    "8h": Client.KLINE_INTERVAL_8HOUR,
    "12h": Client.KLINE_INTERVAL_12HOUR,
    "1d": Client.KLINE_INTERVAL_1DAY,
}

PARAMS_TEST = {
    "media_rapida": [5, 7, 8, 9, 10, 12, 14, 15, 18, 21],
    "media_lenta": [20, 30, 40, 50, 80, 100, 120, 150, 200],
    "media_filtro": [50, 100, 150, 200, 250],
    "atr_periodo": [5, 7, 10, 14, 20, 25, 30],
    "atr_multiplicador": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0],
    "multiplicador_trailing": [1.0, 1.5, 2.0, 3.0, 4.0],
}
PESOS_DISTANCIA = {1: 1.0, 2: 0.5, 3: 0.25}


def carregar_dados(symbol: str, interval: str) -> pd.DataFrame:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{symbol}_{interval}.parquet")
    if os.path.exists(cache_path):
        idade_horas = (datetime.now().timestamp() - os.path.getmtime(cache_path)) / 3600
        if idade_horas < CACHE_VALIDADE_HORAS:
            try:
                df_cache = pd.read_parquet(cache_path)
                if "t_abert" in df_cache.columns:
                    return df_cache
            except Exception:
                pass
    client = Client()
    start_date = (
        datetime.now() - timedelta(days=ANOS_DE_DADOS_BACKTEST * 365)
    ).strftime("%d %b, %Y")
    try:
        klines = client.get_historical_klines(symbol, interval, start_date)
        if not klines:
            return pd.DataFrame()
        df = pd.DataFrame(
            klines,
            columns=[
                "t_abert",
                "abertura",
                "maxima",
                "minima",
                "fechamento",
                "vol",
                "t_fech",
                "q_vol",
                "n_trades",
                "tk_b",
                "tk_q",
                "ignore",
            ],
        )
        for col in ["abertura", "maxima", "minima", "fechamento"]:
            df[col] = pd.to_numeric(df[col])
        df["t_abert"] = pd.to_datetime(df["t_abert"], unit="ms")
        df = df[["t_abert", "abertura", "maxima", "minima", "fechamento"]]
        try:
            df.to_parquet(cache_path)
        except Exception as e:
            print(f"[AVISO] cache não salvo: {e}")
        return df
    except Exception as e:
        print(f"[ERRO] {symbol} {interval}: {e}")
        return pd.DataFrame()


def executar_backtest_v3(df_arrays: dict, p: dict, inicio: int, fim: int) -> dict:
    """Igual ao v2 na entrada e no stop inicial. Na saída, troca o cruzamento
    contrário por um trailing stop (chandelier) em 2 fases, ativado quando o
    lucro flutuante (medido pela máxima desde a entrada) atinge 1x o risco
    inicial do trade."""
    m_rapida = df_arrays[f"ma_{p['media_rapida']}"][inicio:fim]
    m_lenta = df_arrays[f"ma_{p['media_lenta']}"][inicio:fim]
    m_filtro = df_arrays[f"ma_f_{p['media_filtro']}"][inicio:fim]
    abertura = df_arrays["abertura"][inicio:fim]
    maxima = df_arrays["maxima"][inicio:fim]
    minima = df_arrays["minima"][inicio:fim]
    fechamento = df_arrays["fechamento"][inicio:fim]
    atr = df_arrays[f"atr_{p['atr_periodo']}"][inicio:fim]
    multi_atr = p["atr_multiplicador"]
    multi_trailing = p["multiplicador_trailing"]

    if len(fechamento) < 5:
        return {"retorno": 0.0, "drawdown": 0.0, "num_trades": 0}

    sinais_compra = np.zeros_like(m_rapida, dtype=bool)
    sinais_compra[1:] = (
        (m_rapida[1:] > m_lenta[1:])
        & (m_rapida[:-1] <= m_lenta[:-1])
        & (fechamento[1:] > m_filtro[1:])
    )
    # Cruzamento contrário (m_rapida < m_lenta) NÃO fecha mais posição — ele só
    # existe implicitamente como parte do gatilho de entrada acima, que exige
    # um cruzamento de alta recente (então não há entrada nova enquanto a
    # rápida seguir abaixo da lenta).

    capital, posicionado, max_capital, max_dd = (
        CAPITAL_INICIAL,
        False,
        CAPITAL_INICIAL,
        0.0,
    )
    quantidade_ativo, num_trades = 0.0, 0
    preco_compra = 0.0
    stop_loss_price = 0.0
    risco_inicial = 0.0
    max_preco_desde_entrada = 0.0
    trailing_ativo = False

    for i in range(1, len(fechamento)):
        if not posicionado and sinais_compra[i - 1]:
            preco_compra = abertura[i]
            if preco_compra > 0:
                quantidade_ativo = (capital / preco_compra) * (1 - TAXA_CORRETAGEM)
                posicionado = True
                stop_loss_price = preco_compra - (atr[i - 1] * multi_atr)
                risco_inicial = preco_compra - stop_loss_price
                max_preco_desde_entrada = preco_compra
                trailing_ativo = False
        elif posicionado:
            if maxima[i - 1] > max_preco_desde_entrada:
                max_preco_desde_entrada = maxima[i - 1]

            if not trailing_ativo and risco_inicial > 0:
                if (max_preco_desde_entrada - preco_compra) >= risco_inicial:
                    trailing_ativo = True

            if trailing_ativo:
                novo_stop = max_preco_desde_entrada - (atr[i - 1] * multi_trailing)
                if novo_stop > stop_loss_price:
                    stop_loss_price = novo_stop

            if minima[i] < stop_loss_price:
                preco_saida = min(stop_loss_price, abertura[i])
                capital = (quantidade_ativo * preco_saida) * (1 - TAXA_CORRETAGEM)
                posicionado = False
                num_trades += 1

            equity = capital if not posicionado else (quantidade_ativo * minima[i])
            if equity > max_capital:
                max_capital = equity
            dd = (max_capital - equity) / max_capital if max_capital > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

    capital_final = capital if not posicionado else (quantidade_ativo * fechamento[-1])
    retorno = ((capital_final - CAPITAL_INICIAL) / CAPITAL_INICIAL) * 100.0
    return {
        "retorno": round(retorno, 2),
        "drawdown": round(max_dd * 100.0, 2),
        "num_trades": num_trades,
    }


_WORKER_DF_FAST = None


def _init_worker(df_fast):
    global _WORKER_DF_FAST
    _WORKER_DF_FAST = df_fast


def _worker_full(p: dict) -> dict:
    res = executar_backtest_v3(_WORKER_DF_FAST, p, 0, len(_WORKER_DF_FAST["fechamento"]))
    return {"p": p, "res": res}


def calcular_score_robustez(d: pd.DataFrame, cols_idx: list, lucro_col: str) -> np.ndarray:
    keys = list(zip(*(d[c] for c in cols_idx)))
    lucros = d[lucro_col].values
    lookup = dict(zip(keys, lucros))
    scores = np.empty(len(d))
    for i, key in enumerate(keys):
        soma_pesos, soma_ponderada = 0.0, 0.0
        for dim in range(len(cols_idx)):
            for dist, peso in PESOS_DISTANCIA.items():
                for sinal in (-1, 1):
                    k2 = list(key)
                    k2[dim] += sinal * dist
                    k2 = tuple(k2)
                    if k2 in lookup:
                        soma_ponderada += lookup[k2] * peso
                        soma_pesos += peso
        scores[i] = (soma_ponderada / soma_pesos) if soma_pesos > 0 else np.nan
    return scores


def main():
    comb_base = [
        dict(
            media_rapida=mr,
            media_lenta=ml,
            media_filtro=mf,
            atr_periodo=ap,
            atr_multiplicador=am,
            multiplicador_trailing=mt,
        )
        for mr, ml, mf, ap, am, mt in product(
            PARAMS_TEST["media_rapida"],
            PARAMS_TEST["media_lenta"],
            PARAMS_TEST["media_filtro"],
            PARAMS_TEST["atr_periodo"],
            PARAMS_TEST["atr_multiplicador"],
            PARAMS_TEST["multiplicador_trailing"],
        )
        if ml > mr
    ]
    idx_cols = [
        "media_rapida_per",
        "media_lenta_per",
        "media_filtro_tendencia_per",
        "atr_periodo",
        "atr_multiplicador",
        "multiplicador_trailing",
    ]
    n_cores = multiprocessing.cpu_count()
    print(f"Combinações: {len(comb_base)} | Núcleos: {n_cores}")

    for ativo in ATIVOS:
        for t_str in TIMEFRAMES_A_TESTAR:
            t_val = TIMEFRAME_MAP[t_str]
            print(f"\n>>> {ativo} - {t_str}: carregando dados...")
            df = carregar_dados(ativo, t_val)
            if df.empty:
                continue

            df_fast = {
                "abertura": df["abertura"].values,
                "maxima": df["maxima"].values,
                "minima": df["minima"].values,
                "fechamento": df["fechamento"].values,
            }
            for m in set(PARAMS_TEST["media_rapida"] + PARAMS_TEST["media_lenta"]):
                df_fast[f"ma_{m}"] = df["fechamento"].rolling(m).mean().values
            for mf in set(PARAMS_TEST["media_filtro"]):
                df_fast[f"ma_f_{mf}"] = df["fechamento"].rolling(mf).mean().values
            tr = pd.concat(
                [
                    df["maxima"] - df["minima"],
                    (df["maxima"] - df["fechamento"].shift()).abs(),
                    (df["minima"] - df["fechamento"].shift()).abs(),
                ],
                axis=1,
            ).max(axis=1)
            for pa in set(PARAMS_TEST["atr_periodo"]):
                df_fast[f"atr_{pa}"] = tr.rolling(pa).mean().values

            n_total = len(df_fast["fechamento"])
            print(f"    Total de candles: {n_total}")

            resultados = []
            with ProcessPoolExecutor(
                max_workers=n_cores, initializer=_init_worker, initargs=(df_fast,)
            ) as executor:
                for saida in executor.map(_worker_full, comb_base, chunksize=200):
                    p, res = saida["p"], saida["res"]
                    if res["num_trades"] < 5:
                        continue
                    resultados.append(
                        {
                            "Tempo": t_str,
                            "Lucro (%)": res["retorno"],
                            "DD (%)": res["drawdown"],
                            "Num_Trades": res["num_trades"],
                            "media_rapida_per": p["media_rapida"],
                            "media_lenta_per": p["media_lenta"],
                            "media_filtro_tendencia_per": p["media_filtro"],
                            "atr_periodo": p["atr_periodo"],
                            "atr_multiplicador": p["atr_multiplicador"],
                            "multiplicador_trailing": p["multiplicador_trailing"],
                        }
                    )

            df_res = pd.DataFrame(resultados)
            if df_res.empty:
                print("    Sem resultados suficientes.")
                continue

            uniques = {c: sorted(df_res[c].unique()) for c in idx_cols}
            idx_maps = {c: {v: i for i, v in enumerate(uniques[c])} for c in idx_cols}
            for c in idx_cols:
                df_res[f"_idx_{c}"] = df_res[c].map(idx_maps[c])
            df_res["Score_Robustez"] = calcular_score_robustez(
                df_res, [f"_idx_{c}" for c in idx_cols], "Lucro (%)"
            )
            df_res = df_res.drop(columns=[f"_idx_{c}" for c in idx_cols])

            df_res["Rank_Lucro"] = df_res["Lucro (%)"].rank(ascending=False, method="min").astype(int)
            df_res["Rank_Robustez"] = df_res["Score_Robustez"].rank(ascending=False, method="min").astype(int)
            df_res = df_res.sort_values("Score_Robustez", ascending=False)

            nome_arquivo = f"otimizador_v3_trailing_{ativo}.csv"
            df_res.to_csv(nome_arquivo, index=False)
            print(f"\n✅ Salvo: {nome_arquivo}\n")
            print(df_res.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
