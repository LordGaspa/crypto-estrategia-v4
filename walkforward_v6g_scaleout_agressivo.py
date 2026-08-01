# -*- coding: utf-8 -*-
# WALK-FORWARD V6g -- SCALE-OUT (saida) + BTC-AGRESSIVO (sizing) COMBINADOS
# ----------------------------------------------------------------------------
# Scale-out (v6c) muda QUANDO sai; BTC-Agressivo (Fase 3, backtest_btc_filter_v4.py)
# muda QUANTO capital aloca por trade -- mecanismos ortogonais, nunca testados
# juntos. Aqui: cada trade usa o fator de sizing do regime BTC na entrada
# (1.5x BULL / 1x LATERAL / 0.5x BEAR) E a logica de saida parcial do
# scale-out.
#
# So periodo de DESENVOLVIMENTO -- holdout continua travado.
#
# Como rodar:
#   .venv\Scripts\python.exe walkforward_v6g_scaleout_agressivo.py

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
from walkforward_v6d_scaleout_btc import classificar_regime_btc

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

FRACAO_SAIDA_PARCIAL = 0.5
FATORES_AGRESSIVO = {"BULL": 1.5, "LATERAL": 1.0, "BEAR": 0.5}


def equity_scale_out_sized(eventos, fechamento, t_abert, btc_close, fatores, taxa, slippage, capital_inicial):
    """Como _equity_de_eventos_scale_out, mas cada trade aloca
    capital_disponivel * fator_regime (nao sempre 100%) -- mesmo mecanismo de
    backtest_btc_filter_v4.simular_equity_sized, com suporte a saida_parcial."""
    n = len(fechamento)
    t_abert_pd = pd.to_datetime(t_abert)
    capital_cash = capital_inicial
    qtd_total_entrada = 0.0
    qtd_aberta = 0.0
    posicionado = False
    equity = np.empty(n)
    equity[0] = capital_inicial
    ev_idx = 0
    n_ev = len(eventos)

    for i in range(1, n):
        if ev_idx < n_ev and eventos[ev_idx][1] == i:
            ev = eventos[ev_idx]
            tipo, idx_ev, preco_ev = ev[0], ev[1], ev[2]
            if tipo == "entrada":
                regime = classificar_regime_btc(t_abert_pd[i], btc_close)
                fator = fatores[regime]
                capital_a_alocar = capital_cash * fator
                qtd_total_entrada = (capital_a_alocar / preco_ev) * (1 - taxa)
                qtd_aberta = qtd_total_entrada
                capital_cash = capital_cash - capital_a_alocar  # pode ficar negativo (alavancagem)
                posicionado = True
            elif tipo == "saida_parcial":
                fracao = ev[4]
                qtd_fechada = qtd_total_entrada * fracao
                preco_saida = preco_ev * (1 - slippage)
                capital_cash += (qtd_fechada * preco_saida) * (1 - taxa)
                qtd_aberta -= qtd_fechada
            else:  # saida
                preco_saida = preco_ev * (1 - slippage)
                capital_cash += (qtd_aberta * preco_saida) * (1 - taxa)
                qtd_aberta = 0.0
                posicionado = False
            ev_idx += 1
        equity[i] = capital_cash if not posicionado else (capital_cash + qtd_aberta * fechamento[i])
    return equity


def main():
    print("Carregando BTC (historico completo) para classificacao de regime...")
    df_btc_full = carregar_dados("BTCUSDT", "6h")
    btc_close = pd.Series(
        df_btc_full["fechamento"].values,
        index=pd.to_datetime(df_btc_full["t_abert"].values),
    ).sort_index()

    linhas = []
    print("=" * 100)
    print("WALK-FORWARD V6g -- SCALE-OUT + BTC-AGRESSIVO (sizing) COMBINADOS")
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

            # Base (sem sizing, sem scale-out) -- referencia original
            eventos_base, _ = wf.simular_posicao(abertura, minima, atr, sinais_compra, sinais_venda, multi, info_liq["slippage"])
            eq_base, _ = wf._equity_de_eventos_base(eventos_base, fechamento, info_liq["taxa"], info_liq["slippage"], CAPITAL_INICIAL)
            met_base = wf._metricas_de_equity(eq_base, abertura, fechamento, t_abert, candles_dia)

            # Scale-out sozinho (sem sizing) -- ja conhecido
            eventos_so, _ = simular_posicao_scale_out(abertura, minima, atr, sinais_compra, sinais_venda, multi, FRACAO_SAIDA_PARCIAL, info_liq["slippage"])
            eq_so, _ = wf._equity_de_eventos_scale_out(eventos_so, fechamento, info_liq["taxa"], info_liq["slippage"], CAPITAL_INICIAL)
            met_so = wf._metricas_de_equity(eq_so, abertura, fechamento, t_abert, candles_dia)

            # Scale-out + BTC-Agressivo (sizing) combinados
            eq_comb = equity_scale_out_sized(eventos_so, fechamento, t_abert, btc_close, FATORES_AGRESSIVO, info_liq["taxa"], info_liq["slippage"], CAPITAL_INICIAL)
            met_comb = wf._metricas_de_equity(eq_comb, abertura, fechamento, t_abert, candles_dia)

            linhas.append({
                "Ativo": ativo, "Grupo": grupo, "Ano": jan["ano"], "Regime": regime_janela,
                "BuyHold_%": met_base["bh_pct"],
                "Retorno_Base_%": met_base["retorno_pct"], "DD_Base_%": met_base["dd_pct"],
                "Retorno_ScaleOut_%": met_so["retorno_pct"], "DD_ScaleOut_%": met_so["dd_pct"],
                "Retorno_Combinado_%": met_comb["retorno_pct"], "DD_Combinado_%": met_comb["dd_pct"],
            })

    df_out = pd.DataFrame(linhas)
    df_out.to_csv("comparativo_v4_vs_v6g_scaleout_agressivo_walkforward.csv", index=False)

    print(f"\nTotal de janelas-ativo: {len(df_out)}")
    print("\nPor regime:")
    for regime in ["BULL", "LATERAL", "BEAR"]:
        sub = df_out[df_out["Regime"] == regime]
        if sub.empty:
            continue
        n = len(sub)
        med_base = sub["Retorno_Base_%"].median()
        med_so = sub["Retorno_ScaleOut_%"].median()
        med_comb = sub["Retorno_Combinado_%"].median()
        med_bh = sub["BuyHold_%"].median()
        dd_base = sub["DD_Base_%"].median()
        dd_so = sub["DD_ScaleOut_%"].median()
        dd_comb = sub["DD_Combinado_%"].median()
        print(f"  {regime:<8} ({n:3d} janelas): B&H {med_bh:+7.1f}%")
        print(f"    {'Base':<12} {med_base:+8.1f}%  DD {dd_base:5.1f}%")
        print(f"    {'ScaleOut':<12} {med_so:+8.1f}%  DD {dd_so:5.1f}%")
        print(f"    {'Combinado':<12} {med_comb:+8.1f}%  DD {dd_comb:5.1f}%")

    print(f"\nSalvo: comparativo_v4_vs_v6g_scaleout_agressivo_walkforward.csv ({len(df_out)} linhas)")


if __name__ == "__main__":
    main()
