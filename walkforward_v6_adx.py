# -*- coding: utf-8 -*-
# WALK-FORWARD V6 -- RECEITA_ROBUSTA vs RECEITA_ROBUSTA + FILTRO DE FORCA DE
# TENDENCIA (ADX) NA SAIDA
# ----------------------------------------------------------------------------
# Motivacao: a fraqueza conhecida da RECEITA_ROBUSTA (v4/v5, ja validada) e
# sair cedo demais em bull forte -- so bate Buy&Hold em 19% das janelas BULL
# (walkforward_robusta_v4.py), porque o cruzamento contrario dispara em
# qualquer pullback, mesmo dentro de uma tendencia ainda viva.
#
# v6 testa uma unica mudanca cirurgica: o cruzamento contrario so fecha a
# posicao se a forca de tendencia (ADX) ja tiver enfraquecido (ADX < 20,
# limiar textbook, NAO ajustado contra estes dados). Entrada, stop ATR e
# parametros da RECEITA_ROBUSTA continuam EXATAMENTE os mesmos -- so a
# condicao de honrar o cruzamento contrario muda. Usa
# estrategia_core.simular_posicao_filtro_adx (nova, aditiva -- nao editou
# simular_posicao existente).
#
# Trailing stop (v3, Fase 2A) ja foi testado e descartado -- isto NAO e
# trailing, e um filtro condicional sobre o MESMO cruzamento contrario.
#
# CORRECAO APLICADA (primeira rodada usava so ADX, sem direcao -- resultado:
# melhorou BULL mas destruiu BEAR, porque uma queda forte tambem tem ADX
# alto e o filtro mantinha posicoes compradas presas em quedas fortes). Agora
# exige ADX alto E +DI>-DI (tendencia forte E de ALTA) pra ignorar o
# cruzamento -- ver simular_posicao_filtro_adx em estrategia_core.py.
#
# So periodo de DESENVOLVIMENTO -- holdout continua travado, nao tocado aqui.
#
# Como rodar:
#   .venv\Scripts\python.exe walkforward_v6_adx.py

import sys
import warnings
import numpy as np
import pandas as pd

from config_v4 import (
    ATIVOS_PORTFOLIO_V4, CAPITAL_INICIAL, CANDLES_POR_DIA,
    carregar_dados, separar_periodos, classificar_liquidez, RECEITA_ROBUSTA, grupo_ouro,
)
from estrategia_core import calcular_sinais, simular_posicao, simular_posicao_filtro_adx

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

JANELA_ANOS = 1
MIN_CANDLES_JANELA = 40
ADX_PERIODO = 14     # padrao textbook (Wilder) -- nao ajustado contra os dados
ADX_LIMIAR = 20.0    # padrao textbook de "tendencia fraca" -- nao ajustado


def calcular_adx(maxima, minima, fechamento, periodo=ADX_PERIODO):
    """ADX de Wilder padrao + DI direcional (+DI/-DI). +DM/-DM, suavizacao de
    Wilder (EWM alpha=1/periodo, matematicamente equivalente a suavizacao de
    Wilder), DX, ADX. Devolve (adx, plus_di, minus_di) -- o v6b precisa da
    direcao (+DI vs -DI), nao so da forca (ADX), pra nao confundir tendencia
    forte de BAIXA com motivo pra segurar uma posicao comprada."""
    maxima = pd.Series(maxima)
    minima = pd.Series(minima)
    fechamento = pd.Series(fechamento)

    up_move = maxima.diff()
    down_move = -minima.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr = pd.concat([
        maxima - minima,
        (maxima - fechamento.shift()).abs(),
        (minima - fechamento.shift()).abs(),
    ], axis=1).max(axis=1)

    atr_wilder = tr.ewm(alpha=1 / periodo, adjust=False).mean()
    plus_dm_smooth = pd.Series(plus_dm).ewm(alpha=1 / periodo, adjust=False).mean()
    minus_dm_smooth = pd.Series(minus_dm).ewm(alpha=1 / periodo, adjust=False).mean()

    with np.errstate(invalid="ignore", divide="ignore"):
        plus_di = 100 * plus_dm_smooth / atr_wilder
        minus_di = 100 * minus_dm_smooth / atr_wilder
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.ewm(alpha=1 / periodo, adjust=False).mean()
    return adx.values, plus_di.values, minus_di.values


def montar_df_fast(df: pd.DataFrame, params: dict) -> dict:
    fast = {
        "abertura": df["abertura"].values,
        "minima": df["minima"].values,
        "maxima": df["maxima"].values,
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
    fast["adx"], fast["plus_di"], fast["minus_di"] = calcular_adx(df["maxima"], df["minima"], df["fechamento"])
    return fast


def _equity_de_eventos(eventos, fechamento, taxa, slippage, capital_inicial):
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
    """Roda BASELINE (simular_posicao) e V6 (simular_posicao_filtro_adx) na
    MESMA janela/dados, devolve os dois resultados lado a lado."""
    mr, ml, mf, ap = params["media_rapida"], params["media_lenta"], params["media_filtro"], params["atr_periodo"]
    m_rapida = df_fast[f"ma_{mr}"][inicio:fim]
    m_lenta = df_fast[f"ma_{ml}"][inicio:fim]
    m_filtro = df_fast[f"ma_f_{mf}"][inicio:fim]
    abertura = df_fast["abertura"][inicio:fim]
    minima = df_fast["minima"][inicio:fim]
    fechamento = df_fast["fechamento"][inicio:fim]
    atr = df_fast[f"atr_{ap}"][inicio:fim]
    adx = df_fast["adx"][inicio:fim]
    plus_di = df_fast["plus_di"][inicio:fim]
    minus_di = df_fast["minus_di"][inicio:fim]
    t_abert = df_fast["t_abert"][inicio:fim]
    multi = params["atr_multiplicador"]

    n = len(fechamento)
    if n < MIN_CANDLES_JANELA:
        return None

    sinais_compra, sinais_venda = calcular_sinais(m_rapida, m_lenta, m_filtro, fechamento)

    eventos_base, _ = simular_posicao(abertura, minima, atr, sinais_compra, sinais_venda, multi, slippage)
    eventos_adx, _ = simular_posicao_filtro_adx(
        abertura, minima, atr, adx, plus_di, minus_di, sinais_compra, sinais_venda, multi, ADX_LIMIAR, slippage
    )

    eq_base, trades_base = _equity_de_eventos(eventos_base, fechamento, taxa, slippage, CAPITAL_INICIAL)
    eq_adx, trades_adx = _equity_de_eventos(eventos_adx, fechamento, taxa, slippage, CAPITAL_INICIAL)

    met_base = _metricas_de_equity(eq_base, abertura, fechamento, t_abert, candles_dia)
    met_adx = _metricas_de_equity(eq_adx, abertura, fechamento, t_abert, candles_dia)

    return {
        "regime": met_base["regime"], "bh_pct": met_base["bh_pct"],
        "retorno_base_pct": met_base["retorno_pct"], "dd_base_pct": met_base["dd_pct"], "trades_base": trades_base,
        "retorno_adx_pct": met_adx["retorno_pct"], "dd_adx_pct": met_adx["dd_pct"], "trades_adx": trades_adx,
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
    print("WALK-FORWARD V6 -- RECEITA_ROBUSTA vs +FILTRO ADX NA SAIDA (22 ativos, janelas anuais)")
    print(f"ADX periodo={ADX_PERIODO}, limiar={ADX_LIMIAR} (padroes textbook, nao ajustados)")
    print("=" * 90)

    for ativo, interval_str in ATIVOS_PORTFOLIO_V4.items():
        grupo = grupo_ouro(ativo)
        params = RECEITA_ROBUSTA[grupo]
        info_liq = classificar_liquidez(ativo)
        candles_dia = CANDLES_POR_DIA[interval_str]

        df = carregar_dados(ativo, interval_str)
        if df.empty:
            print(f"  [SEM DADOS] {ativo}")
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
        print(f"  {'Ano':<6} {'Regime':<8} {'Base%':>8} {'ADX%':>8} {'B&H%':>8} {'Trades_B':>9} {'Trades_ADX':>10}")

        for jan in janelas:
            res = executar_janela_comparativo(
                df_fast, params, jan["idx_ini"], jan["idx_fim"],
                info_liq["taxa"], info_liq["slippage"], candles_dia,
            )
            if res is None:
                continue
            print(f"  {jan['ano']:<6} {res['regime']:<8} {res['retorno_base_pct']:>8.1f} "
                  f"{res['retorno_adx_pct']:>8.1f} {res['bh_pct']:>8.1f} "
                  f"{res['trades_base']:>9} {res['trades_adx']:>10}")
            linhas.append({
                "Ativo": ativo, "Grupo": grupo, "Interval": interval_str, "Ano": jan["ano"],
                "Periodo": jan["periodo"], "Regime": res["regime"],
                "Retorno_Base_%": res["retorno_base_pct"], "Retorno_ADX_%": res["retorno_adx_pct"],
                "BuyHold_%": res["bh_pct"],
                "DD_Base_%": res["dd_base_pct"], "DD_ADX_%": res["dd_adx_pct"],
                "Trades_Base": res["trades_base"], "Trades_ADX": res["trades_adx"],
            })

    df_out = pd.DataFrame(linhas)
    df_out.to_csv("comparativo_v4_vs_v6adx_walkforward.csv", index=False)

    print("\n\n" + "=" * 90)
    print("COMPARATIVO GLOBAL -- RECEITA_ROBUSTA (Base) vs +FILTRO ADX (v6)")
    print("=" * 90)
    print(f"Total de janelas-ativo: {len(df_out)}")
    print(f"Base -- mediana: {df_out['Retorno_Base_%'].median():+.1f}% | "
          f"% positivas: {(df_out['Retorno_Base_%']>0).mean()*100:.1f}%")
    print(f"ADX  -- mediana: {df_out['Retorno_ADX_%'].median():+.1f}% | "
          f"% positivas: {(df_out['Retorno_ADX_%']>0).mean()*100:.1f}%")
    n_adx_melhor = (df_out["Retorno_ADX_%"] > df_out["Retorno_Base_%"]).sum()
    print(f"ADX bateu Base em {n_adx_melhor}/{len(df_out)} janelas ({n_adx_melhor/len(df_out)*100:.1f}%)")

    print("\nPor regime:")
    for regime in ["BULL", "LATERAL", "BEAR"]:
        sub = df_out[df_out["Regime"] == regime]
        if sub.empty:
            continue
        n = len(sub)
        med_base = sub["Retorno_Base_%"].median()
        med_adx = sub["Retorno_ADX_%"].median()
        med_bh = sub["BuyHold_%"].median()
        pct_base_bate_bh = (sub["Retorno_Base_%"] > sub["BuyHold_%"]).mean() * 100
        pct_adx_bate_bh = (sub["Retorno_ADX_%"] > sub["BuyHold_%"]).mean() * 100
        pct_adx_bate_base = (sub["Retorno_ADX_%"] > sub["Retorno_Base_%"]).mean() * 100
        dd_base = sub["DD_Base_%"].median()
        dd_adx = sub["DD_ADX_%"].median()
        print(f"  {regime:<8} ({n:3d} janelas): B&H mediana {med_bh:+7.1f}% | "
              f"Base {med_base:+7.1f}% (bate B&H {pct_base_bate_bh:5.1f}%, DD med {dd_base:5.1f}%) | "
              f"ADX {med_adx:+7.1f}% (bate B&H {pct_adx_bate_bh:5.1f}%, DD med {dd_adx:5.1f}%) | "
              f"ADX>Base em {pct_adx_bate_base:5.1f}%")

    print(f"\nSalvo: comparativo_v4_vs_v6adx_walkforward.csv ({len(df_out)} linhas)")


if __name__ == "__main__":
    main()
