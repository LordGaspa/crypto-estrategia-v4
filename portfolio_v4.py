# -*- coding: utf-8 -*-
# PORTFÓLIO V4 - combinação dos 22 ativos por peso de volatilidade inversa
# ----------------------------------------------------------------------------
# Script novo e independente. Não altera nada do v2, do v3, nem do Código
# Ômega original. Roda DEPOIS do otimizador_v4.py — precisa do
# otimizador_v4_RESUMO_ATIVOS.csv (o parâmetro escolhido por ativo).
#
# O que faz:
#   1. Pra cada ativo, reconstrói a curva de equity da estratégia (com o
#      parâmetro escolhido pelo otimizador_v4.py) SÓ no período de
#      desenvolvimento (nunca toca no holdout).
#   2. Reamostra essa curva pra retornos DIÁRIOS de calendário (por isso usar
#      datas reais em vez de "a cada N candles": os 22 ativos têm intervals
#      de produção diferentes — 4h, 6h, 8h, 12h — então alinhar por posição
#      de candle misturaria períodos de calendário diferentes entre ativos).
#   3. Calcula a volatilidade diária de cada ativo no período em comum a
#      TODOS os 22 (a interseção das datas — normalmente definida pelo ativo
#      mais novo da lista, ex: PENGUUSDT, listado em dez/2024).
#   4. Peso por volatilidade inversa: peso_i = (1/vol_i) / soma(1/vol_j),
#      normalizado pra somar 100%. Ativo mais volátil recebe peso menor.
#   5. Simula o portfólio combinado (retorno ponderado diário, composto) no
#      período em comum, e reporta retorno anualizado, drawdown máximo e
#      Calmar Ratio do portfólio como um todo.
#
# Como rodar (depois do otimizador_v4.py):
#   python portfolio_v4.py

import sys
import warnings
import numpy as np
import pandas as pd

from config_v4 import CANDLES_POR_DIA, carregar_dados, separar_periodos, classificar_liquidez
from otimizador_v4 import executar_backtest_v4

warnings.simplefilter(action="ignore", category=FutureWarning)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

RESUMO_CSV = "otimizador_v4_RESUMO_ATIVOS.csv"


def curva_diaria_do_ativo(ativo: str, interval_str: str, params: dict, taxa: float, slippage: float) -> pd.Series:
    """Reconstrói a curva de equity da estratégia no período de desenvolvimento
    e devolve uma Series de retornos DIÁRIOS (index = data de calendário)."""
    df = carregar_dados(ativo, interval_str)
    periodos = separar_periodos(df["t_abert"])
    idx_dev_fim = periodos["idx_dev_fim"]
    df_dev = df.iloc[:idx_dev_fim].reset_index(drop=True)

    df_fast = {
        "abertura": df_dev["abertura"].values,
        "minima": df_dev["minima"].values,
        "fechamento": df_dev["fechamento"].values,
        "t_abert": df_dev["t_abert"].values,
    }
    df_fast[f"ma_{params['media_rapida']}"] = (
        df_dev["fechamento"].rolling(params["media_rapida"]).mean().values
    )
    df_fast[f"ma_{params['media_lenta']}"] = (
        df_dev["fechamento"].rolling(params["media_lenta"]).mean().values
    )
    df_fast[f"ma_f_{params['media_filtro']}"] = (
        df_dev["fechamento"].rolling(params["media_filtro"]).mean().values
    )
    tr = pd.concat(
        [
            df_dev["maxima"] - df_dev["minima"],
            (df_dev["maxima"] - df_dev["fechamento"].shift()).abs(),
            (df_dev["minima"] - df_dev["fechamento"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df_fast[f"atr_{params['atr_periodo']}"] = tr.rolling(params["atr_periodo"]).mean().values

    candles_dia = CANDLES_POR_DIA[interval_str]
    res = executar_backtest_v4(
        df_fast, params, 0, len(df_fast["fechamento"]), taxa, slippage, candles_dia, incluir_equity=True
    )
    equity = res["equity"]
    datas = pd.to_datetime(df_dev["t_abert"].values)

    serie = pd.Series(equity, index=datas)
    equity_diario = serie.resample("1D").last().dropna()
    retorno_diario = equity_diario.pct_change().dropna()
    return retorno_diario


def main():
    df_resumo = pd.read_csv(RESUMO_CSV)
    if df_resumo.empty:
        print(f"[ERRO] {RESUMO_CSV} está vazio. Rode otimizador_v4.py primeiro.")
        return

    print(f"Reconstruindo curva diária de {len(df_resumo)} ativos (período de desenvolvimento)...")
    retornos_por_ativo = {}
    for _, row in df_resumo.iterrows():
        ativo = row["Ativo"]
        interval_str = row["Interval"]
        params = dict(
            media_rapida=int(row["media_rapida_per"]),
            media_lenta=int(row["media_lenta_per"]),
            media_filtro=int(row["media_filtro_tendencia_per"]),
            atr_periodo=int(row["atr_periodo"]),
            atr_multiplicador=float(row["atr_multiplicador"]),
        )
        info_liq = classificar_liquidez(ativo)
        print(f"  {ativo} ({interval_str}, {info_liq['grupo']})...")
        try:
            retornos_por_ativo[ativo] = curva_diaria_do_ativo(
                ativo, interval_str, params, info_liq["taxa"], info_liq["slippage"]
            )
        except Exception as e:
            print(f"    [AVISO] falhou pra {ativo}: {e}")

    if len(retornos_por_ativo) < 2:
        print("[ERRO] menos de 2 ativos com curva válida — não dá pra montar portfólio.")
        return

    # interseção das datas em que TODOS os ativos têm retorno diário —
    # normalmente limitada pelo ativo mais novo da lista (ex: PENGUUSDT).
    datas_comuns = None
    for serie in retornos_por_ativo.values():
        idx = serie.index
        datas_comuns = idx if datas_comuns is None else datas_comuns.intersection(idx)
    datas_comuns = datas_comuns.sort_values()

    if len(datas_comuns) < 30:
        print(
            f"[AVISO] só {len(datas_comuns)} dias em comum entre todos os ativos — "
            "período curto demais pra um resultado de portfólio confiável, mas "
            "vamos reportar mesmo assim (o limitante costuma ser o ativo mais "
            "novo do portfólio)."
        )

    df_ret = pd.DataFrame(
        {ativo: serie.reindex(datas_comuns) for ativo, serie in retornos_por_ativo.items()}
    )
    vol_diaria = df_ret.std(ddof=1)
    inv_vol = 1.0 / vol_diaria
    pesos = inv_vol / inv_vol.sum()

    df_pesos = pd.DataFrame(
        {
            "Ativo": pesos.index,
            "Vol_Diaria_%": (vol_diaria.reindex(pesos.index) * 100).round(3).values,
            "Peso_Portfolio_%": (pesos * 100).round(2).values,
        }
    ).sort_values("Peso_Portfolio_%", ascending=False)
    df_pesos.to_csv("portfolio_v4_pesos.csv", index=False)
    print(f"\n✅ Salvo: portfolio_v4_pesos.csv\n")
    print(df_pesos.to_string(index=False))

    retorno_portfolio_diario = (df_ret * pesos).sum(axis=1)
    equity_portfolio = (1 + retorno_portfolio_diario).cumprod()
    retorno_total = equity_portfolio.iloc[-1] - 1.0

    dias = (datas_comuns[-1] - datas_comuns[0]).days
    anos = dias / 365.25 if dias > 0 else None
    retorno_anualizado = (1 + retorno_total) ** (1 / anos) - 1 if anos and anos > 0 else None

    running_max = equity_portfolio.cummax()
    dd_series = (running_max - equity_portfolio) / running_max
    max_dd = dd_series.max()

    calmar_portfolio = (
        retorno_anualizado / max_dd if (max_dd and max_dd > 0 and retorno_anualizado is not None) else None
    )

    resultado = {
        "Periodo_Comum_Inicio": datas_comuns[0].date(),
        "Periodo_Comum_Fim": datas_comuns[-1].date(),
        "N_Dias_Comuns": len(datas_comuns),
        "N_Ativos": len(retornos_por_ativo),
        "Retorno_Total_%": round(retorno_total * 100, 2),
        "Retorno_Anualizado_%": round(retorno_anualizado * 100, 2) if retorno_anualizado is not None else None,
        "DD_%": round(max_dd * 100, 2),
        "Calmar_Portfolio": round(calmar_portfolio, 3) if calmar_portfolio is not None else None,
    }
    df_resultado = pd.DataFrame([resultado])
    df_resultado.to_csv("portfolio_v4_resultado.csv", index=False)
    print(f"\n✅ Salvo: portfolio_v4_resultado.csv\n")
    print(df_resultado.to_string(index=False))
    print(
        "\nObs: o período em comum entre os 22 ativos costuma ser limitado pelo "
        "ativo com histórico mais curto do portfólio (ex: PENGUUSDT, listado em "
        "dez/2024) — o portfólio combinado só pode ser simulado onde TODOS os "
        "22 já existiam."
    )


if __name__ == "__main__":
    main()
