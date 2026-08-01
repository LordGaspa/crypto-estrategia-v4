# -*- coding: utf-8 -*-
# WALK-FORWARD V6c -- RECEITA_ROBUSTA vs +SAIDA PARCIAL (SCALE-OUT)
# ----------------------------------------------------------------------------
# Candidata B da lista original: em vez de tentar prever se o cruzamento
# contrario e um pullback ou uma reversao real (v6/ADX, que nao funcionou),
# so realiza METADE do lucro no primeiro cruzamento e deixa a outra metade
# correr com o mesmo stop ATR -- funciona em qualquer caso, sem precisar
# adivinhar.
#
# Usa estrategia_core.simular_posicao_scale_out (nova, aditiva). So periodo
# de DESENVOLVIMENTO -- holdout continua travado.
#
# Como rodar:
#   .venv\Scripts\python.exe walkforward_v6c_scaleout.py

import sys
import warnings
import numpy as np
import pandas as pd

from config_v4 import (
    ATIVOS_PORTFOLIO_V4, CAPITAL_INICIAL, CANDLES_POR_DIA,
    carregar_dados, separar_periodos, classificar_liquidez, RECEITA_ROBUSTA, grupo_ouro,
)
from estrategia_core import calcular_sinais, simular_posicao, simular_posicao_scale_out

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

JANELA_ANOS = 1
MIN_CANDLES_JANELA = 40
FRACAO_SAIDA_PARCIAL = 0.5  # realiza metade no 1o cruzamento -- ponto medio, nao ajustado


def montar_df_fast(df: pd.DataFrame, params: dict) -> dict:
    fast = {
        "abertura": df["abertura"].values,
        "minima": df["minima"].values,
        "fechamento": df["fechamento"].values,
        "t_abert": df["t_abert"].values,
    }
    for m in {params["media_rapida"], params["media_lenta"]}:
        fast[f"ma_{m}"] = df["fechamento"].rolling(m).mean().values
    fast[f"ma_f_{params['media_filtro']}"] = (
        df["fechamento"].rolling(params["media_filtro"]).mean().values
    )
    tr = pd.concat([
        df["maxima"] - df["minima"],
        (df["maxima"] - df["fechamento"].shift()).abs(),
        (df["minima"] - df["fechamento"].shift()).abs(),
    ], axis=1).max(axis=1)
    fast[f"atr_{params['atr_periodo']}"] = tr.rolling(params["atr_periodo"]).mean().values
    return fast


def _equity_de_eventos_base(eventos, fechamento, taxa, slippage, capital_inicial):
    n = len(fechamento)
    capital = capital_inicial
    posicionado = False
    qtd = 0.0
    num_trades = 0
    equity = np.empty(n)
    equity[0] = capital
    ev_idx = 0
    n_ev = len(eventos)
    for i in range(1, n):
        if ev_idx < n_ev and eventos[ev_idx][1] == i:
            tipo, _idx, preco_ev, _stop = eventos[ev_idx]
            if tipo == "entrada":
                qtd = (capital / preco_ev) * (1 - taxa)
                posicionado = True
            else:
                preco_saida = preco_ev * (1 - slippage)
                capital = (qtd * preco_saida) * (1 - taxa)
                posicionado = False
                num_trades += 1
            ev_idx += 1
        equity[i] = capital if not posicionado else (qtd * fechamento[i])
    return equity, num_trades


def _equity_de_eventos_scale_out(eventos, fechamento, taxa, slippage, capital_inicial):
    """Como _equity_de_eventos_base, mas trata eventos 'saida_parcial'
    (fecha so uma fracao da quantidade, mantem o resto posicionado)."""
    n = len(fechamento)
    capital_realizado = capital_inicial  # capital em CASH (fora da posicao)
    qtd_total_entrada = 0.0  # quantidade original da entrada (referencia p/ fracao)
    qtd_aberta = 0.0         # quantidade ainda posicionada
    posicionado = False
    num_trades = 0  # conta saidas completas (parcial nao conta como trade fechado)
    equity = np.empty(n)
    equity[0] = capital_inicial
    ev_idx = 0
    n_ev = len(eventos)
    capital_alocado_na_entrada = 0.0

    for i in range(1, n):
        if ev_idx < n_ev and eventos[ev_idx][1] == i:
            ev = eventos[ev_idx]
            tipo, _idx, preco_ev = ev[0], ev[1], ev[2]
            if tipo == "entrada":
                capital_alocado_na_entrada = capital_realizado
                qtd_total_entrada = (capital_realizado / preco_ev) * (1 - taxa)
                qtd_aberta = qtd_total_entrada
                capital_realizado = 0.0
                posicionado = True
            elif tipo == "saida_parcial":
                fracao = ev[4]
                qtd_fechada = qtd_total_entrada * fracao
                preco_saida = preco_ev * (1 - slippage)
                capital_realizado += (qtd_fechada * preco_saida) * (1 - taxa)
                qtd_aberta -= qtd_fechada
            else:  # saida (fecha o que sobrou de qtd_aberta)
                preco_saida = preco_ev * (1 - slippage)
                capital_realizado += (qtd_aberta * preco_saida) * (1 - taxa)
                qtd_aberta = 0.0
                posicionado = False
                num_trades += 1
            ev_idx += 1
        equity[i] = capital_realizado if not posicionado else (capital_realizado + qtd_aberta * fechamento[i])
    return equity, num_trades


def _metricas_de_equity(equity, abertura, fechamento, t_abert, candles_dia):
    retorno = (equity[-1] - equity[0]) / equity[0]
    running_max = np.maximum.accumulate(equity)
    with np.errstate(invalid="ignore", divide="ignore"):
        dd_series = (running_max - equity) / running_max
    max_dd = float(np.nanmax(dd_series))
    bh = float((fechamento[-1] - abertura[0]) / abertura[0]) if abertura[0] > 0 else 0.0
    if bh > 0.25:
        regime = "BULL"
    elif bh < -0.25:
        regime = "BEAR"
    else:
        regime = "LATERAL"
    return {"retorno_pct": round(retorno * 100, 2), "dd_pct": round(max_dd * 100, 2),
            "bh_pct": round(bh * 100, 2), "regime": regime}


def executar_janela_comparativo(df_fast, params, inicio, fim, taxa, slippage, candles_dia):
    mr, ml, mf, ap = params["media_rapida"], params["media_lenta"], params["media_filtro"], params["atr_periodo"]
    m_rapida = df_fast[f"ma_{mr}"][inicio:fim]
    m_lenta = df_fast[f"ma_{ml}"][inicio:fim]
    m_filtro = df_fast[f"ma_f_{mf}"][inicio:fim]
    abertura = df_fast["abertura"][inicio:fim]
    minima = df_fast["minima"][inicio:fim]
    fechamento = df_fast["fechamento"][inicio:fim]
    atr = df_fast[f"atr_{ap}"][inicio:fim]
    t_abert = df_fast["t_abert"][inicio:fim]
    multi = params["atr_multiplicador"]

    n = len(fechamento)
    if n < MIN_CANDLES_JANELA:
        return None

    sinais_compra, sinais_venda = calcular_sinais(m_rapida, m_lenta, m_filtro, fechamento)

    eventos_base, _ = simular_posicao(abertura, minima, atr, sinais_compra, sinais_venda, multi, slippage)
    eventos_so, _ = simular_posicao_scale_out(
        abertura, minima, atr, sinais_compra, sinais_venda, multi, FRACAO_SAIDA_PARCIAL, slippage
    )

    eq_base, trades_base = _equity_de_eventos_base(eventos_base, fechamento, taxa, slippage, CAPITAL_INICIAL)
    eq_so, trades_so = _equity_de_eventos_scale_out(eventos_so, fechamento, taxa, slippage, CAPITAL_INICIAL)

    met_base = _metricas_de_equity(eq_base, abertura, fechamento, t_abert, candles_dia)
    met_so = _metricas_de_equity(eq_so, abertura, fechamento, t_abert, candles_dia)

    return {
        "regime": met_base["regime"], "bh_pct": met_base["bh_pct"],
        "retorno_base_pct": met_base["retorno_pct"], "dd_base_pct": met_base["dd_pct"], "trades_base": trades_base,
        "retorno_so_pct": met_so["retorno_pct"], "dd_so_pct": met_so["dd_pct"], "trades_so": trades_so,
    }


def gerar_janelas_anuais(df: pd.DataFrame, idx_dev_fim: int) -> list:
    datas = df["t_abert"].iloc[:idx_dev_fim].reset_index(drop=True)
    n = len(datas)
    if n < MIN_CANDLES_JANELA:
        return []
    data_min, data_max = datas.iloc[0], datas.iloc[-1]
    janelas = []
    ano = data_min.year
    while ano <= data_max.year:
        ts_ini = pd.Timestamp(year=ano, month=1, day=1)
        ts_fim = pd.Timestamp(year=ano + JANELA_ANOS, month=1, day=1)
        idx_ini = int(datas.searchsorted(ts_ini))
        idx_fim = min(int(datas.searchsorted(ts_fim)), n)
        if idx_fim - idx_ini >= MIN_CANDLES_JANELA:
            janelas.append({"ano": ano, "idx_ini": idx_ini, "idx_fim": idx_fim,
                             "periodo": f"{datas.iloc[idx_ini].date()} a {datas.iloc[idx_fim-1].date()}"})
        ano += 1
    return janelas


def main():
    linhas = []
    print("=" * 90)
    print("WALK-FORWARD V6c -- RECEITA_ROBUSTA vs +SAIDA PARCIAL (scale-out 50%)")
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
        if idx_dev_fim < MIN_CANDLES_JANELA:
            continue

        df_dev = df.iloc[:idx_dev_fim].reset_index(drop=True)
        df_fast = montar_df_fast(df_dev, params)
        janelas = gerar_janelas_anuais(df, idx_dev_fim)
        if not janelas:
            continue

        print(f"\n{ativo} ({interval_str}) [{grupo}]")
        print(f"  {'Ano':<6} {'Regime':<8} {'Base%':>8} {'ScaleOut%':>10} {'B&H%':>8} {'Tr_B':>6} {'Tr_SO':>6}")

        for jan in janelas:
            res = executar_janela_comparativo(
                df_fast, params, jan["idx_ini"], jan["idx_fim"],
                info_liq["taxa"], info_liq["slippage"], candles_dia,
            )
            if res is None:
                continue
            print(f"  {jan['ano']:<6} {res['regime']:<8} {res['retorno_base_pct']:>8.1f} "
                  f"{res['retorno_so_pct']:>10.1f} {res['bh_pct']:>8.1f} "
                  f"{res['trades_base']:>6} {res['trades_so']:>6}")
            linhas.append({
                "Ativo": ativo, "Grupo": grupo, "Interval": interval_str, "Ano": jan["ano"],
                "Periodo": jan["periodo"], "Regime": res["regime"],
                "Retorno_Base_%": res["retorno_base_pct"], "Retorno_ScaleOut_%": res["retorno_so_pct"],
                "BuyHold_%": res["bh_pct"],
                "DD_Base_%": res["dd_base_pct"], "DD_ScaleOut_%": res["dd_so_pct"],
                "Trades_Base": res["trades_base"], "Trades_ScaleOut": res["trades_so"],
            })

    df_out = pd.DataFrame(linhas)
    df_out.to_csv("comparativo_v4_vs_v6c_scaleout_walkforward.csv", index=False)

    print("\n\n" + "=" * 90)
    print("COMPARATIVO GLOBAL -- RECEITA_ROBUSTA (Base) vs +SAIDA PARCIAL (v6c)")
    print("=" * 90)
    print(f"Total de janelas-ativo: {len(df_out)}")
    print(f"Base      -- mediana: {df_out['Retorno_Base_%'].median():+.1f}% | "
          f"% positivas: {(df_out['Retorno_Base_%']>0).mean()*100:.1f}%")
    print(f"ScaleOut  -- mediana: {df_out['Retorno_ScaleOut_%'].median():+.1f}% | "
          f"% positivas: {(df_out['Retorno_ScaleOut_%']>0).mean()*100:.1f}%")
    n_so_melhor = (df_out["Retorno_ScaleOut_%"] > df_out["Retorno_Base_%"]).sum()
    print(f"ScaleOut bateu Base em {n_so_melhor}/{len(df_out)} janelas ({n_so_melhor/len(df_out)*100:.1f}%)")

    print("\nPor regime:")
    for regime in ["BULL", "LATERAL", "BEAR"]:
        sub = df_out[df_out["Regime"] == regime]
        if sub.empty:
            continue
        n = len(sub)
        med_base = sub["Retorno_Base_%"].median()
        med_so = sub["Retorno_ScaleOut_%"].median()
        med_bh = sub["BuyHold_%"].median()
        pct_base_bate_bh = (sub["Retorno_Base_%"] > sub["BuyHold_%"]).mean() * 100
        pct_so_bate_bh = (sub["Retorno_ScaleOut_%"] > sub["BuyHold_%"]).mean() * 100
        pct_so_bate_base = (sub["Retorno_ScaleOut_%"] > sub["Retorno_Base_%"]).mean() * 100
        dd_base = sub["DD_Base_%"].median()
        dd_so = sub["DD_ScaleOut_%"].median()
        print(f"  {regime:<8} ({n:3d} janelas): B&H mediana {med_bh:+7.1f}% | "
              f"Base {med_base:+7.1f}% (bate B&H {pct_base_bate_bh:5.1f}%, DD med {dd_base:5.1f}%) | "
              f"ScaleOut {med_so:+7.1f}% (bate B&H {pct_so_bate_bh:5.1f}%, DD med {dd_so:5.1f}%) | "
              f"SO>Base em {pct_so_bate_base:5.1f}%")

    print(f"\nSalvo: comparativo_v4_vs_v6c_scaleout_walkforward.csv ({len(df_out)} linhas)")


if __name__ == "__main__":
    main()
