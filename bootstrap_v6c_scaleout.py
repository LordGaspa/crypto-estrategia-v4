# -*- coding: utf-8 -*-
# BOOTSTRAP SHARPE -- SCALE-OUT (v6c) POR ATIVO, PERIODO DE DEV COMPLETO
# ----------------------------------------------------------------------------
# Antes de considerar o scale-out (candidata B do v6, unica com sinal real de
# melhora em bull) como algo mais que "observado no walk-forward", testa
# significancia estatistica por ativo -- mesmo metodo bootstrap de
# walkforward_robusta_v4.py (reimplementado aqui, nao importado, pra manter
# o script de v6 desacoplado do de v4/v5 original).
#
# So periodo de DESENVOLVIMENTO -- holdout continua travado.
#
# Como rodar:
#   .venv\Scripts\python.exe bootstrap_v6c_scaleout.py

import sys
import warnings
import numpy as np
import pandas as pd

from config_v4 import (
    ATIVOS_PORTFOLIO_V4, CAPITAL_INICIAL, CANDLES_POR_DIA,
    carregar_dados, separar_periodos, classificar_liquidez, RECEITA_ROBUSTA, grupo_ouro,
)
from estrategia_core import calcular_sinais, simular_posicao, simular_posicao_scale_out
import walkforward_v6c_scaleout as wf

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

BOOTSTRAP_N = 2000
FRACAO_SAIDA_PARCIAL = 0.5


def bootstrap_sharpe(equity: np.ndarray, candles_dia: int, n_boot: int = BOOTSTRAP_N) -> dict:
    """Mesmo metodo de walkforward_robusta_v4.bootstrap_sharpe."""
    eq_dia = equity[::candles_dia]
    ret = np.diff(eq_dia) / eq_dia[:-1]
    ret = ret[np.isfinite(ret)]
    T = len(ret)
    if T < 10 or np.std(ret, ddof=1) == 0:
        return {"sharpe_obs": None, "p_value": None, "T": T}
    sharpe_obs = float(np.mean(ret) / np.std(ret, ddof=1) * np.sqrt(365))
    rng = np.random.default_rng(42)
    boot = []
    for _ in range(n_boot):
        idx = rng.integers(0, T, size=T)
        r = ret[idx]
        s = np.std(r, ddof=1)
        if s > 0:
            boot.append(float(np.mean(r) / s * np.sqrt(365)))
    if not boot:
        return {"sharpe_obs": sharpe_obs, "p_value": None, "T": T}
    p_value = float(np.mean(np.array(boot) <= 0))
    return {"sharpe_obs": sharpe_obs, "p_value": p_value, "T": T}


def main():
    linhas = []
    print("=" * 100)
    print("BOOTSTRAP SHARPE -- RECEITA_ROBUSTA (Base) vs +SAIDA PARCIAL (Scale-out 50%)")
    print("Periodo de desenvolvimento completo, por ativo")
    print("=" * 100)
    print(f"{'Ativo':<16} {'Grupo':<10} {'Sharpe_Base':>12} {'p_Base':>8} {'Sharpe_SO':>11} {'p_SO':>7} {'Melhorou?':>10}")

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

        mr, ml, mf, ap = params["media_rapida"], params["media_lenta"], params["media_filtro"], params["atr_periodo"]
        m_rapida = df_fast[f"ma_{mr}"]
        m_lenta = df_fast[f"ma_{ml}"]
        m_filtro = df_fast[f"ma_f_{mf}"]
        abertura = df_fast["abertura"]
        minima = df_fast["minima"]
        fechamento = df_fast["fechamento"]
        atr = df_fast[f"atr_{ap}"]
        multi = params["atr_multiplicador"]
        n = len(fechamento)
        if n < wf.MIN_CANDLES_JANELA:
            continue

        sinais_compra, sinais_venda = calcular_sinais(m_rapida, m_lenta, m_filtro, fechamento)

        eventos_base, _ = simular_posicao(abertura, minima, atr, sinais_compra, sinais_venda, multi, info_liq["slippage"])
        eventos_so, _ = simular_posicao_scale_out(abertura, minima, atr, sinais_compra, sinais_venda, multi, FRACAO_SAIDA_PARCIAL, info_liq["slippage"])

        eq_base, _ = wf._equity_de_eventos_base(eventos_base, fechamento, info_liq["taxa"], info_liq["slippage"], CAPITAL_INICIAL)
        eq_so, _ = wf._equity_de_eventos_scale_out(eventos_so, fechamento, info_liq["taxa"], info_liq["slippage"], CAPITAL_INICIAL)

        b_base = bootstrap_sharpe(eq_base, candles_dia)
        b_so = bootstrap_sharpe(eq_so, candles_dia)

        sb = b_base["sharpe_obs"]
        pb = b_base["p_value"]
        sso = b_so["sharpe_obs"]
        pso = b_so["p_value"]

        melhorou = "N/A"
        if sb is not None and sso is not None:
            melhorou = "SIM" if sso > sb else "nao"

        sb_str = f"{sb:+.3f}" if sb is not None else "N/A"
        pb_str = f"{pb:.3f}" if pb is not None else "N/A"
        sso_str = f"{sso:+.3f}" if sso is not None else "N/A"
        pso_str = f"{pso:.3f}" if pso is not None else "N/A"
        print(f"{ativo:<16} {grupo:<10} {sb_str:>12} {pb_str:>8} {sso_str:>11} {pso_str:>7} {melhorou:>10}")

        linhas.append({
            "Ativo": ativo, "Grupo": grupo,
            "Sharpe_Base": sb, "p_value_Base": pb, "Significativo_Base": (pb is not None and pb < 0.05),
            "Sharpe_ScaleOut": sso, "p_value_ScaleOut": pso, "Significativo_ScaleOut": (pso is not None and pso < 0.05),
            "ScaleOut_Melhora_Sharpe": melhorou,
        })

    df_out = pd.DataFrame(linhas)
    df_out.to_csv("bootstrap_v6c_scaleout_resultado.csv", index=False)

    n_sig_base = df_out["Significativo_Base"].sum()
    n_sig_so = df_out["Significativo_ScaleOut"].sum()
    n_melhorou = (df_out["ScaleOut_Melhora_Sharpe"] == "SIM").sum()
    n_total = len(df_out)

    print("\n" + "=" * 100)
    print("RESUMO")
    print("=" * 100)
    print(f"Ativos com Sharpe significativo (p<0.05) -- Base: {n_sig_base}/{n_total}")
    print(f"Ativos com Sharpe significativo (p<0.05) -- Scale-out: {n_sig_so}/{n_total}")
    print(f"Ativos onde Scale-out MELHOROU o Sharpe vs Base: {n_melhorou}/{n_total}")
    print(f"\nSalvo: bootstrap_v6c_scaleout_resultado.csv ({len(df_out)} linhas)")


if __name__ == "__main__":
    main()
