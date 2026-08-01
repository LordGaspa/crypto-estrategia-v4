# -*- coding: utf-8 -*-
# VARIANTE SPOT-COMPATIVEL do sizing agressivo (sem margem)
# ----------------------------------------------------------------------------
# A checagem de exposicao (riqueza_terminal_v6_exposicao.py) mostrou dois fatos:
#   1. O modo AGRESSIVO (1.5x em BULL) estoura 100% do capital em ~28% do
#      tempo -> exige margem, NAO e Spot puro. Custo de juros e risco de
#      liquidacao nao estao modelados em lugar nenhum deste projeto.
#   2. A exposicao agregada MEDIANA e so ~0.49 -- metade do capital fica parada
#      a maior parte do tempo, porque raramente todos os ativos estao
#      posicionados simultaneamente.
#
# (2) abre espaco pra uma versao honesta: alocar MAIS por trade, mas com TETO
# de 100% do capital agregado -- quando o teto morde, reduz proporcionalmente.
# Isso e implementavel em Spot puro.
#
# NOTA DE IMPLEMENTACAO (bug corrigido): o retorno de cada ativo ja vem da
# equity DIMENSIONADA (equity_sized_por_regime), que embute o fator
# corretamente candle a candle. NAO multiplicar o retorno diario pelo fator
# depois -- isso zeraria os dias de SAIDA (fator=0 no fim do dia), removendo
# justamente os dias de stop e inflando o resultado.
#
# So periodo de DESENVOLVIMENTO. Holdout LACRADO.
#
# Como rodar:
#   .venv\Scripts\python.exe riqueza_terminal_v6_spotcap.py

import sys
import warnings
import numpy as np
import pandas as pd

from config_v4 import (
    ATIVOS_PORTFOLIO_V4, ATIVOS_LIQUIDOS, CAPITAL_INICIAL,
    carregar_dados, separar_periodos, classificar_liquidez, RECEITA_ROBUSTA, grupo_ouro,
)
from estrategia_core import calcular_sinais, simular_posicao
import walkforward_v6c_scaleout as wf
from walkforward_v6d_scaleout_btc import classificar_regime_btc
from riqueza_terminal_v6 import (
    pesos_inverse_vol, metricas_riqueza, equity_sized_por_regime, CAPITAL_REPORTE,
)

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ESCALAS = {
    "Base (1.0x sempre)":    {"BULL": 1.0, "LATERAL": 1.0, "BEAR": 1.0},
    "Filtrado (1.0/0.5)":    {"BULL": 1.0, "LATERAL": 1.0, "BEAR": 0.5},
    "Agressivo (1.5/1/0.5)": {"BULL": 1.5, "LATERAL": 1.0, "BEAR": 0.5},
    "Escalado 2.0x bull":    {"BULL": 2.0, "LATERAL": 1.3, "BEAR": 0.5},
    "Escalado 2.5x bull":    {"BULL": 2.5, "LATERAL": 1.6, "BEAR": 0.5},
}


def serie_ativo(ativo, interval_str, btc_close, fatores):
    """Devolve (retorno_diario_JA_DIMENSIONADO, fator_diario_de_exposicao)."""
    grupo = grupo_ouro(ativo)
    params = RECEITA_ROBUSTA[grupo]
    info_liq = classificar_liquidez(ativo)
    taxa, slippage = info_liq["taxa"], info_liq["slippage"]

    df = carregar_dados(ativo, interval_str)
    if df.empty:
        return None
    periodos = separar_periodos(df["t_abert"])
    idx = periodos["idx_dev_fim"]
    if idx < 200:
        return None
    df_dev = df.iloc[:idx].reset_index(drop=True)
    df_fast = wf.montar_df_fast(df_dev, params)

    mr, ml, mf, ap = params["media_rapida"], params["media_lenta"], params["media_filtro"], params["atr_periodo"]
    compra, venda = calcular_sinais(
        df_fast[f"ma_{mr}"], df_fast[f"ma_{ml}"], df_fast[f"ma_f_{mf}"], df_fast["fechamento"]
    )
    eventos, _ = simular_posicao(
        df_fast["abertura"], df_fast["minima"], df_fast[f"atr_{ap}"],
        compra, venda, params["atr_multiplicador"], slippage,
    )

    fechamento = df_fast["fechamento"]
    t_abert_raw = df_fast["t_abert"]
    t_abert = pd.to_datetime(t_abert_raw)
    n = len(fechamento)

    # equity JA dimensionada pelo regime -- o fator entra aqui, corretamente
    eq = equity_sized_por_regime(
        eventos, fechamento, t_abert_raw, btc_close, fatores, taxa, slippage, CAPITAL_INICIAL
    )
    ret_diario = pd.Series(eq, index=t_abert).resample("1D").last().dropna().pct_change().dropna()

    # serie do fator so pra MEDIR exposicao (nao entra no retorno)
    fator_arr = np.zeros(n)
    posicionado, fator_atual = False, 0.0
    ev_idx, n_ev = 0, len(eventos)
    for i in range(n):
        if ev_idx < n_ev and eventos[ev_idx][1] == i:
            if eventos[ev_idx][0] == "entrada":
                fator_atual = fatores[classificar_regime_btc(t_abert[i], btc_close)]
                posicionado = True
            else:
                posicionado, fator_atual = False, 0.0
            ev_idx += 1
        fator_arr[i] = fator_atual if posicionado else 0.0
    fator_diario = pd.Series(fator_arr, index=t_abert).resample("1D").last().ffill()

    return ret_diario, fator_diario


def main():
    print("=" * 100)
    print("VARIANTE SPOT-COMPATIVEL -- sizing escalado COM TETO de 100% do capital")
    print("=" * 100)

    df_btc = carregar_dados("BTCUSDT", "6h")
    btc_close = pd.Series(
        df_btc["fechamento"].values, index=pd.to_datetime(df_btc["t_abert"].values)
    ).sort_index()

    universo = {a: i for a, i in ATIVOS_PORTFOLIO_V4.items() if a in ATIVOS_LIQUIDOS}
    print(f"\nUniverso: {len(universo)} veteranas (janela longa) · capital inicial ${CAPITAL_REPORTE:,.0f}\n")

    linhas = []
    for nome_escala, fatores in ESCALAS.items():
        rets, fats = {}, {}
        for ativo, interval_str in universo.items():
            r = serie_ativo(ativo, interval_str, btc_close, fatores)
            if r is None:
                continue
            rets[ativo], fats[ativo] = r
        if len(rets) < 2:
            continue

        pesos = pesos_inverse_vol(rets)
        datas = None
        for s in rets.values():
            datas = s.index if datas is None else datas.intersection(s.index)
        datas = datas.sort_values()

        df_ret = pd.DataFrame({a: s.reindex(datas).fillna(0.0) for a, s in rets.items()})
        df_fat = pd.DataFrame({a: s.reindex(datas).fillna(0.0) for a, s in fats.items()})
        p = pesos.reindex(df_ret.columns)
        p = p / p.sum()

        # retorno do portfolio: retornos JA dimensionados, so ponderados
        ret_portfolio = (df_ret * p).sum(axis=1)

        # exposicao agregada desejada (pra saber quando o teto morde)
        exposicao = (df_fat * p).sum(axis=1)
        escala_teto = np.minimum(1.0, 1.0 / exposicao.replace(0, np.nan)).fillna(1.0)
        ret_com_teto = ret_portfolio * escala_teto

        m_sem = metricas_riqueza(ret_portfolio)
        m_com = metricas_riqueza(ret_com_teto)
        pct_teto = float((exposicao > 1.0).mean()) * 100

        print(f"{nome_escala}")
        print(f"  exposicao mediana {exposicao.median():.2f} · maxima {exposicao.max():.2f} · "
              f"teto morde em {pct_teto:.1f}% dos dias")
        if m_sem:
            print(f"  SEM teto (exige margem): ${m_sem['capital_final']:>12,.0f}  "
                  f"CAGR {m_sem['cagr_pct']:>6.1f}%  DD {m_sem['max_dd_pct']:>5.1f}%")
        if m_com:
            print(f"  COM teto (Spot puro):    ${m_com['capital_final']:>12,.0f}  "
                  f"CAGR {m_com['cagr_pct']:>6.1f}%  DD {m_com['max_dd_pct']:>5.1f}%")
        print()

        if m_com:
            linhas.append({
                "Escala": nome_escala,
                "Exposicao_mediana": round(float(exposicao.median()), 3),
                "Exposicao_max": round(float(exposicao.max()), 3),
                "Pct_dias_teto": round(pct_teto, 1),
                "Capital_SemTeto": m_sem["capital_final"] if m_sem else None,
                "CAGR_SemTeto_%": m_sem["cagr_pct"] if m_sem else None,
                "DD_SemTeto_%": m_sem["max_dd_pct"] if m_sem else None,
                "Capital_ComTeto": m_com["capital_final"],
                "CAGR_ComTeto_%": m_com["cagr_pct"],
                "DD_ComTeto_%": m_com["max_dd_pct"],
                "Submerso_ComTeto_dias": m_com["maior_submerso_dias"],
            })

    df_out = pd.DataFrame(linhas)
    df_out.to_csv("riqueza_terminal_v6_spotcap.csv", index=False)
    print(f"Salvo: riqueza_terminal_v6_spotcap.csv ({len(df_out)} linhas)")
    print("\nNOTA: 'COM teto' e a unica coluna implementavel em Binance Spot sem")
    print("margem. 'SEM teto' exige emprestimo -- custo de juros e risco de")
    print("liquidacao NAO estao modelados em nenhuma das duas.")


if __name__ == "__main__":
    main()
