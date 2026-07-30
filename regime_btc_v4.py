# -*- coding: utf-8 -*-
# FILTRO DE REGIME BTC — Alavanca C (Fase 2)
# ----------------------------------------------------------------------------
# A Fase 1 mostrou que a estrategia:
#   - BULL:    ganha +55% (mediana) mas perde do B&H (+156%)
#   - LATERAL: ganha +28% e bate o B&H          <-- ponto forte
#   - BEAR:    perde -14% mas protege vs B&H (-54%)  <-- ponto forte
#
# O trailing stop (Alavanca A) piorou tudo — descartado.
#
# Alavanca C: usar o regime MACRO do BTC como sinal de tamanho de posicao.
#   - Anos de BTC BULL:    usar 1.5x capital (aproveita a alta)
#   - Anos de BTC LATERAL: usar 1.0x capital (neutro)
#   - Anos de BTC BEAR:    usar 0.5x capital (metade em cash)
#
# Mecanica: escalar o retorno anual de cada ativo pelo fator de posicao do
# regime BTC naquele ano. Cash = retorno 0%.
#   retorno_escalado = retorno_base * fator + 0 * (1 - fator)
#
# Configs testadas:
#   DEFENSIVO      : fator fixo 1.0 (receita atual, sem filtro)
#   FILTRO_BTC     : fator {BULL: 1.0, LATERAL: 1.0, BEAR: 0.5}
#   AGRESSIVO_BTC  : fator {BULL: 1.5, LATERAL: 1.0, BEAR: 0.5}
#
# Usa os CSVs gerados pela Fase 1 (walkforward_robusta_v4_janelas.csv).
#
# Como rodar:
#   .venv\Scripts\python.exe regime_btc_v4.py

import sys
import numpy as np
import pandas as pd

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

FATOR = {
    "DEFENSIVO":     {"BULL": 1.0, "LATERAL": 1.0, "BEAR": 1.0},
    "FILTRO_BTC":    {"BULL": 1.0, "LATERAL": 1.0, "BEAR": 0.5},
    "AGRESSIVO_BTC": {"BULL": 1.5, "LATERAL": 1.0, "BEAR": 0.5},
}

DESCRICOES = {
    "DEFENSIVO":     "Receita atual sem filtro (fator 1.0 sempre)",
    "FILTRO_BTC":    "Metade do capital em anos de BEAR do BTC (protecao)",
    "AGRESSIVO_BTC": "1.5x em BULL BTC + 0.5x em BEAR BTC (busca retorno)",
}


def main():
    try:
        df = pd.read_csv("walkforward_robusta_v4_janelas.csv")
    except FileNotFoundError:
        print("ERRO: walkforward_robusta_v4_janelas.csv nao encontrado.")
        print("Rode walkforward_robusta_v4.py primeiro.")
        return

    # Regime macro do BTC por ano (classificado pelo retorno do B&H do BTC)
    btc = df[df["Ativo"] == "BTCUSDT"][["Ano", "Regime", "BH_%"]].copy()
    btc = btc.rename(columns={"Regime": "BTC_Regime", "BH_%": "BTC_BH"})
    btc = btc.set_index("Ano")

    # Merge: cada linha de cada ativo ganha o regime BTC do mesmo ano
    df = df.merge(btc, on="Ano", how="left")
    df["BTC_Regime"] = df["BTC_Regime"].fillna("LATERAL")  # anos sem BTC = neutro

    print("=" * 72)
    print("FILTRO DE REGIME BTC — impacto no portfolio de 22 ativos")
    print("=" * 72)
    for k, v in DESCRICOES.items():
        print(f"  {k}: {v}")

    # Regime BTC por ano
    print("\nRegime BTC por ano de calendario (usado como filtro):")
    for ano in sorted(btc.index.unique()):
        r = btc.loc[ano, "BTC_Regime"] if ano in btc.index else "N/A"
        bh = btc.loc[ano, "BTC_BH"] if ano in btc.index else 0
        print(f"  {ano}: {r:<10} (BTC B&H {bh:+.1f}%)")

    print("\n\n" + "=" * 72)
    print("RETORNO ESCALONADO — mediana por regime do ATIVO")
    print("=" * 72)
    print("  (nota: escalonamento e pelo regime BTC, exibicao e pelo regime do ativo)")
    print()

    configs = list(FATOR.keys())
    regimes = ["BULL", "LATERAL", "BEAR", "TODOS"]

    for metric_name, metric_fn, unit in [
        ("Mediana retorno", lambda v: float(np.median(v)), "%"),
        ("% janelas positivas", lambda v: float(np.mean(v > 0) * 100), "%"),
        ("% bate B&H", None, "%"),
        ("Drawdown medio", lambda v: float(np.mean(v)), "%"),
    ]:
        print(f"{metric_name}:")
        header = f"  {'Regime':<12}" + "".join(f" {c:>14}" for c in configs) + "   N"
        print(header)
        print("  " + "-" * (12 + 15 * len(configs) + 4))

        for regime in regimes:
            sub = df if regime == "TODOS" else df[df["Regime"] == regime]
            n_jan = len(sub[sub.get("BTC_Regime", pd.Series(dtype=str)).notna()])
            n_jan = len(sub)  # conta linhas unicas por ativo
            row = f"  {regime:<12}"
            for c in configs:
                fator_map = FATOR[c]
                sub2 = sub.copy()
                sub2["fator"] = sub2["BTC_Regime"].map(fator_map).fillna(1.0)
                sub2["retorno_esc"] = sub2["Retorno_%"] * sub2["fator"]
                vals = sub2["retorno_esc"].values
                if metric_fn is None:  # bate B&H
                    bh_esc = sub2["BH_%"].values
                    val = float(np.mean(vals > bh_esc) * 100)
                elif metric_name == "Drawdown medio":
                    val = float(np.mean(sub2["DD_%"].values))  # DD nao muda com escala simples
                else:
                    val = metric_fn(vals)
                sign = "+" if val >= 0 and metric_name != "Drawdown medio" and metric_name != "% janelas positivas" and metric_name != "% bate B&H" else ""
                row += f" {sign}{val:>12.1f}{unit}"
            row += f"   {n_jan}"
            print(row)
        print()

    # ── Portfolio composto (pesos iguais para simplificar) ────────────────────
    print("=" * 72)
    print("SIMULACAO DE PORTFOLIO IGUAL-PESO (media dos 22 ativos por ano)")
    print("=" * 72)
    print("  (simplificacao: peso igual por ativo; resultado orientativo)")
    print()

    anos = sorted(df["Ano"].unique())
    for c in configs:
        fator_map = FATOR[c]
        df2 = df.copy()
        df2["fator"] = df2["BTC_Regime"].map(fator_map).fillna(1.0)
        df2["retorno_esc"] = df2["Retorno_%"] * df2["fator"]
        retornos_anuais = df2.groupby("Ano")["retorno_esc"].mean().reindex(anos)
        bh_anuais       = df2.groupby("Ano")["BH_%"].mean().reindex(anos)
        btc_reg         = df2.groupby("Ano")["BTC_Regime"].first().reindex(anos)

        print(f"  {c}:")
        print(f"  {'Ano':<6} {'BTC':>8} {'Strat%':>9} {'B&H%':>9}")
        capital = 1000.0
        for ano in anos:
            r = retornos_anuais.get(ano, 0)
            b = bh_anuais.get(ano, 0)
            reg = btc_reg.get(ano, "?")
            capital *= (1 + r / 100)
            print(f"  {ano:<6} {reg:>8} {r:>+9.1f}% {b:>+9.1f}%")
        ret_total = (capital - 1000) / 1000 * 100
        mediana = float(np.median([retornos_anuais.get(a, 0) for a in anos if pd.notna(retornos_anuais.get(a))]))
        print(f"  -> Capital final: ${capital:.0f} | Retorno total: {ret_total:+.1f}%")
        print(f"  -> Mediana anual: {mediana:+.1f}%")
        print()

    # ── Veredicto ─────────────────────────────────────────────────────────────
    print("=" * 72)
    print("VEREDICTO")
    print("=" * 72)
    df2_def  = df.copy()
    df2_filt = df.copy()
    df2_agr  = df.copy()
    for d, c in [(df2_def, "DEFENSIVO"), (df2_filt, "FILTRO_BTC"), (df2_agr, "AGRESSIVO_BTC")]:
        fator_map = FATOR[c]
        d["fator"] = d["BTC_Regime"].map(fator_map).fillna(1.0)
        d["retorno_esc"] = d["Retorno_%"] * d["fator"]

    med_def  = float(np.median(df2_def["retorno_esc"].values))
    med_filt = float(np.median(df2_filt["retorno_esc"].values))
    med_agr  = float(np.median(df2_agr["retorno_esc"].values))

    print(f"  DEFENSIVO     mediana {med_def:+.1f}% por janela-ativo")
    print(f"  FILTRO_BTC    mediana {med_filt:+.1f}% por janela-ativo  "
          f"(delta: {med_filt-med_def:+.1f}pp)")
    print(f"  AGRESSIVO_BTC mediana {med_agr:+.1f}% por janela-ativo  "
          f"(delta: {med_agr-med_def:+.1f}pp)")

    # Drawdown
    print()
    bear_def  = df2_def[df2_def["Regime"] == "BEAR"]["retorno_esc"].values
    bear_filt = df2_filt[df2_filt["Regime"] == "BEAR"]["retorno_esc"].values
    bear_agr  = df2_agr[df2_agr["Regime"] == "BEAR"]["retorno_esc"].values
    print(f"  Em anos BEAR do ATIVO:")
    print(f"    DEFENSIVO     mediana {np.median(bear_def):+.1f}%")
    print(f"    FILTRO_BTC    mediana {np.median(bear_filt):+.1f}%  "
          f"(metade capital = metade da dor)")
    print(f"    AGRESSIVO_BTC mediana {np.median(bear_agr):+.1f}%  "
          f"(idem)")

    bull_def  = df2_def[df2_def["Regime"] == "BULL"]["retorno_esc"].values
    bull_agr  = df2_agr[df2_agr["Regime"] == "BULL"]["retorno_esc"].values
    print()
    print(f"  Em anos BULL do ATIVO:")
    print(f"    DEFENSIVO     mediana {np.median(bull_def):+.1f}%")
    print(f"    AGRESSIVO_BTC mediana {np.median(bull_agr):+.1f}%  "
          f"(1.5x em BULL BTC)")

    print()
    print("  NOTA: O escalonamento e SIMULADO sobre os retornos do walk-forward.")
    print("  Para uma validacao real, re-rodar o backtest com sizes variaveis")
    print("  e necessario (Fase 3 do plano).")

    print("\nFase 2C concluida.")


if __name__ == "__main__":
    main()
