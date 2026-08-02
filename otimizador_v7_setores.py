# -*- coding: utf-8 -*-
# OTIMIZADOR V7 -- grade de parametros por ativo, TIMEFRAME UNIFORME
# ----------------------------------------------------------------------------
# Roda a MESMA grade do otimizador_v4.py (mesmas combinacoes, mesmo motor,
# mesmos custos por liquidez), com duas diferencas deliberadas:
#
#   1. TIMEFRAME UNIFORME pra todos os ativos. Os CSVs do v4 usam o interval
#      de producao de cada ativo (4h/6h/8h/12h/1d, herdado do Codigo Omega).
#      Isso e correto pra operar, mas ATRAPALHA a analise setorial: "media
#      lenta = 100" em 4h nao e a mesma coisa que em 12h, entao comparar a
#      frequencia de parametros entre ativos de intervals diferentes mistura
#      duas coisas. Aqui todos rodam no MESMO interval.
#   2. Universo expandido (setores_v7.UNIVERSO_V7) -- inclui ativos novos
#      escolhidos por maior capitalizacao dentro de cada setor, pra que cada
#      setor tenha ativos suficientes pra a analise ter poder estatistico.
#
# NAO altera nada do v4/v5 em producao. So gera CSVs novos, com prefixo
# otimizador_v7_.
#
# So periodo de DESENVOLVIMENTO -- holdout LACRADO.
#
# Como rodar:
#   .venv\Scripts\python.exe otimizador_v7_setores.py            (todos)
#   .venv\Scripts\python.exe otimizador_v7_setores.py BTCUSDT    (so um, pra testar)

import os
import sys
import time
import warnings
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from itertools import product

import numpy as np
import pandas as pd

from config_v4 import (
    CANDLES_POR_DIA, carregar_dados, separar_periodos, classificar_liquidez,
)
from otimizador_v4 import (
    PARAMS_TEST, MIN_TRADES, MIN_CANDLES_DEV, PESOS_DISTANCIA,
    _init_worker, _worker_treino, calcular_score_robustez,
)

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# Timeframes testados. O usuario quer o TIMEFRAME como mais uma dimensao do
# padrao setorial (hipotese: memecoin pode preferir timeframe mais rapido que
# L1). Abaixo de 4h nao entra: a Fase 1 do day-trade ja mostrou que o custo
# come o edge em horizontes curtos (ver RELATORIO_DAYTRADE_FASE1.md).
TIMEFRAMES_V7 = ["4h", "6h", "8h", "12h"]
PREFIXO = "otimizador_v7_"


def rodar_ativo(ativo: str, interval_str: str, comb_base: list, idx_cols: list, n_cores: int) -> dict | None:
    t0 = time.time()
    info_liq = classificar_liquidez(ativo)

    df = carregar_dados(ativo, interval_str)
    if df.empty:
        print(f"    [SEM DADOS] {ativo}")
        return None

    periodos = separar_periodos(df["t_abert"])
    idx_dev_fim = periodos["idx_dev_fim"]
    if idx_dev_fim < MIN_CANDLES_DEV:
        print(f"    [CURTO] {ativo}: {idx_dev_fim} candles de dev (min {MIN_CANDLES_DEV})")
        return None

    df_dev = df.iloc[:idx_dev_fim].reset_index(drop=True)
    df_fast = {
        "abertura": df_dev["abertura"].values,
        "minima": df_dev["minima"].values,
        "fechamento": df_dev["fechamento"].values,
        "t_abert": df_dev["t_abert"].values,
    }
    for m in set(PARAMS_TEST["media_rapida"] + PARAMS_TEST["media_lenta"]):
        df_fast[f"ma_{m}"] = df_dev["fechamento"].rolling(m).mean().values
    for mf in set(PARAMS_TEST["media_filtro"]):
        df_fast[f"ma_f_{mf}"] = df_dev["fechamento"].rolling(mf).mean().values
    tr = pd.concat([
        df_dev["maxima"] - df_dev["minima"],
        (df_dev["maxima"] - df_dev["fechamento"].shift()).abs(),
        (df_dev["minima"] - df_dev["fechamento"].shift()).abs(),
    ], axis=1).max(axis=1)
    for pa in set(PARAMS_TEST["atr_periodo"]):
        df_fast[f"atr_{pa}"] = tr.rolling(pa).mean().values

    n_dev = len(df_fast["fechamento"])
    candles_dia = CANDLES_POR_DIA[interval_str]

    resultados = []
    with ProcessPoolExecutor(
        max_workers=n_cores, initializer=_init_worker,
        initargs=(df_fast, n_dev, info_liq["taxa"], info_liq["slippage"], candles_dia),
    ) as executor:
        for saida in executor.map(_worker_treino, comb_base, chunksize=200):
            p, res = saida["p"], saida["res"]
            if res["num_trades"] < MIN_TRADES:
                continue
            if res["calmar"] is None or res["sharpe"] is None:
                continue
            resultados.append({
                "media_rapida_per": p["media_rapida"],
                "media_lenta_per": p["media_lenta"],
                "media_filtro_tendencia_per": p["media_filtro"],
                "atr_periodo": p["atr_periodo"],
                "atr_multiplicador": p["atr_multiplicador"],
                "Retorno_Anualizado_%": res["retorno_anualizado_pct"],
                "DD_%": res["drawdown_pct"],
                "Num_Trades": res["num_trades"],
                "Calmar": res["calmar"],
                "Sharpe": res["sharpe"],
            })

    if not resultados:
        print(f"    [SEM COMBINACOES VALIDAS] {ativo}")
        return None

    d = pd.DataFrame(resultados)
    d["Score_Robustez"] = calcular_score_robustez(d, idx_cols, "Calmar")
    d["Rank_Calmar"] = d["Calmar"].rank(ascending=False, method="min").astype(int)
    d["Rank_Robustez"] = d["Score_Robustez"].rank(ascending=False, method="min").astype(int)
    d = d.sort_values("Calmar", ascending=False)
    d.to_csv(f"{PREFIXO}{ativo}_{interval_str}.csv", index=False)

    dt = time.time() - t0
    print(f"    OK {ativo} {interval_str}: {len(d):,} combos · dev {periodos['dev_inicio'].date()} a "
          f"{periodos['dev_fim'].date()} ({n_dev} candles) · {dt:.0f}s", flush=True)
    return {
        "Ativo": ativo, "Interval": interval_str, "Grupo_Liquidez": info_liq["grupo"],
        "N_Combinacoes_Validas": len(d), "N_Candles_Dev": n_dev,
        "Dev_Inicio": periodos["dev_inicio"].date(), "Dev_Fim": periodos["dev_fim"].date(),
        "Melhor_Calmar": round(float(d["Calmar"].max()), 4),
        "segundos": round(dt, 1),
    }


def main():
    from setores_v7 import UNIVERSO_V7

    alvo = sys.argv[1:] if len(sys.argv) > 1 else list(UNIVERSO_V7)
    comb_base = [
        dict(media_rapida=mr, media_lenta=ml, media_filtro=mf, atr_periodo=ap, atr_multiplicador=am)
        for mr, ml, mf, ap, am in product(
            PARAMS_TEST["media_rapida"], PARAMS_TEST["media_lenta"], PARAMS_TEST["media_filtro"],
            PARAMS_TEST["atr_periodo"], PARAMS_TEST["atr_multiplicador"],
        ) if ml > mr
    ]
    idx_cols = ["media_rapida_per", "media_lenta_per", "media_filtro_tendencia_per",
                "atr_periodo", "atr_multiplicador"]
    n_cores = multiprocessing.cpu_count()

    alvo = [a for a in alvo if not a.startswith("--")]
    total = len(alvo) * len(TIMEFRAMES_V7)
    print("=" * 90)
    print(f"OTIMIZADOR V7 -- grade setorial, timeframes {', '.join(TIMEFRAMES_V7)}")
    print(f"{len(comb_base):,} combinacoes · {len(alvo)} ativos x {len(TIMEFRAMES_V7)} timeframes "
          f"= {total} rodadas · {n_cores} nucleos")
    print("=" * 90, flush=True)

    linhas = []
    k = 0
    for ativo in alvo:
        for interval_str in TIMEFRAMES_V7:
            k += 1
            caminho = f"{PREFIXO}{ativo}_{interval_str}.csv"
            if os.path.exists(caminho) and "--refazer" not in sys.argv:
                print(f"[{k}/{total}] {ativo} {interval_str}: ja existe, pulando", flush=True)
                continue
            print(f"[{k}/{total}] {ativo} {interval_str} ...", flush=True)
            r = rodar_ativo(ativo, interval_str, comb_base, idx_cols, n_cores)
            if r:
                linhas.append(r)
                # salva incremental -- se a rodada for interrompida, nao perde o feito
                pd.DataFrame(linhas).to_csv(f"{PREFIXO}RESUMO_parcial.csv", index=False)

    if linhas:
        df_res = pd.DataFrame(linhas)
        caminho = f"{PREFIXO}RESUMO.csv"
        if os.path.exists(caminho):
            antigo = pd.read_csv(caminho)
            df_res = pd.concat([antigo[~antigo["Ativo"].isin(df_res["Ativo"])], df_res])
        df_res.to_csv(caminho, index=False)
        print(f"\nSalvo: {caminho} ({len(df_res)} ativos)")
        print(f"Tempo total desta rodada: {df_res['segundos'].sum()/60:.1f} min")


if __name__ == "__main__":
    main()
