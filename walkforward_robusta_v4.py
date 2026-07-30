# -*- coding: utf-8 -*-
# WALK-FORWARD MULTI-JANELA - RECEITA ROBUSTA FIXA (v4)
# ----------------------------------------------------------------------------
# Diferenca fundamental do walkforward_validacao.py (v2):
#   - O v2 RE-OTIMIZA os parametros em cada janela de treino (walk-forward
#     com otimizacao = mede a qualidade do PROCESSO de otimizacao).
#   - Este script usa a RECEITA_ROBUSTA FIXA por grupo (veterana/nova) — sem
#     otimizacao, sem data snooping, sem look-ahead nos parametros.
#   - Objetivo: medir a distribuicao de resultados da receita em diferentes
#     regimes de mercado (alta, baixa, lateral) ao longo do periodo de
#     desenvolvimento dos 22 ativos.
#
# Saidas:
#   walkforward_robusta_v4_janelas.csv  — uma linha por (ativo, ano)
#   walkforward_robusta_v4_resumo.txt   — report legivel
#
# Como rodar:
#   .venv\Scripts\python.exe walkforward_robusta_v4.py

import sys
import warnings
import numpy as np
import pandas as pd
from scipy.stats import skew, kurtosis, norm, wilcoxon

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

JANELA_ANOS = 1
MIN_CANDLES_JANELA = 40  # janelas com menos candles sao ignoradas
BOOTSTRAP_N = 2000       # permutacoes para CI do Sharpe


# ─── Indicadores ────────────────────────────────────────────────────────────

def montar_df_fast(df: pd.DataFrame, params: dict) -> dict:
    """Monta o dict de arrays (indicadores pre-computados) para `params`."""
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


# ─── Backtest de uma janela ──────────────────────────────────────────────────

def executar_janela(
    df_fast: dict,
    params: dict,
    inicio: int,
    fim: int,
    taxa: float,
    slippage: float,
    candles_dia: int,
) -> dict | None:
    """Backtest da receita fixa no intervalo [inicio:fim). Retorna None se a
    janela for pequena demais ou nao houver dados validos."""
    mr = params["media_rapida"]
    ml = params["media_lenta"]
    mf = params["media_filtro"]
    ap = params["atr_periodo"]

    m_rapida   = df_fast[f"ma_{mr}"][inicio:fim]
    m_lenta    = df_fast[f"ma_{ml}"][inicio:fim]
    m_filtro   = df_fast[f"ma_f_{mf}"][inicio:fim]
    abertura   = df_fast["abertura"][inicio:fim]
    minima     = df_fast["minima"][inicio:fim]
    fechamento = df_fast["fechamento"][inicio:fim]
    atr        = df_fast[f"atr_{ap}"][inicio:fim]
    t_abert    = df_fast["t_abert"][inicio:fim]
    multi      = params["atr_multiplicador"]

    n = len(fechamento)
    if n < MIN_CANDLES_JANELA:
        return None

    sinais_compra, sinais_venda = calcular_sinais(
        m_rapida, m_lenta, m_filtro, fechamento
    )
    eventos, _ = simular_posicao(
        abertura, minima, atr, sinais_compra, sinais_venda, multi, slippage
    )

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
        equity[i] = capital if not posicionado else (qtd * fechamento[i])

    retorno = (equity[-1] - CAPITAL_INICIAL) / CAPITAL_INICIAL

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

    dias = float((t_abert[-1] - t_abert[0]) / np.timedelta64(1, "D"))

    # Sharpe anual da janela
    sharpe = None
    if candles_dia and n >= candles_dia * 5:
        eq_dia = equity[::candles_dia]
        ret_dia = np.diff(eq_dia) / eq_dia[:-1]
        ret_dia = ret_dia[np.isfinite(ret_dia)]
        if len(ret_dia) >= 5 and np.std(ret_dia, ddof=1) > 0:
            sharpe = float(np.mean(ret_dia) / np.std(ret_dia, ddof=1) * np.sqrt(365))

    return {
        "retorno_pct":  round(retorno * 100, 2),
        "bh_pct":       round(bh * 100, 2),
        "drawdown_pct": round(max_dd * 100, 2),
        "num_trades":   num_trades,
        "regime":       regime,
        "dias":         round(dias, 0),
        "sharpe":       sharpe,
        "equity":       equity,
    }


# ─── Janelas anuais ──────────────────────────────────────────────────────────

def gerar_janelas_anuais(df: pd.DataFrame, idx_dev_fim: int) -> list:
    """Janelas de JANELA_ANOS ano(s), cobrindo todo o periodo de desenvolvimento."""
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
                "periodo": f"{datas.iloc[idx_ini].date()} a {datas.iloc[idx_fim-1].date()}",
            })
        ano += 1
    return janelas


# ─── Significancia (bootstrap do Sharpe) ────────────────────────────────────

def bootstrap_sharpe(equity: np.ndarray, candles_dia: int, n_boot: int = 2000) -> dict:
    """Bootstrap CI do Sharpe anualizado. Retorna sharpe_obs, p5, p95 e p_value
    (fracao de bootstraps com Sharpe <= 0 — quanto menor, mais significativo)."""
    eq_dia = equity[::candles_dia]
    ret = np.diff(eq_dia) / eq_dia[:-1]
    ret = ret[np.isfinite(ret)]
    T = len(ret)
    if T < 10 or np.std(ret, ddof=1) == 0:
        return {"sharpe_obs": None, "p5": None, "p95": None, "p_value": None, "T": T}
    sharpe_obs = float(np.mean(ret) / np.std(ret, ddof=1) * np.sqrt(365))
    rng = np.random.default_rng(42)
    boot_sharpes = []
    for _ in range(n_boot):
        idx = rng.integers(0, T, size=T)
        r = ret[idx]
        s = np.std(r, ddof=1)
        if s > 0:
            boot_sharpes.append(float(np.mean(r) / s * np.sqrt(365)))
    if not boot_sharpes:
        return {"sharpe_obs": sharpe_obs, "p5": None, "p95": None, "p_value": None, "T": T}
    p5  = float(np.percentile(boot_sharpes, 5))
    p95 = float(np.percentile(boot_sharpes, 95))
    p_value = float(np.mean(np.array(boot_sharpes) <= 0))
    return {"sharpe_obs": sharpe_obs, "p5": p5, "p95": p95, "p_value": p_value, "T": T}


# ─── Correlacao e bets efetivos ──────────────────────────────────────────────

def efetivo_n_bets(corr_matrix: np.ndarray) -> float:
    """Numero efetivo de apostas independentes via entropia de autovalores.
    Formula: exp(H) onde H = entropia de Shannon dos autovalores normalizados."""
    eigvals = np.linalg.eigvalsh(corr_matrix)
    eigvals = np.maximum(eigvals, 0)  # evita negativos por erro numerico
    total = eigvals.sum()
    if total <= 0:
        return 1.0
    p = eigvals / total
    p = p[p > 0]
    H = -np.sum(p * np.log(p))
    return float(np.exp(H))


# ─── Relatorio ──────────────────────────────────────────────────────────────

def linha_tabela(colunas: list, larguras: list) -> str:
    partes = []
    for val, larg in zip(colunas, larguras):
        s = str(val)
        partes.append(s[:larg].ljust(larg))
    return "  ".join(partes)


def main():
    linhas_janelas = []
    linhas_resumo = []
    retornos_anuais_por_ativo = {}  # para correlacao

    print("=" * 72)
    print("WALK-FORWARD RECEITA ROBUSTA FIXA — 22 ativos, janelas anuais")
    print("=" * 72)

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
            print(f"  [CURTO] {ativo}: {idx_dev_fim} candles de dev")
            continue

        df_dev = df.iloc[:idx_dev_fim].reset_index(drop=True)
        df_fast = montar_df_fast(df_dev, params)

        janelas = gerar_janelas_anuais(df, idx_dev_fim)
        if not janelas:
            print(f"  [SEM JANELAS] {ativo}")
            continue

        retornos_ativo = []
        print(f"\n{ativo} ({interval_str}) [{grupo}]  "
              f"{periodos['dev_inicio'].date()} a {periodos['dev_fim'].date()}")
        print(f"  {'Ano':<6} {'Regime':<8} {'Strat%':>7} {'B&H%':>7} "
              f"{'DD%':>6} {'Trades':>7}")

        for jan in janelas:
            res = executar_janela(
                df_fast, params,
                jan["idx_ini"], jan["idx_fim"],
                info_liq["taxa"], info_liq["slippage"], candles_dia,
            )
            if res is None:
                continue
            print(f"  {jan['ano']:<6} {res['regime']:<8} {res['retorno_pct']:>7.1f} "
                  f"{res['bh_pct']:>7.1f} {res['drawdown_pct']:>6.1f} {res['num_trades']:>7}")

            linha = {
                "Ativo": ativo,
                "Grupo": grupo,
                "Interval": interval_str,
                "Ano": jan["ano"],
                "Periodo": jan["periodo"],
                "Regime": res["regime"],
                "Retorno_%": res["retorno_pct"],
                "BH_%": res["bh_pct"],
                "DD_%": res["drawdown_pct"],
                "Trades": res["num_trades"],
                "Sharpe_janela": round(res["sharpe"], 3) if res["sharpe"] else None,
            }
            linhas_janelas.append(linha)
            retornos_ativo.append(res["retorno_pct"])

        retornos_anuais_por_ativo[ativo] = retornos_ativo

        if not retornos_ativo:
            continue

        # Bootstrap no periodo completo de dev
        res_full = executar_janela(
            df_fast, params, 0, idx_dev_fim,
            info_liq["taxa"], info_liq["slippage"], candles_dia,
        )
        bstrap = {}
        if res_full:
            bstrap = bootstrap_sharpe(res_full["equity"], candles_dia, BOOTSTRAP_N)

        ret_arr = np.array(retornos_ativo)
        n_pos = int(np.sum(ret_arr > 0))
        n_jan = len(ret_arr)

        # Wilcoxon: retornos anuais significativamente > 0?
        p_wilcoxon = None
        if n_jan >= 5:
            try:
                _, p_wilcoxon = wilcoxon(ret_arr, alternative="greater")
            except Exception:
                pass

        resumo_ativo = {
            "Ativo": ativo,
            "Grupo": grupo,
            "N_Janelas": n_jan,
            "Mediana_%": round(float(np.median(ret_arr)), 2),
            "Pior_%": round(float(np.min(ret_arr)), 2),
            "Melhor_%": round(float(np.max(ret_arr)), 2),
            "Pct_Positivas": round(n_pos / n_jan * 100, 1),
            "Sharpe_boot": round(bstrap.get("sharpe_obs") or 0, 3),
            "Sharpe_P5": round(bstrap.get("p5") or 0, 3) if bstrap.get("p5") is not None else None,
            "P_value_boot": round(bstrap.get("p_value") or 1, 3) if bstrap.get("p_value") is not None else None,
            "P_wilcoxon": round(p_wilcoxon, 3) if p_wilcoxon is not None else None,
        }
        linhas_resumo.append(resumo_ativo)

    # ─── Salvar CSVs ────────────────────────────────────────────────────────
    df_jan = pd.DataFrame(linhas_janelas)
    df_jan.to_csv("walkforward_robusta_v4_janelas.csv", index=False)

    df_res = pd.DataFrame(linhas_resumo)
    df_res.to_csv("walkforward_robusta_v4_resumo.csv", index=False)

    # ─── Relatorio agregado ─────────────────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("RESUMO AGREGADO — RECEITA ROBUSTA POR GRUPO")
    print("=" * 72)

    for grupo in ["veterana", "nova"]:
        sub = df_jan[df_jan["Grupo"] == grupo]
        if sub.empty:
            continue
        ret = sub["Retorno_%"].values
        bh  = sub["BH_%"].values
        print(f"\nGrupo '{grupo}' ({len(sub)} janelas-ativo):")
        print(f"  Estrategia — mediana: {np.median(ret):+.1f}%  "
              f"| pior: {np.min(ret):+.1f}%  | melhor: {np.max(ret):+.1f}%")
        print(f"  Janelas positivas: {int(np.sum(ret>0))}/{len(ret)} "
              f"({np.mean(ret>0)*100:.1f}%)")
        print(f"  B&H — mediana: {np.median(bh):+.1f}%  "
              f"| pior: {np.min(bh):+.1f}%  | melhor: {np.max(bh):+.1f}%")
        # Por regime
        for regime in ["BULL", "LATERAL", "BEAR"]:
            r = sub[sub["Regime"] == regime]
            if r.empty:
                continue
            rv = r["Retorno_%"].values
            bv = r["BH_%"].values
            print(f"  {regime:<8} ({len(rv):2d} janelas): strat {np.median(rv):+.1f}% "
                  f"(mediana)  vs  B&H {np.median(bv):+.1f}%  |  "
                  f"{int(np.sum(rv>0))}/{len(rv)} positivas")

    print("\n\n" + "=" * 72)
    print("DISTRIBUICAO GLOBAL (todos os 22 ativos × todas as janelas)")
    print("=" * 72)
    ret_all = df_jan["Retorno_%"].values
    bh_all  = df_jan["BH_%"].values
    print(f"Total de janelas-ativo: {len(ret_all)}")
    print(f"Estrategia: mediana {np.median(ret_all):+.1f}% | "
          f"p25 {np.percentile(ret_all,25):+.1f}% | p75 {np.percentile(ret_all,75):+.1f}% | "
          f"pior {np.min(ret_all):+.1f}% | melhor {np.max(ret_all):+.1f}%")
    print(f"B&H:        mediana {np.median(bh_all):+.1f}% | "
          f"p25 {np.percentile(bh_all,25):+.1f}% | p75 {np.percentile(bh_all,75):+.1f}% | "
          f"pior {np.min(bh_all):+.1f}% | melhor {np.max(bh_all):+.1f}%")
    pct_pos = np.mean(ret_all > 0) * 100
    pct_bat_bh = np.mean(ret_all > bh_all) * 100
    print(f"% janelas positivas: {pct_pos:.1f}%")
    print(f"% janelas que bateram B&H: {pct_bat_bh:.1f}%")

    # Regime breakdown global
    print("\nPor regime (global):")
    for regime in ["BULL", "LATERAL", "BEAR"]:
        r = df_jan[df_jan["Regime"] == regime]
        if r.empty:
            continue
        rv = r["Retorno_%"].values
        bv = r["BH_%"].values
        print(f"  {regime:<8} ({len(rv):3d} janelas): "
              f"strat mediana {np.median(rv):+.1f}%  vs  B&H mediana {np.median(bv):+.1f}%  |  "
              f"{int(np.sum(rv>0))}/{len(rv)} positivas  |  "
              f"bate B&H em {int(np.sum(rv>bv))}/{len(rv)}")

    # ─── Correlacao e N efetivo de apostas ──────────────────────────────────
    print("\n\n" + "=" * 72)
    print("CORRELACAO E APOSTAS EFETIVAS")
    print("=" * 72)

    # Alinhar series de retorno anual por ativo
    anos_set = sorted({jan["Ano"] for jan in linhas_janelas})
    matriz_ret = {}
    for ativo, rets in retornos_anuais_por_ativo.items():
        # Associar cada retorno ao seu ano
        jans_ativo = [j for j in linhas_janelas if j["Ativo"] == ativo]
        ret_por_ano = {j["Ano"]: j["Retorno_%"] for j in jans_ativo}
        if len(ret_por_ano) >= 3:
            matriz_ret[ativo] = ret_por_ano

    if len(matriz_ret) >= 4:
        df_corr = pd.DataFrame(matriz_ret).T
        df_corr = df_corr[sorted(df_corr.columns)]
        df_corr_completo = df_corr.dropna(axis=1, how="any")
        if df_corr_completo.shape[1] >= 3:
            corr_mat = df_corr_completo.T.corr()
            n_eff = efetivo_n_bets(corr_mat.values)
            print(f"Ativos com series completas: {len(corr_mat)}")
            print(f"Anos de dados comuns: {df_corr_completo.shape[1]}")
            print(f"N efetivo de apostas independentes: {n_eff:.1f} "
                  f"(de {len(corr_mat)} ativos)")
            pct_div = n_eff / len(corr_mat) * 100
            print(f"Diversificacao efetiva: {pct_div:.0f}% "
                  f"({'boa' if pct_div > 50 else 'moderada' if pct_div > 30 else 'baixa'})")

            # Top 5 correlacoes mais altas
            corr_upper = corr_mat.where(np.triu(np.ones_like(corr_mat, dtype=bool), k=1))
            top_corr = (
                corr_upper.stack()
                .reset_index()
                .rename(columns={"level_0": "A1", "level_1": "A2", 0: "Corr"})
                .sort_values("Corr", ascending=False)
                .head(5)
            )
            print("\nTop 5 pares mais correlacionados (retornos anuais da estrategia):")
            for _, row in top_corr.iterrows():
                print(f"  {row['A1']} x {row['A2']}: {row['Corr']:+.2f}")
            df_corr_completo.to_csv("walkforward_robusta_v4_correlacao_anual.csv")
        else:
            print("  Dados insuficientes para correlacao completa.")

    # ─── Tabela de resumo por ativo ─────────────────────────────────────────
    print("\n\n" + "=" * 72)
    print("SIGNIFICANCIA POR ATIVO (bootstrap Sharpe no periodo dev completo)")
    print("=" * 72)
    print(f"  {'Ativo':<18} {'Grupo':<10} {'Median%':>7} {'Pior%':>7} "
          f"{'%Pos':>6} {'Sharpe':>7} {'P5':>7} {'p-val':>6}")
    print("  " + "-" * 70)
    for row in linhas_resumo:
        p5_str = f"{row['Sharpe_P5']:+.2f}" if row["Sharpe_P5"] is not None else "   N/A"
        pv_str = f"{row['P_value_boot']:.2f}" if row["P_value_boot"] is not None else "  N/A"
        print(f"  {row['Ativo']:<18} {row['Grupo']:<10} {row['Mediana_%']:>+7.1f} "
              f"{row['Pior_%']:>+7.1f} {row['Pct_Positivas']:>5.0f}% "
              f"{row['Sharpe_boot']:>7.2f} {p5_str:>7} {pv_str:>6}")

    print(f"\n\nSalvo: walkforward_robusta_v4_janelas.csv  ({len(df_jan)} linhas)")
    print(f"Salvo: walkforward_robusta_v4_resumo.csv   ({len(df_res)} ativos)")
    print("Salvo: walkforward_robusta_v4_correlacao_anual.csv")
    print("\nFase 1 concluida.")


if __name__ == "__main__":
    main()
