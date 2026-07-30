# -*- coding: utf-8 -*-
"""
BACKTEST COM FILTRO DE REGIME BTC - SIZES DINÂMICOS (v4)
--------------------------------------------------------------
Implementa o re-backtest REAL do filtro de regime BTC (Fase 3 do
PLANO_EVOLUCAO). A diferença do regime_btc_v4.py (Fase 2):

  regime_btc_v4.py   → escalou os RETORNOS anuais do walk-forward (simulado)
  backtest_btc_filter_v4.py → re-roda o MOTOR com sizes variáveis por trade

Lógica por trade:
  1. Identifica o regime BTC no momento da abertura do trade (retorno
     trailing 12m do BTC calculado com dados APENAS antes do trade → sem
     look-ahead).
  2. Aplica o fator de tamanho ao capital alocado naquele trade.
  3. O capital não alocado (se fator < 1) fica em cash durante o trade;
     se fator > 1 simula alavancagem.
  4. Equity candle-a-candle inclui o mark-to-market correto.

Três modos:
  DEFENSIVO  : 1× sempre (baseline sem filtro)
  FILTRADO   : 1× normalmente, 0.5× em BEAR BTC
  AGRESSIVO  : 1.5× em BULL BTC, 1× LATERAL, 0.5× BEAR BTC

Regime BTC:
  BULL   : retorno trailing 12m > +25%
  BEAR   : retorno trailing 12m < -25%
  LATERAL: outros

Saídas:
  backtest_btc_filter_v4_ativos.csv    — metrics por ativo × modo
  backtest_btc_filter_v4_portfolio.csv — portfólio combinado (pesos inv-vol)

Como rodar:
  .venv\\Scripts\\python.exe backtest_btc_filter_v4.py
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

FATORES = {
    "DEFENSIVO": {"BULL": 1.0, "LATERAL": 1.0, "BEAR": 1.0},
    "FILTRADO":  {"BULL": 1.0, "LATERAL": 1.0, "BEAR": 0.5},
    "AGRESSIVO": {"BULL": 1.5, "LATERAL": 1.0, "BEAR": 0.5},
}

BULL_THRESH   = 0.25   # >+25% → BULL
BEAR_THRESH   = -0.25  # <-25% → BEAR
JANELA_REGIME = 365    # dias trailing para classificar regime BTC


# ─────────────────────────────────────────────────────────────────────────────
# Indicadores (mesmo padrão do walkforward_robusta_v4.py)
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
# Regime BTC
# ─────────────────────────────────────────────────────────────────────────────

def classificar_regime_btc(
    t_entry: pd.Timestamp,
    btc_close: pd.Series,
) -> str:
    """Regime BTC baseado em retorno trailing 12m até t_entry (sem look-ahead)."""
    t_12m = t_entry - pd.Timedelta(days=JANELA_REGIME)
    p_atual = btc_close.asof(t_entry)
    p_12m   = btc_close.asof(t_12m)
    if pd.isna(p_atual) or pd.isna(p_12m) or p_12m <= 0:
        return "LATERAL"
    ret = p_atual / p_12m - 1.0
    if ret > BULL_THRESH:
        return "BULL"
    if ret < BEAR_THRESH:
        return "BEAR"
    return "LATERAL"


# ─────────────────────────────────────────────────────────────────────────────
# Equity com sizes dinâmicos
# ─────────────────────────────────────────────────────────────────────────────

def simular_equity_sized(
    eventos: list,
    fechamento: np.ndarray,
    t_abert: np.ndarray,
    btc_close: pd.Series,
    fatores_modo: dict,
    taxa: float,
    slippage: float,
) -> tuple[np.ndarray, list]:
    """Reconstrói curva de equity com fator de tamanho por regime BTC.

    Retorna (equity_curve, trade_log) onde trade_log é lista de dicts com
    detalhes de cada trade fechado (para diagnóstico).

    Matemática do fator f em cada trade:
      - capital_em_trade  = capital * f
      - capital_cash      = capital * (1 - f)   [negativo se f>1 = alavancagem]
      - qty               = capital_em_trade / preco_entry * (1 - taxa)
      - equity no trade   = capital_cash + qty * preco_atual
      - capital pós-saída = capital_cash + qty * preco_saida_eff * (1 - taxa)
    """
    n = len(fechamento)
    equity = np.empty(n)
    capital = float(CAPITAL_INICIAL)
    equity[0] = capital

    # Mapeia índice → evento para lookup O(1)
    ev_entry = {}  # idx → preco_entry (com slippage de entrada já do core)
    ev_exit  = {}  # idx → preco_bruto_saida
    for ev in eventos:
        tipo, idx, preco, _stop = ev
        if tipo == "entrada":
            ev_entry[idx] = preco
        else:
            ev_exit[idx] = preco

    posicionado  = False
    qty          = 0.0
    capital_cash = 0.0
    fator_atual  = 1.0
    trade_log    = []
    capital_pre_entrada = capital

    for i in range(1, n):
        if i in ev_entry and not posicionado:
            preco_entry = ev_entry[i]
            t = pd.Timestamp(t_abert[i])
            regime = classificar_regime_btc(t, btc_close)
            fator_atual  = fatores_modo[regime]
            capital_pre_entrada = capital
            capital_em_trade    = capital * fator_atual
            capital_cash        = capital - capital_em_trade
            qty = (capital_em_trade / preco_entry) * (1.0 - taxa)
            posicionado  = True
            trade_log.append({
                "tipo": "entrada", "idx": i,
                "regime_btc": regime, "fator": fator_atual,
                "preco": preco_entry, "capital_antes": capital,
            })

        elif i in ev_exit and posicionado:
            preco_bruto = ev_exit[i]
            preco_saida_eff = preco_bruto * (1.0 - slippage)
            capital_novo = capital_cash + (qty * preco_saida_eff) * (1.0 - taxa)
            ret_trade = (capital_novo - capital_pre_entrada) / capital_pre_entrada
            trade_log.append({
                "tipo": "saida", "idx": i,
                "preco": preco_saida_eff, "capital_apos": capital_novo,
                "ret_trade_pct": round(ret_trade * 100, 2),
            })
            capital      = capital_novo
            posicionado  = False

        equity[i] = (capital_cash + qty * fechamento[i]) if posicionado else capital

    return equity, trade_log


# ─────────────────────────────────────────────────────────────────────────────
# Métricas
# ─────────────────────────────────────────────────────────────────────────────

def calcular_metricas(
    equity: np.ndarray,
    t_abert: np.ndarray,
    candles_dia: int,
) -> dict:
    n = len(equity)
    capital_final = float(equity[-1])
    ret_total = (capital_final - CAPITAL_INICIAL) / CAPITAL_INICIAL

    running_max = np.maximum.accumulate(equity)
    with np.errstate(invalid="ignore", divide="ignore"):
        dd_series = (running_max - equity) / running_max
    max_dd = float(np.nanmax(dd_series)) if n > 1 else 0.0

    dias = float((t_abert[-1] - t_abert[0]) / np.timedelta64(1, "D"))
    anos = dias / 365.25 if dias > 0 else None
    ret_anual = ((1 + ret_total) ** (1.0 / anos) - 1) if anos else None
    calmar = (ret_anual / max_dd) if (max_dd > 0 and ret_anual is not None) else None

    sharpe = None
    if candles_dia and n >= candles_dia * 10:
        eq_d = equity[::candles_dia]
        rd = np.diff(eq_d) / eq_d[:-1]
        rd = rd[np.isfinite(rd)]
        if len(rd) >= 10 and np.std(rd, ddof=1) > 0:
            sharpe = float((np.mean(rd) / np.std(rd, ddof=1)) * np.sqrt(365.0))

    return {
        "retorno_total_pct":  round(ret_total * 100, 2),
        "retorno_anual_pct":  round(ret_anual * 100, 2) if ret_anual is not None else None,
        "drawdown_pct":       round(max_dd * 100, 2),
        "calmar":             round(calmar, 4) if calmar is not None else None,
        "sharpe":             round(sharpe, 4) if sharpe is not None else None,
        "capital_final":      round(capital_final, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    # 1. Carrega BTC (histórico completo — precisa de 12m anteriores ao início
    #    dos ativos mais novos; usar histórico inteiro evita NaN no regime)
    print("Carregando dados BTC para classificação de regime...")
    df_btc_full = carregar_dados("BTCUSDT", "6h")
    btc_close = pd.Series(
        df_btc_full["fechamento"].values,
        index=pd.to_datetime(df_btc_full["t_abert"].values),
    ).sort_index()
    print(f"  BTC: {btc_close.index[0].date()} → {btc_close.index[-1].date()}")

    # 2. Pesos do portfólio (calculados pela portfolio_v4.py com inv-vol)
    try:
        df_pesos = pd.read_csv(PESOS_CSV)
        pesos_dict = dict(zip(df_pesos["Ativo"], df_pesos["Peso_Portfolio_%"] / 100.0))
        print(f"  Pesos: {PESOS_CSV} ({len(pesos_dict)} ativos)")
    except FileNotFoundError:
        print(f"  [AVISO] {PESOS_CSV} não encontrado — usando pesos iguais.")
        pesos_dict = {a: 1.0 / len(ATIVOS_PORTFOLIO_V4) for a in ATIVOS_PORTFOLIO_V4}

    # 3. Backtest por ativo
    rows_ativos = []
    ret_diarios: dict[str, dict[str, pd.Series]] = {m: {} for m in FATORES}

    print(f"\nBacktest dos {len(ATIVOS_PORTFOLIO_V4)} ativos (período de desenvolvimento):")
    for ativo, interval_str in ATIVOS_PORTFOLIO_V4.items():
        info   = classificar_liquidez(ativo)
        taxa, slippage = info["taxa"], info["slippage"]
        candles_dia    = CANDLES_POR_DIA[interval_str]
        grupo          = grupo_ouro(ativo)
        params         = RECEITA_ROBUSTA[grupo]

        print(f"  {ativo:<22} ({interval_str}, {grupo:8})", end="  ", flush=True)
        try:
            df = carregar_dados(ativo, interval_str)
            periodos = separar_periodos(df["t_abert"])
            df_dev   = df.iloc[:periodos["idx_dev_fim"]].reset_index(drop=True)
            df_fast  = montar_df_fast(df_dev, params)

            compra, venda = calcular_sinais(
                df_fast[f"ma_{params['media_rapida']}"],
                df_fast[f"ma_{params['media_lenta']}"],
                df_fast[f"ma_f_{params['media_filtro']}"],
                df_fast["fechamento"],
            )
            eventos, _ = simular_posicao(
                df_fast["abertura"],
                df_fast["minima"],
                df_fast[f"atr_{params['atr_periodo']}"],
                compra, venda,
                params["atr_multiplicador"],
                slippage,
            )
            n_trades = sum(1 for e in eventos if e[0] == "saida")
            print(f"{n_trades:3d} trades", end="  ")
        except Exception as exc:
            print(f"ERRO: {exc}")
            continue

        fechamento = df_fast["fechamento"]
        t_abert    = df_fast["t_abert"]
        datas      = pd.to_datetime(t_abert)

        for modo, fat in FATORES.items():
            equity, _log = simular_equity_sized(
                eventos, fechamento, t_abert, btc_close, fat, taxa, slippage,
            )
            m = calcular_metricas(equity, t_abert, candles_dia)
            rows_ativos.append({
                "Ativo": ativo, "Modo": modo, "Grupo": grupo,
                "n_trades": n_trades, **m,
            })
            eq_s     = pd.Series(equity, index=datas)
            eq_d     = eq_s.resample("1D").last().dropna()
            rd       = eq_d.pct_change().dropna()
            ret_diarios[modo][ativo] = rd

        print("✓")

    if not rows_ativos:
        print("[ERRO] Nenhum ativo processado.")
        return

    # 4. Portfólio combinado por modo
    rows_port = []
    for modo, rd_map in ret_diarios.items():
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

    # 5. Salvar
    df_ativos = pd.DataFrame(rows_ativos)
    df_port   = pd.DataFrame(rows_port)
    df_ativos.to_csv("backtest_btc_filter_v4_ativos.csv", index=False)
    df_port.to_csv("backtest_btc_filter_v4_portfolio.csv", index=False)

    # 6. Resumo
    print("\n" + "=" * 62)
    print("  PORTFÓLIO — PERÍODO DE DESENVOLVIMENTO (re-backtest real)")
    print("=" * 62)
    print(f"{'Modo':<12} {'Ret%':>8} {'Ret/ano%':>9} {'DD%':>7} {'Calmar':>8} {'Sharpe':>8} {'Capital':>10}")
    print("-" * 62)
    for _, row in df_port.iterrows():
        print(
            f"{row['Modo']:<12} {row['retorno_total_pct']:>8.1f}"
            f" {row['retorno_anual_pct']:>9.1f}"
            f" {row['drawdown_pct']:>7.1f}"
            f" {row['calmar']:>8.3f}"
            f" {row['sharpe']:>8.3f}"
            f" {row['capital_final']:>10.0f}"
        )

    print("\n  MEDIANA POR ATIVO")
    print("-" * 40)
    for modo in FATORES:
        sub = df_ativos[df_ativos["Modo"] == modo]
        cal = sub["calmar"].dropna()
        print(
            f"  {modo:<12} ret={sub['retorno_total_pct'].median():+.1f}%"
            f"  DD={sub['drawdown_pct'].median():.1f}%"
            f"  Calmar={cal.median():.3f}"
        )

    delta_df = df_port.set_index("Modo")[["retorno_total_pct", "drawdown_pct", "calmar"]]
    if "DEFENSIVO" in delta_df.index and "AGRESSIVO" in delta_df.index:
        d = delta_df.loc["AGRESSIVO"] - delta_df.loc["DEFENSIVO"]
        print(f"\n  AGRESSIVO vs DEFENSIVO (portfólio):")
        print(f"    Δ Retorno total: {d['retorno_total_pct']:+.1f}pp")
        print(f"    Δ Drawdown:      {d['drawdown_pct']:+.1f}pp")
        print(f"    Δ Calmar:        {d['calmar']:+.3f}")

    print(f"\n✅ Salvo: backtest_btc_filter_v4_ativos.csv")
    print(f"✅ Salvo: backtest_btc_filter_v4_portfolio.csv")


if __name__ == "__main__":
    main()
