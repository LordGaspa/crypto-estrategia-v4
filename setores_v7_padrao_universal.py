# -*- coding: utf-8 -*-
# PADRAO UNIVERSAL vs PADRAO SETORIAL -- o que os dados realmente mostraram
# ----------------------------------------------------------------------------
# O teste de permutacao (setores_v7_consistencia.py) refutou a hipotese
# setorial: agrupar por setor NAO organiza os parametros melhor que agrupar
# aleatoriamente (p entre 0.30 e 0.78 nos 4 timeframes).
#
# MAS o motivo da refutacao e interessante: olhando os "parametros mais
# constantes" de cada setor, o mesmo valor aparece em quase TODOS os setores
# (atr_multiplicador=6.0 principalmente). Ou seja: existe consistencia real,
# so que ela e GLOBAL, nao setorial -- e por isso grupos aleatorios exibem a
# mesma consistencia (todo mundo quer o mesmo valor, tanto faz como agrupa).
#
# Este script quantifica isso: mede a preferencia de parametro no universo
# INTEIRO (43 ativos), e testa separadamente a outra hipotese do usuario --
# a de que o TIMEFRAME ideal muda por setor.
#
# So periodo de DESENVOLVIMENTO. Holdout LACRADO.
#
# Como rodar:
#   .venv\Scripts\python.exe setores_v7_padrao_universal.py

import os
import sys
import glob
import warnings
import numpy as np
import pandas as pd
from scipy.stats import kruskal

from setores_v7 import SETORES, UNIVERSO_V7, setor_de
from setores_v7_consistencia import carregar_perfis, perfil_vetor, PARAMS

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

PREFIXO = "otimizador_v7_"


def main():
    perfis = carregar_perfis()
    if not perfis:
        print("Rode antes: otimizador_v7_setores.py")
        return

    tfs = sorted({tf for (_a, tf) in perfis}, key=lambda x: int(x.replace("h", "")))

    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 100)
    print("1. PADRAO UNIVERSAL -- preferencia de parametro no universo INTEIRO (43 ativos)")
    print("=" * 100)
    print("""
Se um valor aparece como preferido na maioria dos ativos INDEPENDENTE de setor,
ele e uma propriedade da ESTRATEGIA (ou do mercado cripto como um todo), nao do
setor. Isso explica por que o teste de permutacao deu nao-significativo.
""")

    linhas_univ = []
    for tf in tfs:
        vetores = {a: perfil_vetor(perfis[(a, tf)]) for (a, t) in perfis if t == tf}
        if len(vetores) < 10:
            continue
        print(f"--- Timeframe {tf} ({len(vetores)} ativos) ---")
        print(f"  {'parametro':<32} {'valor +comum':>13} {'% dos ativos':>13}   2o mais comum")
        for p in PARAMS:
            vals = [v[p] for v in vetores.values()]
            vc = pd.Series(vals).value_counts(normalize=True)
            top1_v, top1_f = vc.index[0], vc.iloc[0]
            seg = f"{vc.index[1]} ({vc.iloc[1]:.0%})" if len(vc) > 1 else "-"
            marca = "  <== FORTE" if top1_f >= 0.6 else ""
            print(f"  {p:<32} {str(top1_v):>13} {top1_f:>12.0%}   {seg}{marca}")
            linhas_univ.append({"Timeframe": tf, "Parametro": p, "Valor_Mais_Comum": top1_v,
                                 "Fracao_Ativos": round(float(top1_f), 3)})
        print()

    pd.DataFrame(linhas_univ).to_csv("setores_v7_padrao_universal.csv", index=False)

    # ═══════════════════════════════════════════════════════════════════════
    print("=" * 100)
    print("2. O TIMEFRAME IDEAL MUDA POR SETOR? (hipotese separada do usuario)")
    print("=" * 100)
    print("""
Aqui a pergunta e outra: nao 'os parametros sao parecidos dentro do setor',
mas 'o timeframe que funciona melhor depende do setor'. Testado com
Kruskal-Wallis sobre o Calmar do topo da grade de cada ativo -- se os setores
tiverem distribuicoes de Calmar diferentes ENTRE timeframes, isso aparece.
""")
    caminho = f"{PREFIXO}RESUMO.csv"
    if not os.path.exists(caminho):
        caminho = f"{PREFIXO}RESUMO_parcial.csv"
    if not os.path.exists(caminho):
        print("  RESUMO nao encontrado.")
        return
    df = pd.read_csv(caminho)
    df = df[df["Ativo"].isin(UNIVERSO_V7)].copy()
    df["Setor"] = df["Ativo"].map(setor_de)

    # Para cada ativo, qual timeframe deu o melhor Calmar?
    idx = df.groupby("Ativo")["Melhor_Calmar"].idxmax()
    melhor_tf = df.loc[idx, ["Ativo", "Setor", "Interval", "Melhor_Calmar"]]
    print("Timeframe vencedor por ativo, contado por setor:")
    tab = pd.crosstab(melhor_tf["Setor"], melhor_tf["Interval"])
    cols = [c for c in ["4h", "6h", "8h", "12h"] if c in tab.columns]
    print(tab[cols].to_string())

    # Teste: a distribuicao do timeframe vencedor difere entre setores?
    from scipy.stats import chi2_contingency
    try:
        chi2, p_chi, dof, esp = chi2_contingency(tab[cols].values)
        n_baixo = int((esp < 5).sum())
        print(f"\n  Qui-quadrado: p = {p_chi:.4f}", end="  ")
        if p_chi < 0.05:
            print("-> a escolha de timeframe DEPENDE do setor")
        else:
            print("-> nao significativo (timeframe vencedor nao depende do setor)")
        if n_baixo > 0:
            print(f"  [RESSALVA] {n_baixo} celulas com frequencia esperada < 5 -- "
                  f"qui-quadrado pouco confiavel com amostra deste tamanho")
    except Exception as e:
        print(f"  (qui-quadrado nao aplicavel: {e})")

    # Kruskal por timeframe: o Calmar difere entre setores?
    print("\n  Calmar do topo da grade difere entre SETORES, dentro de cada timeframe?")
    for tf in cols:
        sub = df[df["Interval"] == tf]
        grupos = [g["Melhor_Calmar"].values for _s, g in sub.groupby("Setor") if len(g) >= 3]
        if len(grupos) >= 3:
            h, p = kruskal(*grupos)
            sig = "SIM" if p < 0.05 else "nao"
            print(f"    {tf:>4}: p = {p:.4f}  -> difere entre setores? {sig}")

    print("""
NOTA: 'Melhor_Calmar' e o TOPO da grade de 28k combinacoes -- e a estatistica
mais contaminada por overfitting que existe neste projeto. Diferencas aqui
indicam que alguns setores sao mais faceis de ajustar (ex.: MEME, com historico
curto e volatilidade alta, atinge Calmar altissimo no topo da grade), nao que
a estrategia va performar assim. Usar so como sinal exploratorio.
""")


if __name__ == "__main__":
    main()
