# -*- coding: utf-8 -*-
# WALK-FORWARD V6h -- SCALE-OUT COM TRAILING NA METADE REMANESCENTE
# ----------------------------------------------------------------------------
# Depois da saida parcial (scale-out), a metade que fica aberta passa a usar
# trailing stop em vez do stop fixo original -- diferente do trailing puro
# (v3/Fase 2A, ja descartado), que aplicava trailing na posicao INTEIRA desde
# o começo.
#
# So periodo de DESENVOLVIMENTO -- holdout continua travado.
#
# Como rodar:
#   .venv\Scripts\python.exe walkforward_v6h_scaleout_trailing.py

import sys
import warnings
import numpy as np
import pandas as pd

from config_v4 import (
    ATIVOS_PORTFOLIO_V4, CAPITAL_INICIAL, CANDLES_POR_DIA,
    carregar_dados, separar_periodos, classificar_liquidez, RECEITA_ROBUSTA, grupo_ouro,
)
from estrategia_core import calcular_sinais, simular_posicao_scale_out, simular_posicao_scale_out_trailing
import walkforward_v6c_scaleout as wf

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

FRACAO_SAIDA_PARCIAL = 0.5
MULT_TRAILING = 2.0  # mesma ordem de grandeza do multi_atr do stop fixo, nao ajustado


def main():
    linhas = []
    print("=" * 100)
    print("WALK-FORWARD V6h -- SCALE-OUT vs SCALE-OUT+TRAILING (na metade remanescente)")
    print("=" * 100)

    for ativo, interval_str in ATIVOS_PORTFOLIO_V4.items():
        grupo = grupo_ouro(ativo)
        params = RECEITA_ROBUSTA[grupo]
        info_liq = classificar_liquidez(ativo)
        candles_dia = CANDLES_POR_DIA[interval_str]

        df = carregar_dados(ativo, interval_str)
        if df.empty:
            continue
        periodos = separar_periodos(df["t_abert"])
        idx_dev_fim = periodos["idx_dev_fim"]
        if idx_dev_fim < wf.MIN_CANDLES_JANELA:
            continue

        df_dev = df.iloc[:idx_dev_fim].reset_index(drop=True)
        df_fast = wf.montar_df_fast(df_dev, params)
        janelas = wf.gerar_janelas_anuais(df, idx_dev_fim)
        if not janelas:
            continue

        for jan in janelas:
            mr, ml, mf, ap = params["media_rapida"], params["media_lenta"], params["media_filtro"], params["atr_periodo"]
            ini, fim = jan["idx_ini"], jan["idx_fim"]
            m_rapida = df_fast[f"ma_{mr}"][ini:fim]
            m_lenta = df_fast[f"ma_{ml}"][ini:fim]
            m_filtro = df_fast[f"ma_f_{mf}"][ini:fim]
            abertura = df_fast["abertura"][ini:fim]
            minima = df_fast["minima"][ini:fim]
            maxima = df_dev["maxima"].values[ini:fim]  # nao esta no df_fast do v6c, pega direto do df_dev
            fechamento = df_fast["fechamento"][ini:fim]
            atr = df_fast[f"atr_{ap}"][ini:fim]
            t_abert = df_fast["t_abert"][ini:fim]
            multi = params["atr_multiplicador"]
            n = len(fechamento)
            if n < wf.MIN_CANDLES_JANELA:
                continue

            sinais_compra, sinais_venda = calcular_sinais(m_rapida, m_lenta, m_filtro, fechamento)
            bh = float((fechamento[-1] - abertura[0]) / abertura[0]) if abertura[0] > 0 else 0.0
            regime_janela = "BULL" if bh > 0.25 else ("BEAR" if bh < -0.25 else "LATERAL")

            eventos_so, _ = simular_posicao_scale_out(abertura, minima, atr, sinais_compra, sinais_venda, multi, FRACAO_SAIDA_PARCIAL, info_liq["slippage"])
            eventos_sot, _ = simular_posicao_scale_out_trailing(
                abertura, minima, maxima, atr, sinais_compra, sinais_venda, multi,
                FRACAO_SAIDA_PARCIAL, MULT_TRAILING, info_liq["slippage"],
            )

            eq_so, _ = wf._equity_de_eventos_scale_out(eventos_so, fechamento, info_liq["taxa"], info_liq["slippage"], CAPITAL_INICIAL)
            eq_sot, _ = wf._equity_de_eventos_scale_out(eventos_sot, fechamento, info_liq["taxa"], info_liq["slippage"], CAPITAL_INICIAL)

            met_so = wf._metricas_de_equity(eq_so, abertura, fechamento, t_abert, candles_dia)
            met_sot = wf._metricas_de_equity(eq_sot, abertura, fechamento, t_abert, candles_dia)

            linhas.append({
                "Ativo": ativo, "Grupo": grupo, "Ano": jan["ano"], "Regime": regime_janela,
                "BuyHold_%": met_so["bh_pct"],
                "Retorno_ScaleOut_%": met_so["retorno_pct"], "DD_ScaleOut_%": met_so["dd_pct"],
                "Retorno_ScaleOutTrailing_%": met_sot["retorno_pct"], "DD_ScaleOutTrailing_%": met_sot["dd_pct"],
            })

    df_out = pd.DataFrame(linhas)
    df_out.to_csv("comparativo_v4_vs_v6h_scaleout_trailing_walkforward.csv", index=False)

    print(f"\nTotal de janelas-ativo: {len(df_out)}")
    print("\nPor regime:")
    for regime in ["BULL", "LATERAL", "BEAR"]:
        sub = df_out[df_out["Regime"] == regime]
        if sub.empty:
            continue
        n = len(sub)
        med_so = sub["Retorno_ScaleOut_%"].median()
        med_sot = sub["Retorno_ScaleOutTrailing_%"].median()
        med_bh = sub["BuyHold_%"].median()
        pct_sot_bate_so = (sub["Retorno_ScaleOutTrailing_%"] > sub["Retorno_ScaleOut_%"]).mean() * 100
        dd_so = sub["DD_ScaleOut_%"].median()
        dd_sot = sub["DD_ScaleOutTrailing_%"].median()
        print(f"  {regime:<8} ({n:3d} janelas): B&H {med_bh:+7.1f}% | "
              f"ScaleOut {med_so:+7.1f}% (DD {dd_so:5.1f}%) | "
              f"ScaleOut+Trail {med_sot:+7.1f}% (DD {dd_sot:5.1f}%) | "
              f"Trail>ScaleOut em {pct_sot_bate_so:5.1f}%")

    print(f"\nSalvo: comparativo_v4_vs_v6h_scaleout_trailing_walkforward.csv ({len(df_out)} linhas)")


if __name__ == "__main__":
    main()
