# -*- coding: utf-8 -*-
# TESTE DO TRAILING STOP — RECEITA ROBUSTA + TRAILING (Fase 2, Alavanca A)
# ----------------------------------------------------------------------------
# Compara o backtest da RECEITA_ROBUSTA nas mesmas janelas anuais do
# walkforward_robusta_v4.py, mas substituindo a saida (stop fixo + cruzamento
# contrario) por um TRAILING STOP.
#
# Logica do trailing:
#   - Mesma entrada (cruzamento + filtro ATR).
#   - Stop FIXO ativo ate lucro flutuante >= ativacao_x * risco_inicial.
#   - Apos isso, stop SOBE conforme preco sobe: max_desde_entrada - ATR_entrada * mult_trailing.
#   - Saida por cruzamento NAO usada (a posicao so fecha por stop).
#
# Para nao criar overfitting nos parametros do trailing, testamos apenas
# 3 configs pre-definidas e escolhemos 1 como "Equilibrado":
#   DEFENSIVO   : base atual (stop fixo + cruzamento) — sem trailing
#   EQUILIBRADO : trailing ativa apos 1.5x risco, trail = ATR x 3.0
#   AGRESSIVO   : trailing ativa apos 1.0x risco, trail = ATR x 2.0 (mais curto = mais arriscado)
#
# Saidas:
#   trailing_teste_v4_janelas.csv  — (ativo, ano, config, resultado)
#   trailing_teste_v4_resumo.txt   — comparativo por regime
#
# Como rodar:
#   .venv\Scripts\python.exe trailing_teste_v4.py

import sys
import warnings
import numpy as np
import pandas as pd

from config_v4 import (
    ATIVOS_PORTFOLIO_V4,
    CAPITAL_INICIAL,
    CANDLES_POR_DIA,
    carregar_dados,
    separar_periodos,
    classificar_liquidez,
    RECEITA_ROBUSTA,
    grupo_ouro,
)
from estrategia_core import calcular_sinais, simular_posicao, simular_posicao_trailing

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

MIN_CANDLES_JANELA = 40
JANELA_ANOS = 1

CONFIGS = {
    "DEFENSIVO": {
        "trailing": False,
        "descricao": "Stop fixo + saida por cruzamento (receita atual)",
    },
    "EQUILIBRADO": {
        "trailing": True,
        "ativacao_x": 1.5,
        "mult_trailing": 3.0,
        "descricao": "Trailing ativa apos 1.5x risco, trail = ATR x 3.0",
    },
    "AGRESSIVO": {
        "trailing": True,
        "ativacao_x": 1.0,
        "mult_trailing": 2.0,
        "descricao": "Trailing ativa apos 1.0x risco, trail = ATR x 2.0",
    },
}


def montar_df_fast(df: pd.DataFrame, params: dict) -> dict:
    fast = {
        "abertura":   df["abertura"].values,
        "minima":     df["minima"].values,
        "maxima":     df["maxima"].values,
        "fechamento": df["fechamento"].values,
        "t_abert":    df["t_abert"].values,
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


def pl_de_eventos(eventos, fechamento_slice, taxa, slippage, n):
    """Calcula equity curve a partir da lista de eventos do core."""
    capital = CAPITAL_INICIAL
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
        equity[i] = capital if not posicionado else (qtd * fechamento_slice[i])
    return equity, num_trades


def executar_config_janela(df_fast, params, config_key, config, inicio, fim, taxa, slippage):
    mr = params["media_rapida"]
    ml = params["media_lenta"]
    mf = params["media_filtro"]
    ap = params["atr_periodo"]

    m_rapida   = df_fast[f"ma_{mr}"][inicio:fim]
    m_lenta    = df_fast[f"ma_{ml}"][inicio:fim]
    m_filtro   = df_fast[f"ma_f_{mf}"][inicio:fim]
    abertura   = df_fast["abertura"][inicio:fim]
    minima     = df_fast["minima"][inicio:fim]
    maxima     = df_fast["maxima"][inicio:fim]
    fechamento = df_fast["fechamento"][inicio:fim]
    atr        = df_fast[f"atr_{ap}"][inicio:fim]
    multi      = params["atr_multiplicador"]
    n          = len(fechamento)

    if n < MIN_CANDLES_JANELA:
        return None

    sinais_compra, sinais_venda = calcular_sinais(m_rapida, m_lenta, m_filtro, fechamento)

    if not config["trailing"]:
        eventos, _ = simular_posicao(
            abertura, minima, atr, sinais_compra, sinais_venda, multi, slippage
        )
    else:
        eventos, _ = simular_posicao_trailing(
            abertura, minima, maxima, atr, sinais_compra, sinais_venda, multi,
            ativacao_x=config["ativacao_x"],
            mult_trailing=config["mult_trailing"],
            usar_saida_cruzamento=False,
            slippage=slippage,
        )

    equity, num_trades = pl_de_eventos(eventos, fechamento, taxa, slippage, n)

    retorno = (equity[-1] - CAPITAL_INICIAL) / CAPITAL_INICIAL
    running_max = np.maximum.accumulate(equity)
    with np.errstate(invalid="ignore", divide="ignore"):
        dd = (running_max - equity) / running_max
    max_dd = float(np.nanmax(dd))

    bh = float((fechamento[-1] - abertura[0]) / abertura[0]) if abertura[0] > 0 else 0.0
    regime = "BULL" if bh > 0.25 else ("BEAR" if bh < -0.25 else "LATERAL")

    return {
        "retorno_pct":  round(retorno * 100, 2),
        "bh_pct":       round(bh * 100, 2),
        "drawdown_pct": round(max_dd * 100, 2),
        "num_trades":   num_trades,
        "regime":       regime,
    }


def gerar_janelas_anuais(df, idx_dev_fim):
    datas = df["t_abert"].iloc[:idx_dev_fim].reset_index(drop=True)
    n = len(datas)
    if n < MIN_CANDLES_JANELA:
        return []
    data_min = datas.iloc[0]
    data_max = datas.iloc[-1]
    janelas = []
    ano = data_min.year
    while ano <= data_max.year:
        ts_ini = pd.Timestamp(year=ano, month=1, day=1)
        ts_fim = pd.Timestamp(year=ano + JANELA_ANOS, month=1, day=1)
        idx_ini = int(datas.searchsorted(ts_ini))
        idx_fim = min(int(datas.searchsorted(ts_fim)), n)
        if idx_fim - idx_ini >= MIN_CANDLES_JANELA:
            janelas.append({
                "ano": ano,
                "idx_ini": idx_ini,
                "idx_fim": idx_fim,
            })
        ano += 1
    return janelas


def main():
    linhas = []

    print("=" * 72)
    print("TESTE TRAILING STOP — DEFENSIVO vs EQUILIBRADO vs AGRESSIVO")
    print("=" * 72)
    for k, v in CONFIGS.items():
        print(f"  {k}: {v['descricao']}")
    print()

    for ativo, interval_str in ATIVOS_PORTFOLIO_V4.items():
        grupo = grupo_ouro(ativo)
        params = RECEITA_ROBUSTA[grupo]
        info_liq = classificar_liquidez(ativo)

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

        print(f"  {ativo} [{grupo}]")
        for jan in janelas:
            for config_key, config in CONFIGS.items():
                res = executar_config_janela(
                    df_fast, params, config_key, config,
                    jan["idx_ini"], jan["idx_fim"],
                    info_liq["taxa"], info_liq["slippage"],
                )
                if res is None:
                    continue
                linhas.append({
                    "Ativo":       ativo,
                    "Grupo":       grupo,
                    "Ano":         jan["ano"],
                    "Config":      config_key,
                    "Regime":      res["regime"],
                    "Retorno_%":   res["retorno_pct"],
                    "BH_%":        res["bh_pct"],
                    "DD_%":        res["drawdown_pct"],
                    "Trades":      res["num_trades"],
                })

    df_all = pd.DataFrame(linhas)
    df_all.to_csv("trailing_teste_v4_janelas.csv", index=False)

    # ── Relatorio ────────────────────────────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("COMPARATIVO POR REGIME (mediana de retorno % por janela-ativo)")
    print("=" * 72)

    regimes = ["BULL", "LATERAL", "BEAR", "TODOS"]
    configs  = list(CONFIGS.keys())

    header = f"  {'Regime':<10}" + "".join(f" {c:>12}" for c in configs) + "   N"
    print(header)
    print("  " + "-" * (10 + 13 * len(configs) + 4))

    for regime in regimes:
        if regime == "TODOS":
            sub = df_all
        else:
            sub = df_all[df_all["Regime"] == regime]
        if sub.empty:
            continue
        # N = numero de janelas-ativo unicas (pegar do DEFENSIVO como referencia)
        n_jan = len(sub[sub["Config"] == "DEFENSIVO"])
        row = f"  {regime:<10}"
        for c in configs:
            vals = sub[sub["Config"] == c]["Retorno_%"].values
            med = float(np.median(vals)) if len(vals) > 0 else float("nan")
            row += f" {med:>+11.1f}%"
        row += f"   {n_jan}"
        print(row)

    print()
    # % de janelas positivas
    print("% DE JANELAS-ATIVO POSITIVAS (retorno > 0)")
    print("  " + "-" * (10 + 13 * len(configs) + 4))
    for regime in regimes:
        if regime == "TODOS":
            sub = df_all
        else:
            sub = df_all[df_all["Regime"] == regime]
        if sub.empty:
            continue
        n_jan = len(sub[sub["Config"] == "DEFENSIVO"])
        row = f"  {regime:<10}"
        for c in configs:
            vals = sub[sub["Config"] == c]["Retorno_%"].values
            pct = float(np.mean(vals > 0) * 100) if len(vals) > 0 else float("nan")
            row += f" {pct:>11.1f}%"
        row += f"   {n_jan}"
        print(row)

    print()
    # Drawdown maximo medio
    print("DRAWDOWN MEDIO (media de DD% por janela-ativo)")
    print("  " + "-" * (10 + 13 * len(configs) + 4))
    for regime in regimes:
        if regime == "TODOS":
            sub = df_all
        else:
            sub = df_all[df_all["Regime"] == regime]
        if sub.empty:
            continue
        n_jan = len(sub[sub["Config"] == "DEFENSIVO"])
        row = f"  {regime:<10}"
        for c in configs:
            vals = sub[sub["Config"] == c]["DD_%"].values
            med = float(np.mean(vals)) if len(vals) > 0 else float("nan")
            row += f" {med:>+11.1f}%"
        row += f"   {n_jan}"
        print(row)

    print()
    # Bate B&H?
    print("% QUE BATE B&H (retorno_strat > retorno_bh)")
    print("  " + "-" * (10 + 13 * len(configs) + 4))
    for regime in regimes:
        if regime == "TODOS":
            sub = df_all
        else:
            sub = df_all[df_all["Regime"] == regime]
        if sub.empty:
            continue
        n_jan = len(sub[sub["Config"] == "DEFENSIVO"])
        row = f"  {regime:<10}"
        for c in configs:
            cs = sub[sub["Config"] == c]
            if cs.empty:
                row += "         N/A"
                continue
            pct = float(np.mean(cs["Retorno_%"].values > cs["BH_%"].values) * 100)
            row += f" {pct:>11.1f}%"
        row += f"   {n_jan}"
        print(row)

    # ── Analise por grupo ─────────────────────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("POR GRUPO (veterana / nova)")
    print("=" * 72)
    for grupo in ["veterana", "nova"]:
        g = df_all[df_all["Grupo"] == grupo]
        if g.empty:
            continue
        print(f"\nGrupo '{grupo}':")
        for c in configs:
            vals = g[g["Config"] == c]["Retorno_%"].values
            if len(vals) == 0:
                continue
            print(f"  {c:<12}: mediana {np.median(vals):+.1f}% | "
                  f"pior {np.min(vals):+.1f}% | melhor {np.max(vals):+.1f}% | "
                  f"{int(np.sum(vals>0))}/{len(vals)} positivas")

    # ── Qual e o "Equilibrado" correto? ─────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("VEREDICTO: TRAILING STOP AJUDA?")
    print("=" * 72)
    bull  = df_all[df_all["Regime"] == "BULL"]
    bear  = df_all[df_all["Regime"] == "BEAR"]
    later = df_all[df_all["Regime"] == "LATERAL"]

    for c in ["EQUILIBRADO", "AGRESSIVO"]:
        def_bull  = bull[bull["Config"] == "DEFENSIVO"]["Retorno_%"].values
        cfg_bull  = bull[bull["Config"] == c]["Retorno_%"].values
        def_bear  = bear[bear["Config"] == "DEFENSIVO"]["Retorno_%"].values
        cfg_bear  = bear[bear["Config"] == c]["Retorno_%"].values
        def_lat   = later[later["Config"] == "DEFENSIVO"]["Retorno_%"].values
        cfg_lat   = later[later["Config"] == c]["Retorno_%"].values

        ganho_bull = np.median(cfg_bull) - np.median(def_bull) if len(cfg_bull) else 0
        custo_bear = np.median(def_bear) - np.median(cfg_bear) if len(cfg_bear) else 0
        ganho_lat  = np.median(cfg_lat) - np.median(def_lat) if len(cfg_lat) else 0

        print(f"\n  {c} vs DEFENSIVO:")
        print(f"    BULL:    {'+' if ganho_bull >= 0 else ''}{ganho_bull:.1f}pp mediana "
              f"({'MELHORA' if ganho_bull > 0 else 'PIORA'} em alta)")
        print(f"    LATERAL: {'+' if ganho_lat >= 0 else ''}{ganho_lat:.1f}pp mediana "
              f"({'MELHORA' if ganho_lat > 0 else 'PIORA'} em lateral)")
        print(f"    BEAR:    {'-' if custo_bear >= 0 else '+'}{custo_bear:.1f}pp mediana "
              f"({'custo em baixa' if custo_bear > 0 else 'TAMBEM MELHORA em baixa'})")

    print(f"\nSalvo: trailing_teste_v4_janelas.csv ({len(df_all)} linhas)")
    print("\nFase 2A concluida.")


if __name__ == "__main__":
    main()
