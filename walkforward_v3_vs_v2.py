# -*- coding: utf-8 -*-
# WALK-FORWARD MULTI-JANELA - COMPARATIVO V3 (trailing stop) x V2 (stop fixo)
# ----------------------------------------------------------------------------
# Script novo e independente. Não altera nada do Código Ômega original, nem o
# otimizador_v2_robustez.py, nem o walkforward_validacao.py.
#
# Roda o MESMO processo de walk-forward multi-janela (janelas expanding,
# ancoradas em ano civil, mesma função geradora de janelas do
# walkforward_validacao.py) só que usando o motor de backtest do
# otimizador_v3_trailing.py (trailing stop em 2 fases) em vez do v2 (stop fixo
# + saída por cruzamento contrário).
#
# No final, junta os resultados de teste (fora da amostra) do v3 com os do v2
# que já estão salvos em walkforward_janelas_<ATIVO>_4h.csv (gerados
# anteriormente pelo walkforward_validacao.py) e com o buy & hold, numa
# tabela comparativa lado a lado.
#
# Pré-requisito: já ter rodado walkforward_validacao.py para os mesmos ativos
# (usa os CSVs walkforward_janelas_<ATIVO>_4h.csv como referência do v2).
#
# Como rodar:
#   python walkforward_v3_vs_v2.py

import os
import sys
import glob
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

TREINO_INICIAL_ANOS = 3  # igual ao walkforward_validacao.py, pra gerar as MESMAS janelas
JANELA_TESTE_ANOS = 1
MIN_CANDLES_TESTE = 60

ATIVOS = [
    "FETUSDT",
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "BNBUSDT",
    "TRXUSDT",
]
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
    """Entrada e stop inicial iguais ao v2. Saída: trailing stop (chandelier)
    em 2 fases a partir de 1x o risco inicial; cruzamento contrário não fecha
    mais posição."""
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


def buy_and_hold(df_arrays: dict, inicio: int, fim: int) -> float:
    abertura = df_arrays["abertura"][inicio:fim]
    fechamento = df_arrays["fechamento"][inicio:fim]
    if len(abertura) < 2 or abertura[0] <= 0:
        return 0.0
    return round(((fechamento[-1] - abertura[0]) / abertura[0]) * 100.0, 2)


_WORKER_DF_FAST = None
_WORKER_FIM_TREINO = None


def _init_worker(df_fast, fim_treino):
    global _WORKER_DF_FAST, _WORKER_FIM_TREINO
    _WORKER_DF_FAST = df_fast
    _WORKER_FIM_TREINO = fim_treino


def _worker_treino(p: dict) -> dict:
    res = executar_backtest_v3(_WORKER_DF_FAST, p, 0, _WORKER_FIM_TREINO)
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


def gerar_janelas(datas: pd.Series) -> list:
    """Idêntico ao gerar_janelas do walkforward_validacao.py — garante que as
    janelas do v3 caiam exatamente nos mesmos cortes de calendário do v2."""
    datas = datas.reset_index(drop=True)
    n_total = len(datas)
    data_min, data_max = datas.iloc[0], datas.iloc[-1]
    corte = data_min.year + TREINO_INICIAL_ANOS

    janelas = []
    while corte <= data_max.year:
        treino_fim_data = pd.Timestamp(year=corte, month=1, day=1)
        teste_fim_data = pd.Timestamp(year=corte + JANELA_TESTE_ANOS, month=1, day=1)

        idx_treino_fim = int(datas.searchsorted(treino_fim_data))
        idx_teste_fim = min(int(datas.searchsorted(teste_fim_data)), n_total)

        if idx_treino_fim >= n_total - 1:
            break
        if (idx_teste_fim - idx_treino_fim) < MIN_CANDLES_TESTE:
            break

        janelas.append(
            {
                "treino_fim_idx": idx_treino_fim,
                "teste_fim_idx": idx_teste_fim,
                "treino_periodo": f"{data_min.date()} a {(treino_fim_data - pd.Timedelta(days=1)).date()}",
                "teste_periodo": f"{treino_fim_data.date()} a {datas.iloc[idx_teste_fim - 1].date()}",
            }
        )

        if idx_teste_fim >= n_total:
            break
        corte += JANELA_TESTE_ANOS

    return janelas


def carregar_v2(ativo: str) -> pd.DataFrame:
    candidatos = glob.glob(f"walkforward_janelas_{ativo}_*.csv")
    if not candidatos:
        return pd.DataFrame()
    return pd.read_csv(candidatos[0])


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
    print(f"Combinações por janela: {len(comb_base)} | Núcleos: {n_cores}")

    linhas_todos = []

    for ativo in ATIVOS:
        for t_str in TIMEFRAMES_A_TESTAR:
            t_val = TIMEFRAME_MAP[t_str]
            print(f"\n{'=' * 70}\n>>> {ativo} - {t_str}: carregando dados...")
            df = carregar_dados(ativo, t_val)
            if df.empty:
                print(f"    [AVISO] sem dados para {ativo}, pulando.")
                continue

            df_v2 = carregar_v2(ativo)
            if df_v2.empty:
                print(f"    [AVISO] sem CSV do v2 para {ativo} — rode walkforward_validacao.py antes. Pulando.")
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
            janelas = gerar_janelas(df["t_abert"])
            if not janelas:
                print(f"    [AVISO] histórico insuficiente para gerar janelas ({ativo}).")
                continue
            if len(janelas) != len(df_v2):
                print(
                    f"    [AVISO] número de janelas do v3 ({len(janelas)}) difere do "
                    f"CSV do v2 ({len(df_v2)}) para {ativo} — o merge usará a interseção por índice."
                )
            print(f"    Total de candles: {n_total} | {len(janelas)} janela(s) de teste.")

            for i, jan in enumerate(janelas, start=1):
                fim_treino = jan["treino_fim_idx"]
                fim_teste = jan["teste_fim_idx"]
                print(
                    f"\n    [Janela {i}/{len(janelas)}] Treino: {jan['treino_periodo']} "
                    f"| Teste: {jan['teste_periodo']}"
                )
                print(f"        Rodando grid search v3 no treino ({fim_treino} candles)...")

                resultados_treino = []
                with ProcessPoolExecutor(
                    max_workers=n_cores,
                    initializer=_init_worker,
                    initargs=(df_fast, fim_treino),
                ) as executor:
                    for saida in executor.map(_worker_treino, comb_base, chunksize=200):
                        p, res = saida["p"], saida["res"]
                        if res["num_trades"] < 5:
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
                                "multiplicador_trailing": p["multiplicador_trailing"],
                            }
                        )

                df_treino = pd.DataFrame(resultados_treino)
                if df_treino.empty:
                    print("        Sem resultados suficientes no treino, pulando janela.")
                    continue

                uniques = {c: sorted(df_treino[c].unique()) for c in idx_cols}
                idx_maps = {c: {v: i for i, v in enumerate(uniques[c])} for c in idx_cols}
                for c in idx_cols:
                    df_treino[f"_idx_{c}"] = df_treino[c].map(idx_maps[c])
                df_treino["Score_Robustez_Treino"] = calcular_score_robustez(
                    df_treino, [f"_idx_{c}" for c in idx_cols], "Lucro_Treino"
                )

                melhor = df_treino.sort_values(
                    "Score_Robustez_Treino", ascending=False
                ).iloc[0]

                p_exec = dict(
                    media_rapida=int(melhor["media_rapida_per"]),
                    media_lenta=int(melhor["media_lenta_per"]),
                    media_filtro=int(melhor["media_filtro_tendencia_per"]),
                    atr_periodo=int(melhor["atr_periodo"]),
                    atr_multiplicador=float(melhor["atr_multiplicador"]),
                    multiplicador_trailing=float(melhor["multiplicador_trailing"]),
                )
                res_teste = executar_backtest_v3(df_fast, p_exec, fim_treino, fim_teste)
                bh = buy_and_hold(df_fast, fim_treino, fim_teste)

                v2_row = df_v2.iloc[i - 1] if i - 1 < len(df_v2) else None

                linha = {
                    "Ativo": ativo,
                    "Timeframe": t_str,
                    "Janela": i,
                    "Periodo_Treino": jan["treino_periodo"],
                    "Periodo_Teste": jan["teste_periodo"],
                    "media_rapida_per_v3": p_exec["media_rapida"],
                    "media_lenta_per_v3": p_exec["media_lenta"],
                    "media_filtro_tendencia_per_v3": p_exec["media_filtro"],
                    "atr_periodo_v3": p_exec["atr_periodo"],
                    "atr_multiplicador_v3": p_exec["atr_multiplicador"],
                    "multiplicador_trailing_v3": p_exec["multiplicador_trailing"],
                    "Lucro_V2_TESTE_%": v2_row["Lucro_Estrategia_TESTE_%"] if v2_row is not None else None,
                    "Lucro_V3_TESTE_%": res_teste["retorno"],
                    "Lucro_BuyHold_TESTE_%": bh,
                    "DD_V2_TESTE_%": v2_row["DD_TESTE_%"] if v2_row is not None else None,
                    "DD_V3_TESTE_%": res_teste["drawdown"],
                    "Trades_V2_TESTE": v2_row["Trades_TESTE"] if v2_row is not None else None,
                    "Trades_V3_TESTE": res_teste["num_trades"],
                }
                linhas_todos.append(linha)
                print(
                    f"        V2: {linha['Lucro_V2_TESTE_%']}% (DD {linha['DD_V2_TESTE_%']}%) | "
                    f"V3: {res_teste['retorno']:.2f}% (DD {res_teste['drawdown']:.2f}%) | "
                    f"Buy&Hold: {bh:.2f}%"
                )

    if linhas_todos:
        df_comp = pd.DataFrame(linhas_todos)
        df_comp.to_csv("comparativo_v2_vs_v3_walkforward.csv", index=False)
        print(
            f"\n✅ Salvo: comparativo_v2_vs_v3_walkforward.csv ({len(df_comp)} linhas)\n"
        )
        print(df_comp.to_string(index=False))


if __name__ == "__main__":
    main()
