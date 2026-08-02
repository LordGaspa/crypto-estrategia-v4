# -*- coding: utf-8 -*-
# CONSISTENCIA DE PARAMETROS POR SETOR -- com TESTE DE PERMUTACAO
# ----------------------------------------------------------------------------
# Pergunta do usuario: "ativos do mesmo setor devem ter padroes entre si" --
# quais parametros (medias, ATR, timeframe) sao mais CONSTANTES dentro de cada
# setor? Note que a pergunta e sobre CONSISTENCIA, nao sobre lucro maximo:
# buscar o parametro mais lucrativo foi o que gerou overfitting no v5.
#
# O PROBLEMA que este script existe pra resolver: se eu simplesmente agrupar os
# ativos por setor e reportar "olha, os MEME concordam em media_lenta=30!",
# isso nao prova nada -- QUALQUER agrupamento de ativos vai mostrar alguma
# concordancia por acaso, ainda mais com poucos grupos e muitos parametros.
#
# TESTE DE PERMUTACAO (o nucleo honesto da analise): mede a consistencia do
# agrupamento SETORIAL e compara com a consistencia de milhares de
# agrupamentos ALEATORIOS dos MESMOS TAMANHOS. Se o setorial nao for mais
# consistente que o acaso, a hipotese esta refutada -- e reportamos isso.
#
# So periodo de DESENVOLVIMENTO (os CSVs do v7 so contem dev). Holdout LACRADO.
#
# Como rodar:
#   .venv\Scripts\python.exe setores_v7_consistencia.py

import os
import sys
import glob
import warnings
import numpy as np
import pandas as pd

from setores_v7 import SETORES, UNIVERSO_V7, setor_de, MIN_ATIVOS_CONFIAVEL

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

PREFIXO = "otimizador_v7_"
PARAMS = ["media_rapida_per", "media_lenta_per", "media_filtro_tendencia_per",
          "atr_periodo", "atr_multiplicador"]
CORTE_ROBUSTEZ_PCT = 5.0   # top 5% por Score_Robustez (mesmo criterio da RECEITA_ROBUSTA)
N_PERMUTACOES = 5000
SEED = 42


def carregar_perfis():
    """Para cada (ativo, timeframe) com CSV disponivel, extrai o PERFIL: a
    distribuicao de cada parametro no corte robusto (top 5% por
    Score_Robustez). Devolve dict {(ativo, tf): {param: pd.Series de valores}}.

    Usa o corte por ROBUSTEZ, nao por Calmar puro -- e a licao do v5: o topo
    por Calmar puro e ruido; a vizinhanca robusta e o que se repete."""
    perfis = {}
    for caminho in sorted(glob.glob(f"{PREFIXO}*_*.csv")):
        base = os.path.basename(caminho).replace(PREFIXO, "").replace(".csv", "")
        if base.startswith("RESUMO"):
            continue
        if "_" not in base:
            continue
        ativo, tf = base.rsplit("_", 1)
        if ativo not in UNIVERSO_V7:
            continue
        try:
            d = pd.read_csv(caminho)
        except Exception:
            continue
        if d.empty or "Score_Robustez" not in d.columns:
            continue
        d = d.dropna(subset=["Score_Robustez"])
        if len(d) < 50:
            continue
        n_corte = max(10, int(len(d) * CORTE_ROBUSTEZ_PCT / 100))
        top = d.nlargest(n_corte, "Score_Robustez")
        perfis[(ativo, tf)] = {p: top[p] for p in PARAMS}
    return perfis


def moda_e_concentracao(serie: pd.Series):
    """Valor mais frequente e que fracao do corte ele representa."""
    vc = serie.value_counts(normalize=True)
    return vc.index[0], float(vc.iloc[0])


def perfil_vetor(perfil: dict) -> dict:
    """Resume o perfil de um (ativo,tf) na MODA de cada parametro -- e essa
    moda que comparamos entre ativos."""
    return {p: moda_e_concentracao(s)[0] for p, s in perfil.items()}


def consistencia_grupo(vetores: list) -> float:
    """Consistencia de um grupo de ativos = media, entre os parametros, da
    fracao de ativos que concordam com o valor mais comum daquele parametro.
    1.0 = todos os ativos do grupo querem exatamente os mesmos parametros;
    ~1/n_valores = concordancia de acaso."""
    if len(vetores) < 2:
        return np.nan
    scores = []
    for p in PARAMS:
        vals = [v[p] for v in vetores if p in v]
        if not vals:
            continue
        vc = pd.Series(vals).value_counts(normalize=True)
        scores.append(float(vc.iloc[0]))
    return float(np.mean(scores)) if scores else np.nan


def teste_permutacao(vetores_por_ativo: dict, tamanhos: list, consistencia_obs: float,
                      n_perm=N_PERMUTACOES, seed=SEED):
    """Compara a consistencia media do agrupamento setorial com a de
    agrupamentos ALEATORIOS dos mesmos tamanhos. p-value = fracao de
    permutacoes que atingem consistencia >= a observada."""
    rng = np.random.default_rng(seed)
    ativos = list(vetores_por_ativo)
    nulos = np.empty(n_perm)
    for i in range(n_perm):
        emb = list(rng.permutation(ativos))
        cs, ini = [], 0
        for t in tamanhos:
            grupo = emb[ini:ini + t]
            ini += t
            c = consistencia_grupo([vetores_por_ativo[a] for a in grupo])
            if not np.isnan(c):
                cs.append(c)
        nulos[i] = np.mean(cs) if cs else np.nan
    nulos = nulos[np.isfinite(nulos)]
    p_value = float(np.mean(nulos >= consistencia_obs)) if len(nulos) else np.nan
    return p_value, nulos


def main():
    print("=" * 100)
    print("CONSISTENCIA DE PARAMETROS POR SETOR -- com teste de permutacao")
    print("=" * 100)

    perfis = carregar_perfis()
    if not perfis:
        print("\nNenhum CSV do otimizador v7 encontrado ainda.")
        print("Rode antes: .venv\\Scripts\\python.exe otimizador_v7_setores.py")
        return

    tfs = sorted({tf for (_a, tf) in perfis}, key=lambda x: int(x.replace("h", "")))
    ativos_ok = sorted({a for (a, _tf) in perfis})
    print(f"\nPerfis carregados: {len(perfis)} ({len(ativos_ok)} ativos x {len(tfs)} timeframes: {', '.join(tfs)})")
    print(f"Corte: top {CORTE_ROBUSTEZ_PCT:.0f}% por Score_Robustez de cada grade\n")

    # ─── 1. Qual timeframe cada setor prefere? ───
    print("=" * 100)
    print("1. TIMEFRAME PREFERIDO POR SETOR (mediana do melhor Calmar de cada ativo)")
    print("=" * 100)
    resumo_path = f"{PREFIXO}RESUMO.csv"
    resumo_parcial = f"{PREFIXO}RESUMO_parcial.csv"
    df_res = None
    for caminho in (resumo_path, resumo_parcial):
        if os.path.exists(caminho):
            df_res = pd.read_csv(caminho)
            break
    if df_res is not None and not df_res.empty:
        df_res["Setor"] = df_res["Ativo"].map(lambda a: setor_de(a) if a in UNIVERSO_V7 else None)
        piv = df_res.pivot_table(index="Setor", columns="Interval", values="Melhor_Calmar", aggfunc="median")
        cols = [c for c in ["4h", "6h", "8h", "12h"] if c in piv.columns]
        piv = piv[cols]
        print(piv.round(3).to_string())
        print("\nTimeframe com melhor Calmar mediano por setor:")
        for setor in piv.index:
            linha = piv.loc[setor].dropna()
            if not linha.empty:
                print(f"  {setor:<12} {linha.idxmax():>4}  (Calmar {linha.max():.3f})")
        print("\nATENCAO: 'melhor Calmar' e o topo da grade -- sujeito a overfitting.")
        print("Serve pra indicar tendencia de timeframe, NAO pra escolher receita.")

    # ─── 2. Consistencia dentro de cada setor, por timeframe ───
    print("\n" + "=" * 100)
    print("2. CONSISTENCIA DE PARAMETROS DENTRO DE CADA SETOR")
    print("   (fracao media de ativos do setor que concordam no valor mais comum)")
    print("=" * 100)

    linhas_cons = []
    for tf in tfs:
        vetores = {a: perfil_vetor(perfis[(a, tf)]) for (a, t) in perfis if t == tf for a in [a]}
        if len(vetores) < 4:
            continue
        print(f"\n--- Timeframe {tf} ({len(vetores)} ativos com dados) ---")
        print(f"  {'Setor':<12} {'n':>3} {'consistencia':>13}   parametros mais constantes")
        cons_por_setor = {}
        for setor, ativos_setor in SETORES.items():
            presentes = [a for a in ativos_setor if a in vetores]
            if len(presentes) < 2:
                continue
            vets = [vetores[a] for a in presentes]
            c = consistencia_grupo(vets)
            cons_por_setor[setor] = (c, len(presentes))
            # quais parametros concordam mais
            detalhe = []
            for p in PARAMS:
                vals = [v[p] for v in vets]
                vc = pd.Series(vals).value_counts(normalize=True)
                if vc.iloc[0] >= 0.6:  # 60%+ dos ativos concordam
                    detalhe.append(f"{p.replace('_per','').replace('media_','m_')}={vc.index[0]} ({vc.iloc[0]:.0%})")
            marca = "" if len(presentes) >= MIN_ATIVOS_CONFIAVEL else " [poucos]"
            print(f"  {setor:<12} {len(presentes):>3} {c:>12.1%}{marca}   {', '.join(detalhe) if detalhe else '-'}")
            linhas_cons.append({"Timeframe": tf, "Setor": setor, "N_Ativos": len(presentes),
                                 "Consistencia": round(c, 4)})

        # ─── 3. TESTE DE PERMUTACAO ───
        tamanhos = [n for (_c, n) in cons_por_setor.values()]
        cons_obs = float(np.mean([c for (c, _n) in cons_por_setor.values()]))
        p_value, nulos = teste_permutacao(vetores, tamanhos, cons_obs)
        media_nula = float(np.mean(nulos))
        print(f"\n  TESTE DE PERMUTACAO ({N_PERMUTACOES:,} agrupamentos aleatorios do mesmo tamanho):")
        print(f"    consistencia SETORIAL observada : {cons_obs:.1%}")
        print(f"    consistencia de grupos ALEATORIOS: {media_nula:.1%} (media)")
        print(f"    p-value: {p_value:.4f}", end="  ")
        if p_value < 0.05:
            print("-> SETOR EXPLICA ALGO (significativo)")
        else:
            print("-> NAO significativo: o setor nao explica melhor que o acaso")
        linhas_cons.append({"Timeframe": tf, "Setor": "__PERMUTACAO__", "N_Ativos": len(vetores),
                             "Consistencia": round(cons_obs, 4), "Consistencia_Nula": round(media_nula, 4),
                             "p_value": round(p_value, 4)})

    if linhas_cons:
        pd.DataFrame(linhas_cons).to_csv("setores_v7_consistencia.csv", index=False)
        print(f"\n\nSalvo: setores_v7_consistencia.csv ({len(linhas_cons)} linhas)")

    print("""
COMO LER: se o p-value do teste de permutacao for >= 0.05, a divisao por setor
NAO organiza os parametros melhor que uma divisao aleatoria -- e a hipotese
(por mais intuitiva que seja) nao se sustenta nos dados. Nesse caso, derivar
uma "receita por setor" seria ajustar ruido, exatamente o erro que a
RECEITA_ROBUSTA foi criada pra evitar.
""")


if __name__ == "__main__":
    main()
