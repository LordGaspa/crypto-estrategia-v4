# -*- coding: utf-8 -*-
# CHECAGEM CRITICA -- o modo AGRESSIVO (1.5x em BULL) exige margem/alavancagem?
# ----------------------------------------------------------------------------
# O projeto inteiro do usuario e Binance SPOT (sem alavancagem). O modo
# Agressivo aloca 1.5x o capital por trade em regime BULL. A pergunta pratica:
# isso estoura os 100% do capital (exigindo margem, que Spot nao tem) ou cabe
# dentro do caixa porque nem todos os 8-22 ativos estao posicionados ao mesmo
# tempo?
#
# Mede a EXPOSICAO AGREGADA do portfolio (soma dos pesos * fator de sizing dos
# ativos posicionados) candle a candle, e reporta quanto tempo passa acima de
# 100%.
#
# Como rodar:
#   .venv\Scripts\python.exe riqueza_terminal_v6_exposicao.py

import sys
import warnings
import numpy as np
import pandas as pd

from config_v4 import (
    ATIVOS_PORTFOLIO_V4, ATIVOS_LIQUIDOS, CANDLES_POR_DIA,
    carregar_dados, separar_periodos, classificar_liquidez, RECEITA_ROBUSTA, grupo_ouro,
)
from estrategia_core import calcular_sinais, simular_posicao
import walkforward_v6c_scaleout as wf
from walkforward_v6d_scaleout_btc import classificar_regime_btc

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

FATORES_AGRESSIVO = {"BULL": 1.5, "LATERAL": 1.0, "BEAR": 0.5}


def serie_exposicao_ativo(ativo, interval_str, btc_close, fatores):
    """Devolve serie diaria com o FATOR de exposicao do ativo (0 se fora da
    posicao, senao o fator do regime BTC no momento da entrada)."""
    grupo = grupo_ouro(ativo)
    params = RECEITA_ROBUSTA[grupo]
    info_liq = classificar_liquidez(ativo)

    df = carregar_dados(ativo, interval_str)
    if df.empty:
        return None
    periodos = separar_periodos(df["t_abert"])
    idx_dev_fim = periodos["idx_dev_fim"]
    if idx_dev_fim < 200:
        return None
    df_dev = df.iloc[:idx_dev_fim].reset_index(drop=True)
    df_fast = wf.montar_df_fast(df_dev, params)

    mr, ml, mf, ap = params["media_rapida"], params["media_lenta"], params["media_filtro"], params["atr_periodo"]
    compra, venda = calcular_sinais(
        df_fast[f"ma_{mr}"], df_fast[f"ma_{ml}"], df_fast[f"ma_f_{mf}"], df_fast["fechamento"]
    )
    eventos, _ = simular_posicao(
        df_fast["abertura"], df_fast["minima"], df_fast[f"atr_{ap}"],
        compra, venda, params["atr_multiplicador"], info_liq["slippage"],
    )

    n = len(df_fast["fechamento"])
    t_abert = pd.to_datetime(df_fast["t_abert"])
    exposicao = np.zeros(n)
    posicionado = False
    fator_atual = 0.0
    ev_idx = 0
    n_ev = len(eventos)
    for i in range(n):
        if ev_idx < n_ev and eventos[ev_idx][1] == i:
            tipo = eventos[ev_idx][0]
            if tipo == "entrada":
                regime = classificar_regime_btc(t_abert[i], btc_close)
                fator_atual = fatores[regime]
                posicionado = True
            else:
                posicionado = False
                fator_atual = 0.0
            ev_idx += 1
        exposicao[i] = fator_atual if posicionado else 0.0

    return pd.Series(exposicao, index=t_abert).resample("1D").last().ffill()


def main():
    print("=" * 100)
    print("CHECAGEM -- o modo AGRESSIVO exige margem (alavancagem) ou cabe no capital?")
    print("=" * 100)

    df_btc = carregar_dados("BTCUSDT", "6h")
    btc_close = pd.Series(
        df_btc["fechamento"].values, index=pd.to_datetime(df_btc["t_abert"].values)
    ).sort_index()

    pesos = pd.read_csv("portfolio_v4_pesos.csv")
    pesos_map = dict(zip(pesos["Ativo"], pesos["Peso_Portfolio_%"] / 100.0))

    for rotulo, universo in [
        ("Veteranas (8)", {a: i for a, i in ATIVOS_PORTFOLIO_V4.items() if a in ATIVOS_LIQUIDOS}),
        ("Portfolio completo (22)", ATIVOS_PORTFOLIO_V4),
    ]:
        print(f"\n{'-' * 100}\n{rotulo}\n{'-' * 100}")
        series = {}
        for ativo, interval_str in universo.items():
            s = serie_exposicao_ativo(ativo, interval_str, btc_close, FATORES_AGRESSIVO)
            if s is not None:
                series[ativo] = s
        if not series:
            print("  sem dados")
            continue

        datas = None
        for s in series.values():
            datas = s.index if datas is None else datas.intersection(s.index)
        datas = datas.sort_values()
        df_exp = pd.DataFrame({a: s.reindex(datas).fillna(0.0) for a, s in series.items()})

        # renormaliza os pesos para o universo em questao
        p = pd.Series({a: pesos_map.get(a, 0.0) for a in df_exp.columns})
        if p.sum() == 0:
            p = pd.Series(1.0 / len(df_exp.columns), index=df_exp.columns)
        p = p / p.sum()

        exposicao_total = (df_exp * p).sum(axis=1)

        print(f"  Periodo: {datas[0].date()} a {datas[-1].date()} ({len(datas)} dias)")
        print(f"  Exposicao agregada do portfolio (1.00 = 100%% do capital):")
        print(f"    mediana:  {exposicao_total.median():.2f}")
        print(f"    media:    {exposicao_total.mean():.2f}")
        print(f"    maxima:   {exposicao_total.max():.2f}")
        print(f"    p95:      {exposicao_total.quantile(0.95):.2f}")
        pct_acima_100 = float((exposicao_total > 1.0).mean()) * 100
        print(f"    % do tempo ACIMA de 100%% (exigiria margem): {pct_acima_100:.1f}%")
        if pct_acima_100 == 0:
            print("    -> CABE NO CAPITAL: nunca precisa de margem. Compativel com Spot.")
        else:
            print(f"    -> EXIGE MARGEM em {pct_acima_100:.1f}% do tempo. NAO e Spot puro.")


if __name__ == "__main__":
    main()
