# -*- coding: utf-8 -*-
# DAYTRADE HOLDOUT - validacao final no periodo LACRADO (ultimas
# HOLDOUT_SEMANAS_DAYTRADE semanas)
# ----------------------------------------------------------------------------
# ATENCAO: mesma trava tecnica de holdout_v4.py. So roda de verdade com:
#   python daytrade_holdout.py --eu-confirmo-holdout-final
#
# So valida a candidata/params escolhidos no walk-forward (daytrade_walkforward.py)
# -- nao reotimiza nada aqui. Se o walk-forward nao mostrou nenhuma candidata
# com expectativa liquida positiva no cenario base, este script nao deveria
# ser executado (nao ha nada honesto pra validar) -- ver RELATORIO_DAYTRADE_FASE1.md.

import sys
import numpy as np
import pandas as pd

from daytrade_config import UNIVERSO_DAYTRADE, HOLDOUT_SEMANAS_DAYTRADE, carregar_dados_intraday, separar_periodos_daytrade
from daytrade_backtest import montar_indicadores_daytrade, executar_backtest_daytrade, relatorio_economia_trades
from daytrade_custos import TAXA_TAKER_BASE, SLIPPAGE_BASE, GAP_FRAC_ESTRESSE
from daytrade_walkforward import CANDLES_POR_DIA_DAYTRADE, montar_params_execucao, CAPITAL_REFERENCIA

# ----------------------------------------------------------------------------
# CANDIDATA VENCEDORA -- preenchido DEPOIS de revisar
# daytrade_walkforward_resumo.csv (walk-forward, so periodo de dev). Nao
# escolher aqui olhando o holdout -- so pode ser definido pelo que o
# walk-forward mostrou.
# ----------------------------------------------------------------------------
CANDIDATA_ESCOLHIDA = None   # ex.: "momentum"
USAR_VOLUME_ESCOLHIDO = None  # ex.: True
TIMEFRAME_ESCOLHIDO = None    # ex.: "15m"


def validar_holdout_daytrade_final():
    if CANDIDATA_ESCOLHIDA is None:
        print(
            "CANDIDATA_ESCOLHIDA nao foi definida neste arquivo. Revise "
            "daytrade_walkforward_resumo.csv e preencha CANDIDATA_ESCOLHIDA / "
            "USAR_VOLUME_ESCOLHIDO / TIMEFRAME_ESCOLHIDO no topo deste script "
            "antes de rodar o holdout final."
        )
        raise SystemExit(1)

    print("=" * 90)
    print(f"DAYTRADE HOLDOUT FINAL -- {CANDIDATA_ESCOLHIDA} (volume={USAR_VOLUME_ESCOLHIDO}, "
          f"{TIMEFRAME_ESCOLHIDO}) -- ultimas {HOLDOUT_SEMANAS_DAYTRADE} semanas")
    print("Periodo LACRADO -- rodado uma unica vez, sem re-otimizacao.")
    print("=" * 90)

    todos_trades = []
    n_dias_total = 0.0
    resultados_por_ativo = []

    for ativo in UNIVERSO_DAYTRADE:
        df = carregar_dados_intraday(ativo, TIMEFRAME_ESCOLHIDO)
        if df.empty:
            print(f"[AVISO] sem dados para {ativo}")
            continue
        periodos = separar_periodos_daytrade(df["t_abert"])
        df_holdout = df.iloc[periodos["idx_dev_fim"]:].reset_index(drop=True)
        if len(df_holdout) < 200:
            print(f"[AVISO] holdout curto demais para {ativo} ({len(df_holdout)} candles)")
            continue

        params = montar_params_execucao(CANDIDATA_ESCOLHIDA, TIMEFRAME_ESCOLHIDO)
        arrays = montar_indicadores_daytrade(df_holdout, params)
        candles_dia = CANDLES_POR_DIA_DAYTRADE[TIMEFRAME_ESCOLHIDO]

        res = executar_backtest_daytrade(
            arrays, CANDIDATA_ESCOLHIDA, params, taxa=TAXA_TAKER_BASE, candles_por_dia=candles_dia,
            usar_filtro_volume=USAR_VOLUME_ESCOLHIDO, gap_frac_estresse=GAP_FRAC_ESTRESSE,
        )
        trades = res["base"]["trades"]
        todos_trades.extend(trades)
        n_dias = len(df_holdout) / candles_dia
        n_dias_total += n_dias

        resultados_por_ativo.append({
            "ativo": ativo, "n_trades": len(trades),
            "retorno_total_pct": res["base"]["retorno_total_pct"],
            "drawdown_pct": res["base"]["drawdown_pct"],
            "calmar": res["base"]["calmar"],
        })
        print(f"  {ativo:14s} {len(trades):4d} trades, ret={res['base']['retorno_total_pct']:+.2f}%, "
              f"DD={res['base']['drawdown_pct']:.2f}%, Calmar={res['base']['calmar']}")

    df_ativos = pd.DataFrame(resultados_por_ativo)
    df_ativos.to_csv("daytrade_holdout_resultado.csv", index=False)

    rep = relatorio_economia_trades(todos_trades, TAXA_TAKER_BASE, SLIPPAGE_BASE, CAPITAL_REFERENCIA, n_dias_total)
    print("\n" + "=" * 90)
    print("RELATORIO DE ECONOMIA POR TRADE -- HOLDOUT (todos os ativos agregados)")
    print("=" * 90)
    for k, v in rep.items():
        print(f"  {k:35s} = {v}")

    print(f"\nSalvo: daytrade_holdout_resultado.csv ({len(df_ativos)} ativos)")
    print(
        "\nCompare estes numeros com daytrade_walkforward_resumo.csv (periodo de dev) "
        "para a mesma combinacao -- sinal/magnitude parecidos = validacao honesta; "
        "sinal invertido ou muito pior = a candidata nao generaliza."
    )


if __name__ == "__main__":
    if "--eu-confirmo-holdout-final" not in sys.argv:
        print(
            "Este script so deve rodar quando voce pedir explicitamente.\n"
            "E o unico lugar do sistema de day-trade que toca no periodo "
            "lacrado (holdout).\n\n"
            "Pra rodar de verdade:\n"
            "  python daytrade_holdout.py --eu-confirmo-holdout-final\n"
        )
        raise SystemExit(0)
    validar_holdout_daytrade_final()
