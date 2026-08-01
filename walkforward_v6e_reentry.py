# -*- coding: utf-8 -*-
# WALK-FORWARD V6e -- REENTRADA RAPIDA APOS CRUZAMENTO, EM REGIME BULL DO BTC
# ----------------------------------------------------------------------------
# Candidata C da lista original: depois de uma saida por CRUZAMENTO (nao por
# stop) enquanto o regime do BTC (trailing 12m) esta em BULL, observa os
# proximos K candles -- se o fechamento superar a MAXIMA que a posicao ja
# tinha alcancado antes da saida (nova maxima = "voltou a subir de verdade,
# nao foi reversao"), reentra imediatamente (sem esperar um novo cruzamento
# de medias completo), com um stop novo calculado do mesmo jeito de sempre.
# Se K candles passarem sem nova maxima, desiste e volta a exigir o sinal de
# compra normal (sinais_compra).
#
# Convencao de nao-look-ahead identica ao resto do projeto: a condicao de
# reentrada e verificada com o FECHAMENTO de i-1 (ja confechado), e a acao
# (reentrar) acontece na ABERTURA de i -- mesmo padrao de sinais_compra[i-1].
#
# So periodo de DESENVOLVIMENTO -- holdout continua travado.
#
# Como rodar:
#   .venv\Scripts\python.exe walkforward_v6e_reentry.py

import sys
import warnings
import numpy as np
import pandas as pd

from config_v4 import (
    ATIVOS_PORTFOLIO_V4, CAPITAL_INICIAL, CANDLES_POR_DIA,
    carregar_dados, separar_periodos, classificar_liquidez, RECEITA_ROBUSTA, grupo_ouro,
)
from estrategia_core import calcular_sinais
import walkforward_v6c_scaleout as wf
from walkforward_v6d_scaleout_btc import classificar_regime_btc

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

JANELA_REENTRADA_CANDLES = 10  # quantos candles esperar por uma nova maxima antes de desistir


def simular_posicao_reentry(
    abertura, minima, fechamento, atr, sinais_compra, sinais_venda, multi_atr,
    t_abert, btc_close, janela_reentrada, slippage=0.0,
):
    """Como simular_posicao, mas apos uma saida por CRUZAMENTO (nao stop) em
    regime BULL do BTC, observa ate janela_reentrada candles: se o
    fechamento[i-1] superar a maxima ja alcancada pela posicao antes da
    saida, reentra na abertura de i (sem esperar sinais_compra), com stop
    novo. Se a janela esgotar sem nova maxima, ou o regime nao for BULL,
    volta ao comportamento normal (exige sinais_compra)."""
    abertura = np.asarray(abertura)
    minima = np.asarray(minima)
    fechamento = np.asarray(fechamento)
    atr = np.asarray(atr)
    n = len(abertura)
    t_abert_pd = pd.to_datetime(t_abert)
    eventos = []
    posicionado = False
    stop = 0.0
    entrada_idx = None
    entrada_preco = None

    aguardando_reentrada = False
    candles_restantes = 0
    maxima_alvo = -np.inf

    for i in range(1, n):
        if not posicionado:
            reentrada_confirmada = False
            if aguardando_reentrada:
                if fechamento[i - 1] > maxima_alvo:
                    reentrada_confirmada = True
                else:
                    candles_restantes -= 1
                    if candles_restantes <= 0:
                        aguardando_reentrada = False

            if (sinais_compra[i - 1] or reentrada_confirmada) and abertura[i] > 0:
                entrada_preco = abertura[i] * (1 + slippage)
                stop = entrada_preco - atr[i - 1] * multi_atr
                entrada_idx = i
                posicionado = True
                aguardando_reentrada = False
                eventos.append(("entrada", i, entrada_preco, stop))
        else:
            furou_stop = minima[i] < stop
            cruzou_venda = sinais_venda[i - 1]
            if furou_stop or cruzou_venda:
                preco_bruto = min(stop, abertura[i]) if furou_stop else abertura[i]
                eventos.append(("saida", i, preco_bruto, stop))
                posicionado = False
                if cruzou_venda and not furou_stop:
                    regime = classificar_regime_btc(t_abert_pd[i], btc_close)
                    if regime == "BULL":
                        maxima_pos = float(np.max(fechamento[entrada_idx:i]))
                        aguardando_reentrada = True
                        candles_restantes = janela_reentrada
                        maxima_alvo = maxima_pos

    estado_final = {"posicionado": posicionado, "stop": stop if posicionado else None}
    return eventos, estado_final


def main():
    print("Carregando BTC (historico completo) para classificacao de regime...")
    df_btc_full = carregar_dados("BTCUSDT", "6h")
    btc_close = pd.Series(
        df_btc_full["fechamento"].values,
        index=pd.to_datetime(df_btc_full["t_abert"].values),
    ).sort_index()

    linhas = []
    print("=" * 90)
    print(f"WALK-FORWARD V6e -- REENTRADA RAPIDA (janela {JANELA_REENTRADA_CANDLES} candles) EM BULL DO BTC")
    print("=" * 90)

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

            eventos_base, _ = wf.simular_posicao(abertura, minima, atr, sinais_compra, sinais_venda, multi, info_liq["slippage"])
            eventos_re, _ = simular_posicao_reentry(
                abertura, minima, fechamento, atr, sinais_compra, sinais_venda, multi,
                t_abert, btc_close, JANELA_REENTRADA_CANDLES, info_liq["slippage"],
            )

            eq_base, tr_base = wf._equity_de_eventos_base(eventos_base, fechamento, info_liq["taxa"], info_liq["slippage"], CAPITAL_INICIAL)
            eq_re, tr_re = wf._equity_de_eventos_base(eventos_re, fechamento, info_liq["taxa"], info_liq["slippage"], CAPITAL_INICIAL)

            met_base = wf._metricas_de_equity(eq_base, abertura, fechamento, t_abert, candles_dia)
            met_re = wf._metricas_de_equity(eq_re, abertura, fechamento, t_abert, candles_dia)

            linhas.append({
                "Ativo": ativo, "Grupo": grupo, "Ano": jan["ano"], "Regime": regime_janela,
                "BuyHold_%": met_base["bh_pct"],
                "Retorno_Base_%": met_base["retorno_pct"], "DD_Base_%": met_base["dd_pct"], "Trades_Base": tr_base,
                "Retorno_Reentry_%": met_re["retorno_pct"], "DD_Reentry_%": met_re["dd_pct"], "Trades_Reentry": tr_re,
            })

    df_out = pd.DataFrame(linhas)
    df_out.to_csv("comparativo_v4_vs_v6e_reentry_walkforward.csv", index=False)

    print(f"\nTotal de janelas-ativo: {len(df_out)}")
    print("\nPor regime:")
    for regime in ["BULL", "LATERAL", "BEAR"]:
        sub = df_out[df_out["Regime"] == regime]
        if sub.empty:
            continue
        n = len(sub)
        med_base = sub["Retorno_Base_%"].median()
        med_re = sub["Retorno_Reentry_%"].median()
        med_bh = sub["BuyHold_%"].median()
        pct_base_bate_bh = (sub["Retorno_Base_%"] > sub["BuyHold_%"]).mean() * 100
        pct_re_bate_bh = (sub["Retorno_Reentry_%"] > sub["BuyHold_%"]).mean() * 100
        pct_re_bate_base = (sub["Retorno_Reentry_%"] > sub["Retorno_Base_%"]).mean() * 100
        dd_base = sub["DD_Base_%"].median()
        dd_re = sub["DD_Reentry_%"].median()
        print(f"  {regime:<8} ({n:3d} janelas): B&H {med_bh:+7.1f}% | "
              f"Base {med_base:+7.1f}% (bate B&H {pct_base_bate_bh:5.1f}%, DD {dd_base:5.1f}%) | "
              f"Reentry {med_re:+7.1f}% (bate B&H {pct_re_bate_bh:5.1f}%, DD {dd_re:5.1f}%) | "
              f"Re>Base em {pct_re_bate_base:5.1f}%")

    print(f"\nSalvo: comparativo_v4_vs_v6e_reentry_walkforward.csv ({len(df_out)} linhas)")


if __name__ == "__main__":
    main()
