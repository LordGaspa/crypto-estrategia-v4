# -*- coding: utf-8 -*-
# RIQUEZA TERMINAL -- PARTES B (formula do cambio), C (Monte Carlo) e D (cenarios)
# ----------------------------------------------------------------------------
# PARTE B: formaliza o cambio entre perda em bear e ganho em bull. Riqueza
#   compoe geometricamente: ln(1+g) = SUM p_regime * ln(1+r_regime). Disso sai
#   a curva de indiferenca -- quanto de retorno extra em bull compensa
#   exatamente X pontos a mais de perda em bear, dada a FREQUENCIA dos regimes.
#
# PARTE C: Monte Carlo por BLOCK BOOTSTRAP dos retornos diarios reais do
#   PORTFOLIO. Reamostra BLOCOS CONTIGUOS (nao dias isolados) pra preservar
#   autocorrelacao e clusters de regime sem precisar modelar regime
#   explicitamente. Todas as variantes usam os MESMOS blocos (comparacao
#   pareada -- mesmo "futuro" pra todas).
#
#   TENTATIVA ANTERIOR DESCARTADA: modelar regime -> retorno anual com
#   persistencia estimada de janelas rolantes diarias. A persistencia saiu
#   96.8% (dias consecutivos quase sempre tem o mesmo rotulo de janela anual),
#   o que na simulacao virava 10 anos seguidos do mesmo regime e produzia
#   numeros absurdos. Block bootstrap sobre retornos diarios nao tem esse
#   problema.
#
# PARTE D: ranking sob 3 niveis de "corte" (haircut) dos retornos historicos,
#   pra testar se a ordem entre variantes se mantem quando o futuro e menos
#   generoso que 2020-2025.
#
# So periodo de DESENVOLVIMENTO. Holdout LACRADO.
#
# Como rodar:
#   .venv\Scripts\python.exe riqueza_terminal_v6_projecao.py

import sys
import warnings
import numpy as np
import pandas as pd

from config_v4 import ATIVOS_PORTFOLIO_V4, ATIVOS_LIQUIDOS, carregar_dados
from riqueza_terminal_v6 import (
    retornos_diarios_por_variante, pesos_inverse_vol, combinar_portfolio, CAPITAL_REPORTE,
)

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

ANOS_PROJECAO = 10
N_SIMULACOES = 5_000
SEED = 42
BLOCO_DIAS = 120           # ~1 trimestre: preserva cluster de regime
DIAS_POR_ANO = 365
VARIANTES_FOCO = ["Base", "BTC_Filtrado", "ScaleOut", "FiltroVolume", "BuyHold"]


# ═════════════════════════════════════════════════════════════════════════════
# PARTE B -- formula do cambio
# ═════════════════════════════════════════════════════════════════════════════

def bull_que_compensa(delta_bear, r_bull_ref, r_bear_ref, p_bull, p_bear):
    r_bear_novo = r_bear_ref + delta_bear
    if 1 + r_bear_novo <= 0:
        return None
    lhs = (p_bull * np.log(1 + r_bull_ref)
           + p_bear * np.log(1 + r_bear_ref)
           - p_bear * np.log(1 + r_bear_novo))
    return float(np.exp(lhs / p_bull) - 1 - r_bull_ref)


def parte_b(freq_hist, r_bull_ref, r_lat_ref, r_bear_ref):
    print("=" * 100)
    print("PARTE B -- A FORMULA DO CAMBIO (perda em bear vs ganho em bull)")
    print("=" * 100)
    print("""
Riqueza compoe GEOMETRICAMENTE, nao aritmeticamente:

    ln(1 + g_anual) = p_bull*ln(1+r_bull) + p_lat*ln(1+r_lat) + p_bear*ln(1+r_bear)
    Capital_final   = Capital_inicial * (1 + g_anual)^anos

Duas consequencias que a mediana por regime esconde:
  1. ASSIMETRIA: perder 30% exige +42.9% pra voltar ao zero; perder 50% exige +100%.
  2. A FREQUENCIA (p) decide o cambio -- nao a magnitude isolada.
""")
    print("Referencia (retorno anual mediano do PORTFOLIO por regime, medido):")
    print(f"  bull {r_bull_ref:+.1%} · lateral {r_lat_ref:+.1%} · bear {r_bear_ref:+.1%}")
    print(f"Frequencia medida no historico: bull {freq_hist['BULL']:.0%} · "
          f"lateral {freq_hist['LATERAL']:.0%} · bear {freq_hist['BEAR']:.0%}\n")

    print("TABELA DE INDIFERENCA -- retorno EXTRA em bull necessario pra compensar")
    print("exatamente uma perda ADICIONAL em bear (mesma riqueza final em 10 anos):\n")
    print(f"  {'p_bull':>7} {'p_bear':>7} | {'bear -5pp':>12} {'bear -10pp':>12} "
          f"{'bear -15pp':>12} {'bear -20pp':>12}")
    print("  " + "-" * 72)
    linhas = []
    for p_bull in [0.30, 0.40, 0.50, 0.60]:
        p_bear = 1.0 - p_bull - 0.15
        cel = []
        for delta in [-0.05, -0.10, -0.15, -0.20]:
            d = bull_que_compensa(delta, r_bull_ref, r_bear_ref, p_bull, p_bear)
            cel.append(f"{d:+.1%}" if d is not None else "impossivel")
            linhas.append({"p_bull": p_bull, "p_bear": round(p_bear, 2),
                            "delta_bear_pp": delta * 100,
                            "delta_bull_necessario_pp": round(d * 100, 2) if d is not None else None})
        print(f"  {p_bull:>6.0%} {p_bear:>7.0%} | " + " ".join(f"{c:>12}" for c in cel))

    print("""
LEITURA: quanto MENOS frequente o bull, mais retorno extra ele precisa entregar
pra pagar a mesma perda adicional em bear -- tem menos ocasioes de recuperar o
estrago. O cambio NUNCA e 1:1.
""")
    return pd.DataFrame(linhas)


# ═════════════════════════════════════════════════════════════════════════════
# PARTE C -- block bootstrap dos retornos diarios
# ═════════════════════════════════════════════════════════════════════════════

def block_bootstrap_pareado(series_por_variante: dict, haircut=0.0,
                             anos=ANOS_PROJECAO, n_sim=N_SIMULACOES, seed=SEED):
    """Reamostra blocos CONTIGUOS de dias. Todas as variantes usam os MESMOS
    indices de bloco -> mesmo 'futuro' sorteado, comparacao pareada.
    haircut: fracao a subtrair do retorno diario medio (0.5 = metade do
    excesso de retorno historico), pra testar futuros menos generosos."""
    rng = np.random.default_rng(seed)
    variantes = list(series_por_variante.keys())
    arrays = {v: series_por_variante[v].values for v in variantes}
    n_dias_hist = len(arrays[variantes[0]])
    n_dias_alvo = anos * DIAS_POR_ANO
    n_blocos = int(np.ceil(n_dias_alvo / BLOCO_DIAS))
    max_ini = n_dias_hist - BLOCO_DIAS
    if max_ini <= 0:
        return None

    # aplica haircut multiplicativo sobre (1+r): reduz o retorno composto
    ajustados = {}
    for v in variantes:
        r = arrays[v]
        if haircut > 0:
            media_log = np.mean(np.log1p(np.clip(r, -0.99, None)))
            ajustados[v] = np.expm1(np.log1p(np.clip(r, -0.99, None)) - haircut * media_log)
        else:
            ajustados[v] = r

    finais = {v: np.empty(n_sim) for v in variantes}
    for s in range(n_sim):
        inicios = rng.integers(0, max_ini, size=n_blocos)
        idx = np.concatenate([np.arange(i, i + BLOCO_DIAS) for i in inicios])[:n_dias_alvo]
        for v in variantes:
            r = ajustados[v][idx]
            finais[v][s] = CAPITAL_REPORTE * float(np.prod(1 + np.clip(r, -0.99, None)))
    return finais


def imprime_distribuicao(finais, titulo, como_multiplo=True):
    print(f"\n{titulo}")
    if como_multiplo:
        print(f"  {'Variante':<16} {'P5':>10} {'P25':>10} {'MEDIANA':>11} {'P75':>11} {'P95':>12} {'P(perda)':>10}")
    print("  " + "-" * 84)
    linhas = []
    ordenado = sorted(finais.items(), key=lambda kv: -np.median(kv[1]))
    for v, arr in ordenado:
        p5, p25, p50, p75, p95 = np.percentile(arr, [5, 25, 50, 75, 95])
        m = lambda x: x / CAPITAL_REPORTE
        p_perda = float(np.mean(arr < CAPITAL_REPORTE)) * 100
        print(f"  {v:<16} {m(p5):>9.2f}x {m(p25):>9.2f}x {m(p50):>10.2f}x "
              f"{m(p75):>10.2f}x {m(p95):>11.2f}x {p_perda:>9.1f}%")
        linhas.append({"Variante": v, "P5_mult": round(m(p5), 3), "P25_mult": round(m(p25), 3),
                        "Mediana_mult": round(m(p50), 3), "P75_mult": round(m(p75), 3),
                        "P95_mult": round(m(p95), 3), "Prob_perda_%": round(p_perda, 1),
                        "Mediana_USD": round(p50, 2)})
    return pd.DataFrame(linhas)


def main():
    print("Montando retornos diarios do portfolio (8 veteranas, janela longa)...\n")
    df_btc = carregar_dados("BTCUSDT", "6h")
    btc_close = pd.Series(df_btc["fechamento"].values,
                           index=pd.to_datetime(df_btc["t_abert"].values)).sort_index()
    universo = {a: i for a, i in ATIVOS_PORTFOLIO_V4.items() if a in ATIVOS_LIQUIDOS}
    ret_por_ativo = {}
    for ativo, interval_str in universo.items():
        r = retornos_diarios_por_variante(ativo, interval_str, btc_close)
        if r is not None:
            ret_por_ativo[ativo] = r
    if len(ret_por_ativo) < 2:
        print("Dados insuficientes.")
        return

    pesos = pesos_inverse_vol({a: d["Base"] for a, d in ret_por_ativo.items()})
    series = {}
    for v in VARIANTES_FOCO:
        rp = combinar_portfolio(ret_por_ativo, v, pesos)
        if not rp.empty:
            series[v] = rp
    # alinha todas na mesma janela
    datas = None
    for s in series.values():
        datas = s.index if datas is None else datas.intersection(s.index)
    datas = datas.sort_values()
    series = {v: s.reindex(datas).fillna(0.0) for v, s in series.items()}
    print(f"Serie diaria do portfolio: {len(datas)} dias "
          f"({datas[0].date()} a {datas[-1].date()})\n")

    # ─── medianas por regime (pra Parte B) ───
    eq_bh = (1 + series["BuyHold"]).cumprod()
    med_regime = {}
    freq_ref = None
    for v, s in series.items():
        eq = (1 + s).cumprod()
        rows = []
        for i in range(0, len(datas) - DIAS_POR_ANO, 30):
            j = i + DIAS_POR_ANO
            r = float(eq.iloc[j] / eq.iloc[i] - 1)
            r_bh = float(eq_bh.iloc[j] / eq_bh.iloc[i] - 1)
            reg = "BULL" if r_bh > 0.25 else ("BEAR" if r_bh < -0.25 else "LATERAL")
            rows.append({"r": r, "reg": reg})
        d = pd.DataFrame(rows)
        med_regime[v] = {reg: float(d[d["reg"] == reg]["r"].median()) if (d["reg"] == reg).any() else 0.0
                          for reg in ["BULL", "LATERAL", "BEAR"]}
        if freq_ref is None:
            freq_ref = {reg: float((d["reg"] == reg).mean()) for reg in ["BULL", "LATERAL", "BEAR"]}

    base_med = med_regime["Base"]
    df_b = parte_b(freq_ref, base_med["BULL"], base_med["LATERAL"], base_med["BEAR"])
    df_b.to_csv("riqueza_terminal_v6_parteB_indiferenca.csv", index=False)

    print("\nMedianas anuais medidas por regime (portfolio):")
    print(f"  {'Variante':<16} {'BULL':>10} {'LATERAL':>10} {'BEAR':>10}")
    print("  " + "-" * 48)
    for v, m in med_regime.items():
        print(f"  {v:<16} {m['BULL']:>+9.1%} {m['LATERAL']:>+9.1%} {m['BEAR']:>+9.1%}")

    # ─── PARTE C ───
    print("\n\n" + "=" * 100)
    print(f"PARTE C -- MONTE CARLO {ANOS_PROJECAO} ANOS (block bootstrap, blocos de "
          f"{BLOCO_DIAS} dias, {N_SIMULACOES:,} simulacoes)")
    print("=" * 100)
    print("""
Resultados em MULTIPLO do capital inicial (nao em $), porque os retornos de
2020-2025 em cripto sao extraordinarios e NAO devem ser lidos como previsao
de 10 anos. O que e robusto aqui e a ORDEM entre as variantes e a dispersao
relativa -- nao o numero absoluto.
""")
    finais = block_bootstrap_pareado(series, haircut=0.0)
    df_c = imprime_distribuicao(finais, "Cenario A -- retornos historicos integrais (OTIMISTA IRREAL):")
    df_c.to_csv("riqueza_terminal_v6_parteC_montecarlo.csv", index=False)

    print("\n\nCOMPARACAO PAREADA -- P(linha termina acima da coluna), mesmo futuro sorteado:")
    variantes = list(finais.keys())
    print(f"\n  {'':<16} " + " ".join(f"{v[:13]:>14}" for v in variantes))
    pareado = []
    for a in variantes:
        cel = []
        for b in variantes:
            if a == b:
                cel.append("--")
            else:
                p = float(np.mean(finais[a] > finais[b])) * 100
                cel.append(f"{p:.0f}%")
                pareado.append({"A": a, "B": b, "P_A_maior_B_%": round(p, 1)})
        print(f"  {a:<16} " + " ".join(f"{c:>14}" for c in cel))
    pd.DataFrame(pareado).to_csv("riqueza_terminal_v6_parteC_pareado.csv", index=False)

    # ─── PARTE D: haircuts ───
    print("\n\n" + "=" * 100)
    print("PARTE D -- E SE O FUTURO FOR MENOS GENEROSO? (corte no retorno historico)")
    print("=" * 100)
    print("""
'Haircut' reduz o retorno composto medio mantendo a estrutura de volatilidade
e drawdown. E o teste que importa: a ORDEM entre as variantes se mantem quando
cripto para de dar 87%/ano?
""")
    linhas_d = []
    for nome, hc in [("Haircut 50% (metade do retorno)", 0.5),
                      ("Haircut 75% (um quarto)", 0.75),
                      ("Haircut 90% (quase nada)", 0.90)]:
        f = block_bootstrap_pareado(series, haircut=hc)
        df_cen = imprime_distribuicao(f, f"\n{nome}:")
        melhor_med = df_cen.iloc[0]["Variante"]
        melhor_p25 = df_cen.sort_values("P25_mult", ascending=False).iloc[0]["Variante"]
        menor_perda = df_cen.sort_values("Prob_perda_%").iloc[0]["Variante"]
        print(f"  -> melhor MEDIANA: {melhor_med} | melhor P25: {melhor_p25} | "
              f"menor P(perda): {menor_perda}")
        for _, row in df_cen.iterrows():
            linhas_d.append({"Cenario": nome, "haircut": hc, **row.to_dict()})
    pd.DataFrame(linhas_d).to_csv("riqueza_terminal_v6_parteD_cenarios.csv", index=False)

    print("\n\nSalvos: riqueza_terminal_v6_parteB_indiferenca.csv, "
          "riqueza_terminal_v6_parteC_montecarlo.csv,")
    print("        riqueza_terminal_v6_parteC_pareado.csv, riqueza_terminal_v6_parteD_cenarios.csv")


if __name__ == "__main__":
    main()
