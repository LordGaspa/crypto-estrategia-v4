# -*- coding: utf-8 -*-
"""
FASE 3 — SLIPPAGE REALISTA (Fase 3 do PLANO_EVOLUCAO)
--------------------------------------------------------------
O modelo atual usa slippage FIXO em todas as execuções (entrada e saída).
Problema: na saída por STOP o mercado já está se movendo contra você —
gaps, spread alargado, fila de ordens na mesma direção. O slippage real
em saídas de stop é significativamente maior que em saídas por cruzamento.

Modelo proposto:
  Entrada (sempre ordem normal):      slip_entrada = slip_base  (inalterado)
  Saída por CRUZAMENTO (mercado calmo): slip_saida = slip_base  (inalterado)
  Saída por STOP (mercado em queda):  slip_saida = slip_base + gap_frac × ATR/preço

  gap_frac representa a fração do ATR que o preço costuma "escorregar" abaixo
  do nível do stop antes de você conseguir executar:
    - Líquidos  (BTC, ETH...): gap_frac = 0.05  → slip extra = 0.05 × ATR%
    - Ilíquidos (memecoins...): gap_frac = 0.25  → slip extra = 0.25 × ATR%

Exemplo com BTC (ATR/preço ≈ 1%):
  slip_stop_real = 0.05% + 0.05 × 1% = 0.10%  (vs 0.05% atual)

Exemplo com PEPE (ATR/preço ≈ 5%):
  slip_stop_real = 0.175% + 0.25 × 5% = 1.425%  (vs 0.175% atual — GRANDE)

O script mostra o DELTA entre o modelo atual (otimista) e o realista,
por ativo e para o portfólio ponderado. Isso responde:
  "A proteção em BEAR ainda vale a pena mesmo com slippage realista?"

Saídas:
  slippage_realista_v4_ativos.csv
  slippage_realista_v4_portfolio.csv

Como rodar:
  .venv\\Scripts\\python.exe slippage_realista_v4.py
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
    ATIVOS_LIQUIDOS,
    grupo_ouro,
)
from estrategia_core import calcular_sinais, simular_posicao

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

PESOS_CSV = "portfolio_v4_pesos.csv"

# Fração do ATR que escorrega abaixo do stop na execução
GAP_FRAC_LIQUIDO      = 0.05   # 5% do ATR (ex: BTC ATR=1% → 0.05% extra)
GAP_FRAC_MENOS_LIQUIDO = 0.25  # 25% do ATR (ex: PEPE ATR=5% → 1.25% extra)


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
# Equity com slippage diferenciado por tipo de saída
# ─────────────────────────────────────────────────────────────────────────────

def simular_equity_slip_realista(
    eventos: list,
    fechamento: np.ndarray,
    taxa: float,
    slip_base: float,
    gap_frac: float,    # fração do ATR que escorrega além do stop na execução
) -> np.ndarray:
    """Equity com slippage realista: saídas de stop recebem extra por volatilidade.

    Distingue saída por stop (preco_bruto = stop ou < stop) de saída por
    cruzamento (preco_bruto = abertura[i]). Só o stop recebe slip extra.

    stop_dist = preco_entrada - stop  (estimativa de 1 ATR × mult)
    slip_extra = gap_frac × (stop_dist / preco_entrada)   (como fração do preço)
    slip_stop_total = slip_base + slip_extra
    """
    n = len(fechamento)
    equity = np.empty(n)
    capital = float(CAPITAL_INICIAL)
    equity[0] = capital

    # Mapear eventos
    ev_entry = {}   # idx → (preco_entry, stop)
    ev_exit  = {}   # idx → (preco_bruto, eh_stop)
    pos_atual = False
    stop_ativo = 0.0
    preco_ent = 0.0

    for ev in eventos:
        tipo, idx, preco, stop = ev
        if tipo == "entrada":
            ev_entry[idx] = (preco, stop)
        else:
            ev_exit[idx] = preco  # preco_bruto já está no evento

    posicionado   = False
    qty           = 0.0
    stop_do_trade = 0.0
    entry_price   = 0.0
    stop_dist     = 0.0

    for i in range(1, n):
        if i in ev_entry and not posicionado:
            entry_price, stop_do_trade = ev_entry[i]
            stop_dist   = entry_price - stop_do_trade
            qty         = (capital / entry_price) * (1 - taxa)
            posicionado = True

        elif i in ev_exit and posicionado:
            preco_bruto = ev_exit[i]

            # Determina se foi saída por stop: preco_bruto == stop ou <= stop_do_trade
            eh_stop = preco_bruto <= stop_do_trade + 1e-10

            if eh_stop and entry_price > 0 and stop_dist > 0:
                atr_rel    = stop_dist / entry_price     # aprox ATR×mult / price
                slip_extra = gap_frac * atr_rel
                slip_saida = slip_base + slip_extra
            else:
                slip_saida = slip_base

            preco_eff = preco_bruto * (1.0 - slip_saida)
            capital   = (qty * preco_eff) * (1.0 - taxa)
            posicionado = False

        equity[i] = qty * fechamento[i] if posicionado else capital

    return equity


def simular_equity_baseline(
    eventos: list,
    fechamento: np.ndarray,
    taxa: float,
    slippage: float,
) -> np.ndarray:
    """Modelo atual (slippage fixo em todas as saídas)."""
    n = len(fechamento)
    equity = np.empty(n)
    capital = float(CAPITAL_INICIAL)
    equity[0] = capital
    ev_entry = {ev[1]: ev[2] for ev in eventos if ev[0] == "entrada"}
    ev_exit  = {ev[1]: ev[2] for ev in eventos if ev[0] == "saida"}
    posicionado = False
    qty = 0.0

    for i in range(1, n):
        if i in ev_entry and not posicionado:
            qty = (capital / ev_entry[i]) * (1 - taxa)
            posicionado = True
        elif i in ev_exit and posicionado:
            preco_eff = ev_exit[i] * (1 - slippage)
            capital   = (qty * preco_eff) * (1 - taxa)
            posicionado = False
        equity[i] = qty * fechamento[i] if posicionado else capital

    return equity


# ─────────────────────────────────────────────────────────────────────────────
# Métricas
# ─────────────────────────────────────────────────────────────────────────────

def metricas(equity: np.ndarray, t_abert: np.ndarray, candles_dia: int) -> dict:
    n = len(equity)
    cf  = float(equity[-1])
    ret = (cf - CAPITAL_INICIAL) / CAPITAL_INICIAL
    rm  = np.maximum.accumulate(equity)
    with np.errstate(invalid="ignore", divide="ignore"):
        dd_s = (rm - equity) / rm
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
        "retorno_total_pct": round(ret * 100, 2),
        "retorno_anual_pct": round(ret_a * 100, 2) if ret_a is not None else None,
        "drawdown_pct":      round(max_dd * 100, 2),
        "calmar":            round(calmar, 4) if calmar is not None else None,
        "sharpe":            round(sharpe, 4) if sharpe is not None else None,
        "capital_final":     round(cf, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    try:
        df_pesos = pd.read_csv(PESOS_CSV)
        pesos_d  = dict(zip(df_pesos["Ativo"], df_pesos["Peso_Portfolio_%"] / 100))
    except FileNotFoundError:
        pesos_d = {a: 1.0 / len(ATIVOS_PORTFOLIO_V4) for a in ATIVOS_PORTFOLIO_V4}

    rows_ativos = []
    ret_diarios: dict[str, dict] = {"baseline": {}, "realista": {}}

    print("Slippage realista vs modelo atual:\n")
    for ativo, interval_str in ATIVOS_PORTFOLIO_V4.items():
        info   = classificar_liquidez(ativo)
        taxa, slip_base = info["taxa"], info["slippage"]
        candles_dia     = CANDLES_POR_DIA[interval_str]
        grupo           = grupo_ouro(ativo)
        params          = RECEITA_ROBUSTA[grupo]
        liquido         = ativo in ATIVOS_LIQUIDOS
        gap_frac        = GAP_FRAC_LIQUIDO if liquido else GAP_FRAC_MENOS_LIQUIDO

        print(f"  {ativo:<22} ({('liq' if liquido else 'iliq'):4}, gap={gap_frac:.0%})", end="  ", flush=True)
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
                compra, venda, params["atr_multiplicador"], slip_base,
            )
        except Exception as exc:
            print(f"ERRO: {exc}")
            continue

        n_stop = sum(
            1 for ev in eventos
            if ev[0] == "saida" and ev[2] <= ev[3] + 1e-10
        )
        n_cruzamento = sum(1 for ev in eventos if ev[0] == "saida") - n_stop
        t_abert    = df_fast["t_abert"]
        fechamento = df_fast["fechamento"]
        datas      = pd.to_datetime(t_abert)

        for modelo in ("baseline", "realista"):
            if modelo == "baseline":
                eq = simular_equity_baseline(eventos, fechamento, taxa, slip_base)
            else:
                eq = simular_equity_slip_realista(eventos, fechamento, taxa, slip_base, gap_frac)
            m = metricas(eq, t_abert, candles_dia)
            rows_ativos.append({
                "Ativo": ativo, "Modelo": modelo, "Grupo": grupo,
                "liquido": liquido,
                "n_stop": n_stop, "n_cruzamento": n_cruzamento,
                **m,
            })
            s = pd.Series(eq, index=datas).resample("1D").last().dropna().pct_change().dropna()
            ret_diarios[modelo][ativo] = s

        # Print delta
        sub = {m: next(r for r in rows_ativos if r["Ativo"] == ativo and r["Modelo"] == m)
               for m in ("baseline", "realista")}
        dr = sub["realista"]["retorno_total_pct"] - sub["baseline"]["retorno_total_pct"]
        print(f"stop={n_stop}  Δret={dr:+.1f}pp ✓")

    df_ativos = pd.DataFrame(rows_ativos)

    # Portfólio ponderado
    rows_port = []
    for modelo in ("baseline", "realista"):
        rd_map = ret_diarios[modelo]
        ativos_ok = list(rd_map.keys())
        if len(ativos_ok) < 2:
            continue
        dc = None
        for s in rd_map.values():
            dc = s.index if dc is None else dc.intersection(s.index)
        dc = dc.sort_values()
        w = {a: pesos_d.get(a, 0.0) for a in ativos_ok}
        sw = sum(w.values())
        if sw > 0:
            w = {a: v / sw for a, v in w.items()}
        df_r  = pd.DataFrame({a: rd_map[a].reindex(dc) for a in ativos_ok}).fillna(0)
        r_pt  = sum(df_r[a] * w[a] for a in ativos_ok)
        eq_pt = (1 + r_pt).cumprod() * CAPITAL_INICIAL
        t_arr = dc.values.astype("datetime64[ns]")
        m     = metricas(eq_pt.values, t_arr, candles_dia=1)
        rows_port.append({"Modelo": modelo, **m})

    df_port = pd.DataFrame(rows_port)
    df_ativos.to_csv("slippage_realista_v4_ativos.csv", index=False)
    df_port.to_csv("slippage_realista_v4_portfolio.csv", index=False)

    # Resumo
    print("\n" + "=" * 62)
    print("  MÉDIAS PONDERADAS PELOS PESOS DO PORTFÓLIO (histórico completo)")
    print("=" * 62)
    print(f"{'Modelo':<12} {'Ret/ano%':>9} {'DD%':>7} {'Calmar':>8} {'Sharpe':>8}")
    print("-" * 48)
    for modelo in ("baseline", "realista"):
        sub = df_ativos[df_ativos["Modelo"] == modelo].copy()
        sub["peso"] = sub["Ativo"].map(pesos_d).fillna(0)
        sw = sub["peso"].sum()
        sub["w"] = sub["peso"] / sw if sw > 0 else 0
        wret = (sub["retorno_anual_pct"] * sub["w"]).sum()
        wdd  = (sub["drawdown_pct"]      * sub["w"]).sum()
        wcal = (sub["calmar"].fillna(0)  * sub["w"]).sum()
        wsha = (sub["sharpe"].fillna(0)  * sub["w"]).sum()
        print(f"  {modelo:<10} {wret:>9.1f} {wdd:>7.1f} {wcal:>8.3f} {wsha:>8.3f}")

    # Delta por grupo
    print("\n  DELTA (realista − baseline) POR GRUPO")
    print("-" * 48)
    for liquido, nome in [(True, "liquidos"), (False, "iliquidos")]:
        for modelo in ("baseline", "realista"):
            sub = df_ativos[(df_ativos["Modelo"] == modelo) & (df_ativos["liquido"] == liquido)]
            cal = sub["calmar"].dropna()
            print(
                f"  {nome:<10} {modelo:<10}  "
                f"ret={sub['retorno_total_pct'].median():+.0f}%  "
                f"DD={sub['drawdown_pct'].median():.0f}%  "
                f"Calmar={cal.median():.3f}"
            )
        # Delta line
        base = df_ativos[(df_ativos["Modelo"] == "baseline") & (df_ativos["liquido"] == liquido)]["retorno_total_pct"]
        real = df_ativos[(df_ativos["Modelo"] == "realista") & (df_ativos["liquido"] == liquido)]["retorno_total_pct"]
        dr = (real.values - base.values).mean()
        print(f"                         → Δ médio retorno: {dr:+.1f}pp\n")

    print(f"✅ Salvo: slippage_realista_v4_ativos.csv")
    print(f"✅ Salvo: slippage_realista_v4_portfolio.csv")


if __name__ == "__main__":
    main()
