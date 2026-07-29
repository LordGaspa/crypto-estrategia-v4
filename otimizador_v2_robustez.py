# -*- coding: utf-8 -*-
# WALK-FORWARD - VALIDAÇÃO FORA DA AMOSTRA
# ----------------------------------------------------------------------------
# Este é um script NOVO e independente (não altera nada do que já existe).
#
# O que ele faz:
#   1. Divide o histórico em duas partes, em ORDEM CRONOLÓGICA (sem embaralhar):
#      - TREINO: primeira fração dos dados (ex: 75%)
#      - TESTE: fração final, mais recente (ex: 25%) — o otimizador NUNCA vê
#        essa parte ao escolher o parâmetro.
#   2. Roda a mesma varredura + score de robustez (do otimizador v2) SÓ no
#      período de TREINO.
#   3. Pega o(s) parâmetro(s) mais robusto(s) do TREINO e aplica ELES, sem
#      qualquer reajuste, no período de TESTE.
#   4. Compara: retorno no treino vs retorno no teste. Se o parâmetro é
#      genuinamente bom (não só decorou o passado), o teste deve continuar
#      positivo — mesmo que menor. Se o teste desabar ou virar negativo, é
#      sinal de que o treino estava ajustado ao próprio histórico (overfitting).
#
# Também compara contra o "campeão de lucro puro" do treino, pra você ver lado
# a lado se escolher por robustez realmente generaliza melhor.
#
# Como rodar:
#   pip install python-binance pandas numpy pyarrow
#   python walkforward_validacao.py

import os
import warnings
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from binance.client import Client
from itertools import product
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

warnings.simplefilter(action="ignore", category=FutureWarning)

CAPITAL_INICIAL = 1000.0
TAXA_CORRETAGEM = 0.001
ANOS_DE_DADOS_BACKTEST = 8
CACHE_DIR = "cache_dados"
CACHE_VALIDADE_HORAS = 24

FRACAO_TREINO = 0.75  # 75% treino / 25% teste, em ordem cronológica

ATIVOS = ["FETUSDT"]  # ajuste aqui
TIMEFRAMES_A_TESTAR = ["4h"]  # comece só com o timeframe já validado; expanda depois

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
}
PESOS_DISTANCIA = {1: 1.0, 2: 0.5, 3: 0.25}
TOP_N_PARA_VALIDAR = 5  # quantos "campeões" do treino testar no período de teste


def carregar_dados(symbol: str, interval: str) -> pd.DataFrame:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{symbol}_{interval}.parquet")
    if os.path.exists(cache_path):
        idade_horas = (datetime.now().timestamp() - os.path.getmtime(cache_path)) / 3600
        if idade_horas < CACHE_VALIDADE_HORAS:
            try:
                return pd.read_parquet(cache_path)
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
        df = df[["abertura", "maxima", "minima", "fechamento"]]
        try:
            df.to_parquet(cache_path)
        except Exception as e:
            print(f"[AVISO] cache não salvo: {e}")
        return df
    except Exception as e:
        print(f"[ERRO] {symbol} {interval}: {e}")
        return pd.DataFrame()


def executar_backtest_intervalo(
    df_arrays: dict, p: dict, inicio: int, fim: int
) -> dict:
    """Roda o backtest só no intervalo [inicio:fim) do array, com capital fresco."""
    m_rapida = df_arrays[f"ma_{p['media_rapida']}"][inicio:fim]
    m_lenta = df_arrays[f"ma_{p['media_lenta']}"][inicio:fim]
    m_filtro = df_arrays[f"ma_f_{p['media_filtro']}"][inicio:fim]
    abertura = df_arrays["abertura"][inicio:fim]
    minima = df_arrays["minima"][inicio:fim]
    fechamento = df_arrays["fechamento"][inicio:fim]
    atr = df_arrays[f"atr_{p['atr_periodo']}"][inicio:fim]
    multi_atr = p["atr_multiplicador"]

    if len(fechamento) < 5:
        return {"retorno": 0.0, "drawdown": 0.0, "num_trades": 0}

    sinais_compra = np.zeros_like(m_rapida, dtype=bool)
    sinais_compra[1:] = (
        (m_rapida[1:] > m_lenta[1:])
        & (m_rapida[:-1] <= m_lenta[:-1])
        & (fechamento[1:] > m_filtro[1:])
    )
    sinais_venda = np.zeros_like(m_rapida, dtype=bool)
    sinais_venda[1:] = (m_rapida[1:] < m_lenta[1:]) & (m_rapida[:-1] >= m_lenta[:-1])

    capital, posicionado, max_capital, max_dd = (
        CAPITAL_INICIAL,
        False,
        CAPITAL_INICIAL,
        0.0,
    )
    quantidade_ativo, stop_loss_price, num_trades = 0.0, 0.0, 0

    for i in range(1, len(fechamento)):
        if not posicionado and sinais_compra[i - 1]:
            preco_compra = abertura[i]
            if preco_compra > 0:
                quantidade_ativo = (capital / preco_compra) * (1 - TAXA_CORRETAGEM)
                posicionado = True
                stop_loss_price = preco_compra - (atr[i - 1] * multi_atr)
        elif posicionado:
            if minima[i] < stop_loss_price or sinais_venda[i - 1]:
                preco_saida = (
                    min(stop_loss_price, abertura[i])
                    if minima[i] < stop_loss_price
                    else abertura[i]
                )
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
_WORKER_FIM_TREINO = None


def _init_worker(df_fast, fim_treino):
    global _WORKER_DF_FAST, _WORKER_FIM_TREINO
    _WORKER_DF_FAST = df_fast
    _WORKER_FIM_TREINO = fim_treino


def _worker_treino(p: dict) -> dict:
    res = executar_backtest_intervalo(_WORKER_DF_FAST, p, 0, _WORKER_FIM_TREINO)
    return {"p": p, "res": res}


def calcular_score_robustez(
    d: pd.DataFrame, cols_idx: list, lucro_col: str
) -> np.ndarray:
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
        )
        for mr, ml, mf, ap, am in product(
            PARAMS_TEST["media_rapida"],
            PARAMS_TEST["media_lenta"],
            PARAMS_TEST["media_filtro"],
            PARAMS_TEST["atr_periodo"],
            PARAMS_TEST["atr_multiplicador"],
        )
        if ml > mr
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
            fim_treino = int(n_total * FRACAO_TREINO)
            data_corte = df["abertura"].index  # apenas para referência de tamanho
            print(
                f"    Total de candles: {n_total} | Treino: 0-{fim_treino} | Teste: {fim_treino}-{n_total}"
            )

            # ---- 1) Rodar grid search SÓ no treino ----
            resultados_treino = []
            print("    Rodando grid search no período de TREINO...")
            with ProcessPoolExecutor(
                max_workers=n_cores,
                initializer=_init_worker,
                initargs=(df_fast, fim_treino),
            ) as executor:
                for saida in executor.map(_worker_treino, comb_base, chunksize=200):
                    p, res = saida["p"], saida["res"]
                    if res["num_trades"] < 5:  # muito poucos trades no treino: ignora
                        continue
                    resultados_treino.append(
                        {
                            "Lucro_Treino": res["retorno"],
                            "Trades_Treino": res["num_trades"],
                            "media_rapida_per": p["media_rapida"],
                            "media_lenta_per": p["media_lenta"],
                            "media_filtro_tendencia_per": p["media_filtro"],
                            "atr_periodo": p["atr_periodo"],
                            "atr_multiplicador": p["atr_multiplicador"],
                        }
                    )

            df_treino = pd.DataFrame(resultados_treino)
            if df_treino.empty:
                print("    Sem resultados suficientes no treino.")
                continue

            idx_cols = [
                "media_rapida_per",
                "media_lenta_per",
                "media_filtro_tendencia_per",
                "atr_periodo",
                "atr_multiplicador",
            ]
            uniques = {c: sorted(df_treino[c].unique()) for c in idx_cols}
            idx_maps = {c: {v: i for i, v in enumerate(uniques[c])} for c in idx_cols}
            for c in idx_cols:
                df_treino[f"_idx_{c}"] = df_treino[c].map(idx_maps[c])
            df_treino["Score_Robustez_Treino"] = calcular_score_robustez(
                df_treino, [f"_idx_{c}" for c in idx_cols], "Lucro_Treino"
            )

            top_robustos = df_treino.sort_values(
                "Score_Robustez_Treino", ascending=False
            ).head(TOP_N_PARA_VALIDAR)
            top_lucro_puro = df_treino.sort_values(
                "Lucro_Treino", ascending=False
            ).head(TOP_N_PARA_VALIDAR)

            # ---- 2) Validar os campeões no período de TESTE (fora da amostra) ----
            print(
                "    Validando campeões no período de TESTE (dados nunca vistos pela otimização)..."
            )
            linhas_finais = []
            for origem, tabela in [
                ("Robustez", top_robustos),
                ("Lucro_Puro", top_lucro_puro),
            ]:
                for _, row in tabela.iterrows():
                    p = {c: row[c] for c in idx_cols}
                    p_exec = dict(
                        media_rapida=p["media_rapida_per"],
                        media_lenta=p["media_lenta_per"],
                        media_filtro=p["media_filtro_tendencia_per"],
                        atr_periodo=p["atr_periodo"],
                        atr_multiplicador=p["atr_multiplicador"],
                    )
                    res_teste = executar_backtest_intervalo(
                        df_fast, p_exec, fim_treino, n_total
                    )
                    linhas_finais.append(
                        {
                            "Selecionado_por": origem,
                            "Lucro_Treino_%": row["Lucro_Treino"],
                            "Lucro_TESTE_%": res_teste["retorno"],
                            "Trades_Treino": row["Trades_Treino"],
                            "Trades_TESTE": res_teste["num_trades"],
                            "DD_TESTE_%": res_teste["drawdown"],
                            **p,
                        }
                    )

            df_final = pd.DataFrame(linhas_finais)
            nome_arquivo = f"walkforward_{ativo}_{t_str}.csv"
            df_final.to_csv(nome_arquivo, index=False)
            print(f"\n✅ Salvo: {nome_arquivo}\n")
            print(df_final.to_string(index=False))


if __name__ == "__main__":
    main()
