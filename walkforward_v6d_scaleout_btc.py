# -*- coding: utf-8 -*-
# WALK-FORWARD V6d -- SCALE-OUT SO EM ANOS BULL DO BTC
# ----------------------------------------------------------------------------
# Combina a candidata B (saida parcial) com o filtro de regime BTC ja
# existente (Fase 3, backtest_btc_filter_v4.py): em vez de aplicar scale-out
# sempre, so aplica quando o regime do BTC (retorno trailing 12m) esta em
# BULL no momento da ENTRADA do trade -- em LATERAL/BEAR, usa a saida cheia
# normal (RECEITA_ROBUSTA padrao). Objetivo: pegar o ganho de bull do
# scale-out sem o custo em lateral que ele mostrou sozinho.
#
# Cada TRADE usa um modo so (decidido na entrada, sem look-ahead: regime
# calculado com dados so ate o momento da entrada) -- nao troca de modo no
# meio do trade.
#
# So periodo de DESENVOLVIMENTO -- holdout continua travado.
#
# Como rodar:
#   .venv\Scripts\python.exe walkforward_v6d_scaleout_btc.py

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

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

FRACAO_SAIDA_PARCIAL = 0.5
BULL_THRESH = 0.25
BEAR_THRESH = -0.25
JANELA_REGIME_DIAS = 365


def classificar_regime_btc(t_entry, btc_close: pd.Series) -> str:
    """Mesma logica de backtest_btc_filter_v4.classificar_regime_btc
    (reimplementada aqui pra nao criar dependencia entre os dois scripts de
    teste) -- retorno trailing 12m do BTC ATE t_entry, sem look-ahead."""
    t_12m = t_entry - pd.Timedelta(days=JANELA_REGIME_DIAS)
    p_atual = btc_close.asof(t_entry)
    p_12m = btc_close.asof(t_12m)
    if pd.isna(p_atual) or pd.isna(p_12m) or p_12m <= 0:
        return "LATERAL"
    ret = p_atual / p_12m - 1.0
    if ret > BULL_THRESH:
        return "BULL"
    if ret < BEAR_THRESH:
        return "BEAR"
    return "LATERAL"


def simular_posicao_scale_out_condicional(
    abertura, minima, atr, sinais_compra, sinais_venda, multi_atr,
    t_abert, btc_close, fracao_saida_parcial, slippage=0.0,
):
    """Como simular_posicao_scale_out, mas SO aplica a logica de saida
    parcial se o regime do BTC no momento da ENTRADA do trade for BULL.
    Em LATERAL/BEAR, o trade usa a saida CHEIA normal (comportamento
    identico a simular_posicao). Decisao e por trade, fixada na entrada."""
    abertura = np.asarray(abertura)
    minima = np.asarray(minima)
    atr = np.asarray(atr)
    n = len(abertura)
    t_abert_pd = pd.to_datetime(t_abert)
    eventos = []
    posicionado = False
    stop = 0.0
    entrada_idx = None
    entrada_preco = None
    modo_scale_out = False
    ja_saiu_parcial = False

    for i in range(1, n):
        if not posicionado and sinais_compra[i - 1] and abertura[i] > 0:
            entrada_preco = abertura[i] * (1 + slippage)
            stop = entrada_preco - atr[i - 1] * multi_atr
            entrada_idx = i
            posicionado = True
            ja_saiu_parcial = False
            regime = classificar_regime_btc(t_abert_pd[i], btc_close)
            modo_scale_out = (regime == "BULL")
            eventos.append(("entrada", i, entrada_preco, stop))
        elif posicionado:
            furou_stop = minima[i] < stop
            if furou_stop:
                preco_bruto = min(stop, abertura[i])
                eventos.append(("saida", i, preco_bruto, stop))
                posicionado = False
            elif sinais_venda[i - 1]:
                if modo_scale_out and not ja_saiu_parcial:
                    eventos.append(("saida_parcial", i, abertura[i], stop, fracao_saida_parcial))
                    ja_saiu_parcial = True
                else:
                    eventos.append(("saida", i, abertura[i], stop))
                    posicionado = False

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
    print("WALK-FORWARD V6d -- SCALE-OUT CONDICIONAL AO REGIME BTC (so em BULL)")
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
            eventos_cond, _ = simular_posicao_scale_out_condicional(
                abertura, minima, atr, sinais_compra, sinais_venda, multi,
                t_abert, btc_close, FRACAO_SAIDA_PARCIAL, info_liq["slippage"],
            )

            eq_base, tr_base = wf._equity_de_eventos_base(eventos_base, fechamento, info_liq["taxa"], info_liq["slippage"], CAPITAL_INICIAL)
            eq_cond, tr_cond = wf._equity_de_eventos_scale_out(eventos_cond, fechamento, info_liq["taxa"], info_liq["slippage"], CAPITAL_INICIAL)

            met_base = wf._metricas_de_equity(eq_base, abertura, fechamento, t_abert, candles_dia)
            met_cond = wf._metricas_de_equity(eq_cond, abertura, fechamento, t_abert, candles_dia)

            linhas.append({
                "Ativo": ativo, "Grupo": grupo, "Ano": jan["ano"], "Regime": regime_janela,
                "BuyHold_%": met_base["bh_pct"],
                "Retorno_Base_%": met_base["retorno_pct"], "DD_Base_%": met_base["dd_pct"],
                "Retorno_Cond_%": met_cond["retorno_pct"], "DD_Cond_%": met_cond["dd_pct"],
            })

    df_out = pd.DataFrame(linhas)
    df_out.to_csv("comparativo_v4_vs_v6d_scaleout_btc_walkforward.csv", index=False)

    print(f"\nTotal de janelas-ativo: {len(df_out)}")
    print("\nPor regime:")
    for regime in ["BULL", "LATERAL", "BEAR"]:
        sub = df_out[df_out["Regime"] == regime]
        if sub.empty:
            continue
        n = len(sub)
        med_base = sub["Retorno_Base_%"].median()
        med_cond = sub["Retorno_Cond_%"].median()
        med_bh = sub["BuyHold_%"].median()
        pct_base_bate_bh = (sub["Retorno_Base_%"] > sub["BuyHold_%"]).mean() * 100
        pct_cond_bate_bh = (sub["Retorno_Cond_%"] > sub["BuyHold_%"]).mean() * 100
        pct_cond_bate_base = (sub["Retorno_Cond_%"] > sub["Retorno_Base_%"]).mean() * 100
        dd_base = sub["DD_Base_%"].median()
        dd_cond = sub["DD_Cond_%"].median()
        print(f"  {regime:<8} ({n:3d} janelas): B&H {med_bh:+7.1f}% | "
              f"Base {med_base:+7.1f}% (bate B&H {pct_base_bate_bh:5.1f}%, DD {dd_base:5.1f}%) | "
              f"Condicional {med_cond:+7.1f}% (bate B&H {pct_cond_bate_bh:5.1f}%, DD {dd_cond:5.1f}%) | "
              f"Cond>Base em {pct_cond_bate_base:5.1f}%")

    print(f"\nSalvo: comparativo_v4_vs_v6d_scaleout_btc_walkforward.csv ({len(df_out)} linhas)")


if __name__ == "__main__":
    main()
