# -*- coding: utf-8 -*-
"""
ALAVANCA B — VOL-TARGETING (Fase 2 do PLANO_EVOLUCAO)
--------------------------------------------------------------
Testa se dimensionar cada posição pelo ATR atual melhora o Sharpe
sem sacrificar muito o retorno total.

Lógica atual (baseline):
  - 100% do capital entra em cada trade
  - Risco por trade = ATR × mult / preço_entrada (varia com a volatilidade)
  - Em mercados voláteis → risco alto; em calmos → risco baixo

Vol-targeting (esta alavanca):
  - Fixa a FRAÇÃO DO CAPITAL em risco por trade em R
  - Unidades compradas = (capital × R) / (ATR × mult)
  - Capital deployado = unidades × preço_entrada (pode ser < ou > 100% do capital)
  - Em mercados voláteis → posição menor, menos capital em risco
  - Em mercados calmos → posição maior, mais capital aproveitado

Testar R ∈ {2%, 5%, 10%, 15%, 20%, 30%} vs baseline.
Baseline: R implícito = (ATR × mult) / preço_entrada (variável).

Métricas de comparação: Calmar, Sharpe, retorno total, drawdown.
Portfólio: médias ponderadas pelos pesos de portfolio_v4_pesos.csv.

Saídas:
  vol_targeting_v4_ativos.csv    — por ativo × R
  vol_targeting_v4_portfolio.csv — portfólio ponderado por R

Como rodar:
  .venv\\Scripts\\python.exe vol_targeting_v4.py
"""

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
from estrategia_core import calcular_sinais, simular_posicao

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

PESOS_CSV = "portfolio_v4_pesos.csv"

# Frações de capital em risco por trade a testar
# "baseline" = modelo atual (100% do capital; risco implícito varia com ATR)
RISCOS = [0.02, 0.05, 0.10, 0.15, 0.20, 0.30]
ROTULOS = {r: f"R={int(r*100)}%" for r in RISCOS}
ROTULOS["baseline"] = "Baseline (100%)"


# ─────────────────────────────────────────────────────────────────────────────
# Indicadores
# ─────────────────────────────────────────────────────────────────────────────

def montar_df_fast(df: pd.DataFrame, params: dict) -> dict:
    fast = {
        "abertura":   df["abertura"].values,
        "minima":     df["minima"].values,
        "fechamento": df["fechamento"].values,
        "t_abert":    df["t_abert"].values,
    }
    for m in {params["media_rapida"], params["media_lenta"]}:
        fast[f"ma_{m}"] = df["fechamento"].rolling(m).mean().values
    fast[f"ma_f_{params['media_filtro']}"] = (
        df["fechamento"].rolling(params["media_filtro"]).mean().values
    )
    tr = pd.concat(
        [
            df["maxima"] - df["minima"],
            (df["maxima"] - df["fechamento"].shift()).abs(),
            (df["minima"] - df["fechamento"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    fast[f"atr_{params['atr_periodo']}"] = tr.rolling(params["atr_periodo"]).mean().values
    return fast


# ─────────────────────────────────────────────────────────────────────────────
# Equity com vol-targeting
# ─────────────────────────────────────────────────────────────────────────────

def simular_equity_vol_targeted(
    eventos: list,
    fechamento: np.ndarray,
    risco_frac: float,  # fração do capital a arriscar por trade
    taxa: float,
    slippage: float,
) -> np.ndarray:
    """Curva de equity com posição dimensionada pelo ATR (vol-targeting).

    Para cada entrada:
      stop_dist = preco_entrada - stop  (do evento core, inclui slippage de entrada)
      units_recebidas = (capital × risco_frac) / stop_dist
      capital_deployado = units_recebidas × preco_entrada / (1 - taxa)
      capital_cash = capital − capital_deployado   [negativo = alavancagem]
    """
    n = len(fechamento)
    equity = np.empty(n)
    capital = float(CAPITAL_INICIAL)
    equity[0] = capital

    # Mapear índice → evento (entrada) e índice → (preço_saida)
    ev_entry = {}   # idx → (preco_entry, stop)
    ev_exit  = {}   # idx → preco_bruto
    for ev in eventos:
        tipo, idx, preco, stop = ev
        if tipo == "entrada":
            ev_entry[idx] = (preco, stop)
        else:
            ev_exit[idx] = preco

    posicionado  = False
    qty          = 0.0
    capital_cash = 0.0

    for i in range(1, n):
        if i in ev_entry and not posicionado:
            preco_entry, stop = ev_entry[i]
            stop_dist = preco_entry - stop   # distância em preço até o stop
            if stop_dist <= 0 or not np.isfinite(stop_dist):
                # ATR inválido — pula entrada
                continue

            # Unidades recebidas (após taxa de entrada)
            qty_raw = (capital * risco_frac) / stop_dist   # unidades antes da taxa
            qty = qty_raw * (1 - taxa)                      # unidades recebidas
            # Gross cost (quanto de capital sai) = unidades brutas × preço
            capital_to_deploy = qty_raw * preco_entry
            capital_cash = capital - capital_to_deploy
            posicionado  = True

        elif i in ev_exit and posicionado:
            preco_bruto = ev_exit[i]
            preco_eff   = preco_bruto * (1 - slippage)
            capital     = capital_cash + (qty * preco_eff) * (1 - taxa)
            posicionado  = False

        equity[i] = (capital_cash + qty * fechamento[i]) if posicionado else capital

    return equity


def simular_equity_baseline(
    eventos: list,
    fechamento: np.ndarray,
    taxa: float,
    slippage: float,
) -> np.ndarray:
    """Curva de equity do modelo atual (100% do capital por trade)."""
    n = len(fechamento)
    equity = np.empty(n)
    capital = float(CAPITAL_INICIAL)
    equity[0] = capital

    ev_entry = {}
    ev_exit  = {}
    for ev in eventos:
        tipo, idx, preco, _stop = ev
        if tipo == "entrada":
            ev_entry[idx] = preco
        else:
            ev_exit[idx] = preco

    posicionado = False
    qty         = 0.0

    for i in range(1, n):
        if i in ev_entry and not posicionado:
            preco_entry = ev_entry[i]
            qty         = (capital / preco_entry) * (1 - taxa)
            posicionado = True

        elif i in ev_exit and posicionado:
            preco_eff   = ev_exit[i] * (1 - slippage)
            capital     = (qty * preco_eff) * (1 - taxa)
            posicionado = False

        equity[i] = qty * fechamento[i] if posicionado else capital

    return equity


# ─────────────────────────────────────────────────────────────────────────────
# Métricas
# ─────────────────────────────────────────────────────────────────────────────

def calcular_metricas(equity: np.ndarray, t_abert: np.ndarray, candles_dia: int) -> dict:
    n = len(equity)
    cf = float(equity[-1])
    ret = (cf - CAPITAL_INICIAL) / CAPITAL_INICIAL
    running_max = np.maximum.accumulate(equity)
    with np.errstate(invalid="ignore", divide="ignore"):
        dd_s = (running_max - equity) / running_max
    max_dd = float(np.nanmax(dd_s)) if n > 1 else 0.0
    dias = float((t_abert[-1] - t_abert[0]) / np.timedelta64(1, "D"))
    anos = dias / 365.25 if dias > 0 else None
    ret_a = ((1 + ret) ** (1 / anos) - 1) if anos else None
    calmar = (ret_a / max_dd) if (max_dd > 0 and ret_a is not None) else None
    sharpe = None
    if candles_dia and n >= candles_dia * 10:
        eq_d = equity[::candles_dia]
        rd   = np.diff(eq_d) / eq_d[:-1]
        rd   = rd[np.isfinite(rd)]
        if len(rd) >= 10 and np.std(rd, ddof=1) > 0:
            sharpe = float((np.mean(rd) / np.std(rd, ddof=1)) * np.sqrt(365.0))
    return {
        "retorno_total_pct":  round(ret * 100, 2),
        "retorno_anual_pct":  round(ret_a * 100, 2) if ret_a is not None else None,
        "drawdown_pct":       round(max_dd * 100, 2),
        "calmar":             round(calmar, 4) if calmar is not None else None,
        "sharpe":             round(sharpe, 4) if sharpe is not None else None,
        "capital_final":      round(cf, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    try:
        df_pesos = pd.read_csv(PESOS_CSV)
        pesos_dict = dict(zip(df_pesos["Ativo"], df_pesos["Peso_Portfolio_%"] / 100))
    except FileNotFoundError:
        print(f"[AVISO] {PESOS_CSV} nao encontrado — pesos iguais.")
        pesos_dict = {a: 1.0 / len(ATIVOS_PORTFOLIO_V4) for a in ATIVOS_PORTFOLIO_V4}

    rows_ativos = []
    # chaves = ["baseline"] + RISCOS
    ret_diarios: dict = {"baseline": {}}
    for r in RISCOS:
        ret_diarios[ROTULOS[r]] = {}

    print(f"Vol-targeting: testando {len(RISCOS)} valores de R em {len(ATIVOS_PORTFOLIO_V4)} ativos...\n")

    for ativo, interval_str in ATIVOS_PORTFOLIO_V4.items():
        info   = classificar_liquidez(ativo)
        taxa, slippage = info["taxa"], info["slippage"]
        candles_dia    = CANDLES_POR_DIA[interval_str]
        grupo          = grupo_ouro(ativo)
        params         = RECEITA_ROBUSTA[grupo]

        print(f"  {ativo:<22}", end="  ", flush=True)
        try:
            df = carregar_dados(ativo, interval_str)
            per = separar_periodos(df["t_abert"])
            df_dev  = df.iloc[:per["idx_dev_fim"]].reset_index(drop=True)
            df_fast = montar_df_fast(df_dev, params)

            compra, venda = calcular_sinais(
                df_fast[f"ma_{params['media_rapida']}"],
                df_fast[f"ma_{params['media_lenta']}"],
                df_fast[f"ma_f_{params['media_filtro']}"],
                df_fast["fechamento"],
            )
            eventos, _ = simular_posicao(
                df_fast["abertura"], df_fast["minima"],
                df_fast[f"atr_{params['atr_periodo']}"],
                compra, venda, params["atr_multiplicador"], slippage,
            )
        except Exception as exc:
            print(f"ERRO: {exc}")
            continue

        n_trades   = sum(1 for e in eventos if e[0] == "saida")
        t_abert    = df_fast["t_abert"]
        fechamento = df_fast["fechamento"]
        datas      = pd.to_datetime(t_abert)

        # Baseline
        eq_base = simular_equity_baseline(eventos, fechamento, taxa, slippage)
        m_base  = calcular_metricas(eq_base, t_abert, candles_dia)
        rows_ativos.append({"Ativo": ativo, "Modo": "Baseline", "Grupo": grupo,
                             "n_trades": n_trades, **m_base})
        s = pd.Series(eq_base, index=datas).resample("1D").last().dropna().pct_change().dropna()
        ret_diarios["baseline"][ativo] = s

        # Vol-targeting por R
        for r in RISCOS:
            eq_vt = simular_equity_vol_targeted(eventos, fechamento, r, taxa, slippage)
            m_vt  = calcular_metricas(eq_vt, t_abert, candles_dia)
            rows_ativos.append({"Ativo": ativo, "Modo": ROTULOS[r], "Grupo": grupo,
                                 "n_trades": n_trades, **m_vt})
            s = pd.Series(eq_vt, index=datas).resample("1D").last().dropna().pct_change().dropna()
            ret_diarios[ROTULOS[r]][ativo] = s

        print(f"{n_trades:3d} trades ✓")

    # Portfólio ponderado
    rows_port = []
    modos_ordem = ["Baseline"] + [ROTULOS[r] for r in RISCOS]
    for modo in modos_ordem:
        chave = "baseline" if modo == "Baseline" else modo
        rd_map = ret_diarios[chave]
        ativos_ok = list(rd_map.keys())
        if len(ativos_ok) < 2:
            continue

        datas_comuns = None
        for s in rd_map.values():
            datas_comuns = s.index if datas_comuns is None else datas_comuns.intersection(s.index)
        datas_comuns = datas_comuns.sort_values()

        w = {a: pesos_dict.get(a, 0.0) for a in ativos_ok}
        soma_w = sum(w.values())
        if soma_w > 0:
            w = {a: v / soma_w for a, v in w.items()}

        df_r  = pd.DataFrame({a: rd_map[a].reindex(datas_comuns) for a in ativos_ok}).fillna(0)
        r_pt  = sum(df_r[a] * w[a] for a in ativos_ok)
        eq_pt = (1 + r_pt).cumprod() * CAPITAL_INICIAL
        t_arr = datas_comuns.values.astype("datetime64[ns]")
        m     = calcular_metricas(eq_pt.values, t_arr, candles_dia=1)
        rows_port.append({"Modo": modo, **m})

    df_ativos = pd.DataFrame(rows_ativos)
    df_port   = pd.DataFrame(rows_port)
    df_ativos.to_csv("vol_targeting_v4_ativos.csv", index=False)
    df_port.to_csv("vol_targeting_v4_portfolio.csv", index=False)

    # Resumo
    print("\n" + "=" * 65)
    print("  PORTFÓLIO — PERÍODO DEV (pesos inv-vol, datas comuns a todos)")
    print("=" * 65)
    print(f"{'Modo':<14} {'Ret%':>7} {'Ret/ano%':>9} {'DD%':>7} {'Calmar':>8} {'Sharpe':>8}")
    print("-" * 65)
    for _, row in df_port.iterrows():
        print(
            f"{row['Modo']:<14} {row['retorno_total_pct']:>7.1f}"
            f" {row['retorno_anual_pct']:>9.1f}"
            f" {row['drawdown_pct']:>7.1f}"
            f" {row['calmar']:>8.3f}"
            f" {row['sharpe']:>8.3f}"
        )

    print("\n  MEDIANA POR ATIVO")
    print("-" * 50)
    todos_modos = ["Baseline"] + [ROTULOS[r] for r in RISCOS]
    for modo in todos_modos:
        sub = df_ativos[df_ativos["Modo"] == modo]
        cal = sub["calmar"].dropna()
        sha = sub["sharpe"].dropna()
        print(
            f"  {modo:<14} ret={sub['retorno_total_pct'].median():+6.0f}%"
            f"  DD={sub['drawdown_pct'].median():.0f}%"
            f"  Calmar={cal.median():.3f}"
            f"  Sharpe={sha.median():.3f}"
        )

    # Médias ponderadas pelos pesos (como em backtest_btc_filter)
    print("\n  MÉDIAS PONDERADAS PELOS PESOS DO PORTFÓLIO (histórico completo de cada ativo)")
    print("-" * 65)
    for modo in todos_modos:
        sub = df_ativos[df_ativos["Modo"] == modo].copy()
        sub["peso"] = sub["Ativo"].map(pesos_dict).fillna(0)
        soma = sub["peso"].sum()
        sub["w"] = sub["peso"] / soma if soma > 0 else 0
        wret = (sub["retorno_anual_pct"] * sub["w"]).sum()
        wdd  = (sub["drawdown_pct"]      * sub["w"]).sum()
        wcal = (sub["calmar"].fillna(0)  * sub["w"]).sum()
        wsha = (sub["sharpe"].fillna(0)  * sub["w"]).sum()
        print(
            f"  {modo:<14} ret/ano={wret:+6.1f}%"
            f"  DD={wdd:.1f}%"
            f"  Calmar={wcal:.3f}"
            f"  Sharpe={wsha:.3f}"
        )

    print(f"\n✅ Salvo: vol_targeting_v4_ativos.csv")
    print(f"✅ Salvo: vol_targeting_v4_portfolio.csv")


if __name__ == "__main__":
    main()
