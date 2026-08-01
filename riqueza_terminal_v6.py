# -*- coding: utf-8 -*-
# RIQUEZA TERMINAL -- reavaliacao das variantes v6 por MONTANTE FINAL COMPOSTO
# ----------------------------------------------------------------------------
# MOTIVACAO (correcao metodologica pedida pelo usuario): todo o relatorio v6
# comparou variantes por MEDIANA POR REGIME e taxa de acerto. Isso nao e como
# o dinheiro se acumula -- medianas nao compoem. Uma variante pode ganhar em
# 3 de 3 medianas e terminar com menos dinheiro que outra, dependendo da
# SEQUENCIA e da FREQUENCIA dos regimes.
#
# Este script responde a pergunta certa: partindo de $10.000 e rodando 100% da
# estrategia, quanto se acumula ao final? Compoe CRONOLOGICAMENTE a curva de
# capital do PORTFOLIO (nao media de ativos isolados).
#
# PARTE A deste script. Partes B (formula) e C (Monte Carlo) em
# riqueza_terminal_v6_projecao.py.
#
# So periodo de DESENVOLVIMENTO -- holdout continua LACRADO.
#
# Como rodar:
#   .venv\Scripts\python.exe riqueza_terminal_v6.py

import sys
import warnings
import numpy as np
import pandas as pd

from config_v4 import (
    ATIVOS_PORTFOLIO_V4, ATIVOS_LIQUIDOS, CAPITAL_INICIAL, CANDLES_POR_DIA,
    carregar_dados, separar_periodos, classificar_liquidez, RECEITA_ROBUSTA, grupo_ouro,
)
from estrategia_core import (
    calcular_sinais, simular_posicao, simular_posicao_scale_out,
)
import walkforward_v6c_scaleout as wf
from walkforward_v6d_scaleout_btc import classificar_regime_btc
from walkforward_v6g_scaleout_agressivo import equity_scale_out_sized
from walkforward_v6i_volume_entrada import carregar_dados_com_volume, VOL_PERIODO, VOL_MULTIPLICADOR

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

CAPITAL_REPORTE = 10_000.0  # capital inicial usado no relatorio (o motor usa CAPITAL_INICIAL=1000 e normalizamos)
FRACAO_SAIDA_PARCIAL = 0.5
FATORES_AGRESSIVO = {"BULL": 1.5, "LATERAL": 1.0, "BEAR": 0.5}
FATORES_FILTRADO = {"BULL": 1.0, "LATERAL": 1.0, "BEAR": 0.5}
RISCO_VOL_TARGET = 0.15  # R=15%, o otimo ja documentado na Fase 2B


# ─────────────────────────────────────────────────────────────────────────────
# Sizing por vol-targeting (reimplementado aqui a partir da logica ja
# documentada em vol_targeting_v4.py: qty = (capital * R) / stop_dist)
# ─────────────────────────────────────────────────────────────────────────────

def equity_vol_targeted(eventos, fechamento, taxa, slippage, risco_frac, capital_inicial):
    n = len(fechamento)
    capital_cash = capital_inicial
    qtd = 0.0
    posicionado = False
    equity = np.empty(n)
    equity[0] = capital_inicial
    ev_idx = 0
    n_ev = len(eventos)

    for i in range(1, n):
        if ev_idx < n_ev and eventos[ev_idx][1] == i:
            ev = eventos[ev_idx]
            tipo, _idx, preco_ev, stop_ev = ev[0], ev[1], ev[2], ev[3]
            if tipo == "entrada":
                stop_dist = preco_ev - stop_ev
                if stop_dist > 0:
                    capital_total = capital_cash
                    qtd_raw = (capital_total * risco_frac) / stop_dist
                    capital_a_alocar = qtd_raw * preco_ev
                    qtd = qtd_raw * (1 - taxa)
                    capital_cash = capital_total - capital_a_alocar
                    posicionado = True
            else:
                preco_saida = preco_ev * (1 - slippage)
                capital_cash += (qtd * preco_saida) * (1 - taxa)
                qtd = 0.0
                posicionado = False
            ev_idx += 1
        equity[i] = capital_cash if not posicionado else (capital_cash + qtd * fechamento[i])
    return equity


def equity_sized_por_regime(eventos, fechamento, t_abert, btc_close, fatores, taxa, slippage, capital_inicial):
    """Sizing por regime BTC pra eventos SEM saida parcial (usa a mesma
    mecanica de equity_scale_out_sized, que ja trata os dois casos)."""
    return equity_scale_out_sized(eventos, fechamento, t_abert, btc_close, fatores, taxa, slippage, capital_inicial)


# ─────────────────────────────────────────────────────────────────────────────
# Gera a serie de retornos DIARIOS de cada variante, por ativo
# ─────────────────────────────────────────────────────────────────────────────

def retornos_diarios_por_variante(ativo, interval_str, btc_close):
    """Devolve dict {variante: pd.Series de retornos diarios} + a serie de
    retorno diario do buy&hold, tudo indexado por data (periodo de dev)."""
    grupo = grupo_ouro(ativo)
    params = RECEITA_ROBUSTA[grupo]
    info_liq = classificar_liquidez(ativo)
    taxa, slippage = info_liq["taxa"], info_liq["slippage"]

    # dados COM volume (necessario pra variante de filtro de volume);
    # carregar_dados_com_volume tem o mesmo OHLC de carregar_dados
    df = carregar_dados_com_volume(ativo, interval_str)
    if df.empty or len(df) < 200:
        return None

    periodos = separar_periodos(df["t_abert"])
    idx_dev_fim = periodos["idx_dev_fim"]
    if idx_dev_fim < 200:
        return None
    df_dev = df.iloc[:idx_dev_fim].reset_index(drop=True)

    df_fast = wf.montar_df_fast(df_dev, params)
    maxima = df_dev["maxima"].values
    volume = df_dev["volume"]
    media_vol = volume.rolling(VOL_PERIODO).mean()
    volume_confirma = (volume > VOL_MULTIPLICADOR * media_vol).fillna(False).values

    mr, ml, mf, ap = params["media_rapida"], params["media_lenta"], params["media_filtro"], params["atr_periodo"]
    m_rapida = df_fast[f"ma_{mr}"]
    m_lenta = df_fast[f"ma_{ml}"]
    m_filtro = df_fast[f"ma_f_{mf}"]
    abertura = df_fast["abertura"]
    minima = df_fast["minima"]
    fechamento = df_fast["fechamento"]
    atr = df_fast[f"atr_{ap}"]
    t_abert = df_fast["t_abert"]
    multi = params["atr_multiplicador"]

    compra, venda = calcular_sinais(m_rapida, m_lenta, m_filtro, fechamento)
    compra_vol = compra & volume_confirma

    ev_base, _ = simular_posicao(abertura, minima, atr, compra, venda, multi, slippage)
    ev_so, _ = simular_posicao_scale_out(abertura, minima, atr, compra, venda, multi, FRACAO_SAIDA_PARCIAL, slippage)
    ev_vol, _ = simular_posicao(abertura, minima, atr, compra_vol, venda, multi, slippage)

    curvas = {}
    curvas["Base"], _ = wf._equity_de_eventos_base(ev_base, fechamento, taxa, slippage, CAPITAL_INICIAL)
    curvas["ScaleOut"], _ = wf._equity_de_eventos_scale_out(ev_so, fechamento, taxa, slippage, CAPITAL_INICIAL)
    curvas["FiltroVolume"], _ = wf._equity_de_eventos_base(ev_vol, fechamento, taxa, slippage, CAPITAL_INICIAL)
    curvas["VolTarget15"] = equity_vol_targeted(ev_base, fechamento, taxa, slippage, RISCO_VOL_TARGET, CAPITAL_INICIAL)
    curvas["BTC_Filtrado"] = equity_sized_por_regime(ev_base, fechamento, t_abert, btc_close, FATORES_FILTRADO, taxa, slippage, CAPITAL_INICIAL)
    curvas["BTC_Agressivo"] = equity_sized_por_regime(ev_base, fechamento, t_abert, btc_close, FATORES_AGRESSIVO, taxa, slippage, CAPITAL_INICIAL)
    curvas["ScaleOut_Agressivo"] = equity_scale_out_sized(ev_so, fechamento, t_abert, btc_close, FATORES_AGRESSIVO, taxa, slippage, CAPITAL_INICIAL)

    datas = pd.to_datetime(t_abert)
    out = {}
    for nome, eq in curvas.items():
        s = pd.Series(eq, index=datas).resample("1D").last().dropna()
        out[nome] = s.pct_change().dropna()

    preco_diario = pd.Series(fechamento, index=datas).resample("1D").last().dropna()
    out["BuyHold"] = preco_diario.pct_change().dropna()
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Combina em portfolio (inverse-vol) e mede riqueza terminal
# ─────────────────────────────────────────────────────────────────────────────

def pesos_inverse_vol(ret_por_ativo: dict) -> pd.Series:
    """Mesma metodologia de portfolio_v4.py / app_v4._pesos_inverse_vol:
    vol diaria na janela COMUM a todos, peso_i = (1/vol_i)/soma."""
    datas = None
    for s in ret_por_ativo.values():
        datas = s.index if datas is None else datas.intersection(s.index)
    if datas is None or len(datas) < 5:
        return pd.Series(dtype=float)
    datas = datas.sort_values()
    dfret = pd.DataFrame({a: s.reindex(datas) for a, s in ret_por_ativo.items()})
    vol = dfret.std(ddof=1)
    inv = 1.0 / vol
    return inv / inv.sum()


def metricas_riqueza(ret_portfolio: pd.Series, capital_inicial=CAPITAL_REPORTE) -> dict:
    """Composicao CRONOLOGICA real -- e isto que responde 'quanto acumulei'."""
    if len(ret_portfolio) < 30:
        return None
    eq = capital_inicial * (1 + ret_portfolio).cumprod()
    capital_final = float(eq.iloc[-1])
    dias = (eq.index[-1] - eq.index[0]).days
    anos = dias / 365.25 if dias > 0 else None
    cagr = (capital_final / capital_inicial) ** (1 / anos) - 1 if anos and anos > 0 else None

    running_max = eq.cummax()
    dd = (running_max - eq) / running_max
    max_dd = float(dd.max())

    # maior periodo submerso (dias ate recuperar o topo anterior)
    submerso = (eq < running_max)
    maior_submerso = 0
    atual = 0
    for v in submerso:
        atual = atual + 1 if v else 0
        maior_submerso = max(maior_submerso, atual)

    return {
        "capital_final": round(capital_final, 2),
        "multiplo": round(capital_final / capital_inicial, 2),
        "cagr_pct": round(cagr * 100, 2) if cagr is not None else None,
        "max_dd_pct": round(max_dd * 100, 2),
        "maior_submerso_dias": int(maior_submerso),
        "anos": round(anos, 2) if anos else None,
        "ini": eq.index[0].date(), "fim": eq.index[-1].date(),
        "equity": eq,
    }


def combinar_portfolio(ret_por_ativo_por_variante: dict, variante: str, pesos: pd.Series) -> pd.Series:
    """Retorno diario do portfolio para uma variante, na janela comum."""
    series = {a: d[variante] for a, d in ret_por_ativo_por_variante.items() if variante in d}
    if not series:
        return pd.Series(dtype=float)
    datas = None
    for s in series.values():
        datas = s.index if datas is None else datas.intersection(s.index)
    if datas is None or len(datas) < 30:
        return pd.Series(dtype=float)
    datas = datas.sort_values()
    dfret = pd.DataFrame({a: s.reindex(datas) for a, s in series.items()}).fillna(0.0)
    p = pesos.reindex(dfret.columns).dropna()
    if p.empty:
        return pd.Series(dtype=float)
    p = p / p.sum()
    return (dfret[p.index] * p).sum(axis=1)


VARIANTES = ["Base", "ScaleOut", "FiltroVolume", "VolTarget15",
             "BTC_Filtrado", "BTC_Agressivo", "ScaleOut_Agressivo", "BuyHold"]


def rodar_universo(ativos: dict, rotulo: str, btc_close, resultados_acc: list):
    print(f"\n{'=' * 100}")
    print(f"JANELA: {rotulo}  ({len(ativos)} ativos)")
    print("=" * 100)

    ret_por_ativo = {}
    for ativo, interval_str in ativos.items():
        r = retornos_diarios_por_variante(ativo, interval_str, btc_close)
        if r is None:
            print(f"  [pulado] {ativo}")
            continue
        ret_por_ativo[ativo] = r

    if len(ret_por_ativo) < 2:
        print("  Dados insuficientes.")
        return

    pesos = pesos_inverse_vol({a: d["Base"] for a, d in ret_por_ativo.items()})
    if pesos.empty:
        print("  Nao foi possivel calcular pesos (janela comum curta demais).")
        return

    print(f"\n  {'Variante':<20} {'Capital final':>14} {'Multiplo':>10} {'CAGR':>9} {'DD max':>9} {'Submerso':>10}")
    print("  " + "-" * 78)

    for variante in VARIANTES:
        ret_p = combinar_portfolio(ret_por_ativo, variante, pesos)
        if ret_p.empty:
            continue
        m = metricas_riqueza(ret_p)
        if m is None:
            continue
        print(f"  {variante:<20} ${m['capital_final']:>13,.0f} {m['multiplo']:>9.2f}x "
              f"{m['cagr_pct']:>8.1f}% {m['max_dd_pct']:>8.1f}% {m['maior_submerso_dias']:>8}d")
        resultados_acc.append({
            "Janela": rotulo, "Variante": variante,
            "Capital_Final": m["capital_final"], "Multiplo": m["multiplo"],
            "CAGR_%": m["cagr_pct"], "DD_Max_%": m["max_dd_pct"],
            "Submerso_dias": m["maior_submerso_dias"],
            "Anos": m["anos"], "Inicio": m["ini"], "Fim": m["fim"],
            "N_Ativos": len(ret_por_ativo),
        })

    desta_janela = [r for r in resultados_acc if r["Janela"] == rotulo]
    if desta_janela:
        p = desta_janela[0]
        print(f"\n  Periodo: {p['Inicio']} a {p['Fim']} "
              f"({p['Anos']} anos) · capital inicial ${CAPITAL_REPORTE:,.0f}")


def main():
    print("=" * 100)
    print("PARTE A -- RIQUEZA TERMINAL EMPIRICA (composicao cronologica do portfolio)")
    print("Periodo de DESENVOLVIMENTO apenas. Holdout LACRADO.")
    print("=" * 100)

    print("\nCarregando BTC (historico completo) para classificacao de regime...")
    df_btc_full = carregar_dados("BTCUSDT", "6h")
    btc_close = pd.Series(
        df_btc_full["fechamento"].values,
        index=pd.to_datetime(df_btc_full["t_abert"].values),
    ).sort_index()

    resultados = []

    # (a) so veteranas -- janela longa, a mais informativa
    ativos_vet = {a: i for a, i in ATIVOS_PORTFOLIO_V4.items() if a in ATIVOS_LIQUIDOS}
    rodar_universo(ativos_vet, "A) Veteranas (janela longa)", btc_close, resultados)

    # (b) 22 ativos -- janela curta comum (rotulada claramente)
    rodar_universo(ATIVOS_PORTFOLIO_V4, "B) 22 ativos (janela CURTA comum)", btc_close, resultados)

    df_out = pd.DataFrame(resultados)
    df_out.to_csv("riqueza_terminal_v6_parteA.csv", index=False)
    print(f"\n\nSalvo: riqueza_terminal_v6_parteA.csv ({len(df_out)} linhas)")
    print("\nATENCAO: a janela (B) cobre um periodo curto e majoritariamente de baixa")
    print("(limitada pela listagem do ativo mais novo) -- nao comparar o capital final")
    print("de (A) com (B) diretamente; sao horizontes diferentes.")


if __name__ == "__main__":
    main()
