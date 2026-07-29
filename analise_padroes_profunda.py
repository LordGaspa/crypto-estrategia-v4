# -*- coding: utf-8 -*-
# ANALISE PROFUNDA DOS PADROES - v4
# ----------------------------------------------------------------------------
# Arquivo novo, so leitura dos CSVs ja gerados. Nao altera nada.
#
# Responde 4 perguntas:
#   1) O que da significancia estatistica (DSR): numero de trades ou tamanho
#      do historico? (a estrategia so tem edge real onde ha trades suficientes)
#   2) Os otimos por-ativo sao PLATOS robustos ou PICOS solitarios de sorte?
#      (compara o topo-por-Calmar cru vs o ponto escolhido por robustez)
#   3) Quais parametros a estrategia REALMENTE depende (travados) e quais sao
#      livres (indiferentes)? - medido pela dispersao no top-100.
#   4) A receita-ouro cai em chao firme ou em buraco, ativo a ativo?
#      (lookup do combo-ouro dentro do grid completo de cada ativo)

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from config_v4 import ATIVOS_PORTFOLIO_V4, ATIVOS_LIQUIDOS

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

PARAM_COLS = ["media_rapida_per", "media_lenta_per", "media_filtro_tendencia_per",
              "atr_periodo", "atr_multiplicador"]
# grid oficial (de otimizador_v4.PARAMS_TEST) - passo de vizinhanca por parametro
GRID = {
    "media_rapida_per": [5, 7, 8, 9, 10, 12, 14, 15, 18, 21],
    "media_lenta_per": [20, 30, 40, 50, 80, 100, 120, 150, 200],
    "media_filtro_tendencia_per": [50, 100, 150, 200, 250],
    "atr_periodo": [5, 7, 10, 14, 20, 25, 30],
    "atr_multiplicador": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0],
}

OURO = {
    "veterana": {"media_rapida_per": 5, "media_lenta_per": 80, "media_filtro_tendencia_per": 50,
                 "atr_periodo": 5, "atr_multiplicador": 6.0},
    "nova": {"media_rapida_per": 10, "media_lenta_per": 80, "media_filtro_tendencia_per": 100,
             "atr_periodo": 25, "atr_multiplicador": 1.0},
}


def grupo(ativo):
    return "veterana" if ativo in ATIVOS_LIQUIDOS else "nova"


def moda_freq(serie):
    vc = pd.Series(serie).value_counts()
    if vc.empty:
        return None, 0.0
    return vc.index[0], vc.iloc[0] / vc.sum() * 100


def carrega_tudo():
    resumo = pd.read_csv("otimizador_v4_RESUMO_ATIVOS.csv")
    resumo["anos_hist"] = (
        pd.to_datetime(resumo["Periodo_Dev_Fim"]) - pd.to_datetime(resumo["Periodo_Dev_Inicio"])
    ).dt.days / 365.25
    csvs = {}
    for ativo in ATIVOS_PORTFOLIO_V4:
        p = Path(f"otimizador_v4_{ativo}.csv")
        if p.exists():
            csvs[ativo] = pd.read_csv(p)
    return resumo, csvs


# ---------------------------------------------------------------------------
# 0) REAVALIACAO DE AMOSTRAGEM: os padroes se mantem ao alargar o corte?
# ---------------------------------------------------------------------------
def analise_estabilidade(csvs):
    print("=" * 90)
    print("0) REAVALIACAO DE AMOSTRAGEM - o padrao aguenta cortes mais largos?")
    print("=" * 90)
    print("   top-100 = so ~0,36% do grid (fatia fina, onde mora a sorte). Aqui a moda de")
    print("   cada parametro e recalculada em cortes por FRACAO de cada ativo, rankeando por")
    print("   Calmar cru E por Score_Robustez. Moda estavel entre cortes = padrao REAL.\n")
    fracoes = [0.0036, 0.01, 0.05, 0.10]  # ~top100, 1%, 5%, 10%
    rotulos = ["~0.36%", "1%", "5%", "10%"]

    for metrica in ["Calmar", "Score_Robustez"]:
        print(f"\n  ### rankeando por {metrica} ###")
        for g in ("veterana", "nova"):
            ativos_g = [a for a in csvs if grupo(a) == g]
            print(f"\n  [{g.upper()}]  ({len(ativos_g)} ativos)")
            print(f"    {'parametro':28} " + " ".join(f"{r:>13}" for r in rotulos))
            for col in PARAM_COLS:
                celulas = []
                for frac in fracoes:
                    blocos = []
                    for a in ativos_g:
                        df = csvs[a]
                        k = max(20, int(len(df) * frac))
                        blocos.append(df.nlargest(k, metrica)[col])
                    todos = pd.concat(blocos, ignore_index=True)
                    moda, freq = moda_freq(todos)
                    celulas.append(f"{moda}({freq:.0f}%)")
                print(f"    {col:28} " + " ".join(f"{c:>13}" for c in celulas))
    print("\n  >> LEITURA: se a moda de um parametro nao muda (ou muda pouco) do corte fino")
    print("     ao largo, ela e robusta. Se muda muito, aquele 'padrao' do top-100 era ruido.")


def derivar_receita_robusta(csvs, frac=0.05, metrica="Score_Robustez"):
    """Receita destilada de um corte LARGO (5%) rankeado por ROBUSTEZ - menos
    sujeita a overfitting que a moda do top-100 por Calmar cru."""
    receitas = {}
    for g in ("veterana", "nova"):
        ativos_g = [a for a in csvs if grupo(a) == g]
        blocos = []
        for a in ativos_g:
            df = csvs[a]
            k = max(20, int(len(df) * frac))
            blocos.append(df.nlargest(k, metrica))
        todos = pd.concat(blocos, ignore_index=True)
        rec = {}
        for col in PARAM_COLS:
            moda, _ = moda_freq(todos[col])
            rec[col] = float(moda) if col == "atr_multiplicador" else int(moda)
        receitas[g] = rec
    return receitas


# ---------------------------------------------------------------------------
# 1) O QUE DIRIGE O DSR: TRADES vs HISTORICO
# ---------------------------------------------------------------------------
def analise_dsr(resumo):
    print("=" * 90)
    print("1) O QUE DA SIGNIFICANCIA ESTATISTICA (DSR)? Trades ou historico?")
    print("=" * 90)
    d = resumo[["Ativo", "Grupo_Liquidez", "anos_hist", "Num_Trades", "DSR_%", "Calmar"]].copy()
    d = d.sort_values("DSR_%", ascending=False)
    print(f"\n  {'Ativo':14} {'Grupo':13} {'Anos':>5} {'Trades':>7} {'DSR%':>7} {'Calmar':>7}")
    print("  " + "-" * 60)
    for _, r in d.iterrows():
        print(f"  {r['Ativo']:14} {r['Grupo_Liquidez']:13} {r['anos_hist']:5.1f} "
              f"{int(r['Num_Trades']):7d} {r['DSR_%']:7.1f} {r['Calmar']:7.2f}")

    # correlacoes de Spearman (rank) - robustas a saturacao 0/100 do DSR
    rho_trades, p_trades = spearmanr(resumo["Num_Trades"], resumo["DSR_%"])
    rho_hist, p_hist = spearmanr(resumo["anos_hist"], resumo["DSR_%"])
    rho_th, _ = spearmanr(resumo["Num_Trades"], resumo["anos_hist"])
    print(f"\n  Correlacao (Spearman) com DSR%:")
    print(f"    Num_Trades  -> DSR% : rho = {rho_trades:+.3f}  (p={p_trades:.3f})")
    print(f"    Anos_hist   -> DSR% : rho = {rho_hist:+.3f}  (p={p_hist:.3f})")
    print(f"    (Trades e historico sao correlacionados entre si: rho = {rho_th:+.3f})")
    # quantos passam do corte de 5%
    passa = (resumo["DSR_%"] >= 5).sum()
    print(f"\n  {passa}/{len(resumo)} ativos com DSR >= 5% (edge estatistico minimo).")
    liq_passa = resumo[(resumo["DSR_%"] >= 5) & (resumo["Grupo_Liquidez"] == "liquido")].shape[0]
    liq_tot = (resumo["Grupo_Liquidez"] == "liquido").sum()
    print(f"  Entre veteranas/liquidas: {liq_passa}/{liq_tot}.  "
          f"Entre novas: {passa - liq_passa}/{len(resumo) - liq_tot}.")
    print("\n  >> LEITURA: o driver dominante do DSR e o NUMERO DE TRADES. Historico ajuda")
    print("     so na medida em que gera mais trades. Poucos trades = sem significancia,")
    print("     por mais anos de dado que tenha (ex: SUI/PENGU) -> resultado e ruido.")


# ---------------------------------------------------------------------------
# 2) PLATO vs PICO: o otimo por-Calmar cru vs o ponto robusto escolhido
# ---------------------------------------------------------------------------
def analise_plato_vs_pico(csvs):
    print("\n" + "=" * 90)
    print("2) OS OTIMOS SAO PLATOS (robustos) OU PICOS (sorte)?")
    print("=" * 90)
    print("   pico   = melhor Calmar cru (Rank_Calmar=1)")
    print("   robusto= ponto escolhido pela vizinhanca (Rank_Robustez=1, o que o v4 usa)")
    print("   'firmeza' = Score_Robustez / Calmar do ponto ROBUSTO (1.0=plato perfeito)\n")
    print(f"  {'Ativo':14} {'CalmarPico':>10} {'CalmarRobu':>10} {'perde%':>7} {'firmeza':>8}")
    print("  " + "-" * 56)
    linhas = []
    for ativo, df in csvs.items():
        pico = df.loc[df["Rank_Calmar"] == 1].iloc[0]
        robu = df.loc[df["Rank_Robustez"] == 1].iloc[0]
        perde = (1 - robu["Calmar"] / pico["Calmar"]) * 100 if pico["Calmar"] else np.nan
        firmeza = robu["Score_Robustez"] / robu["Calmar"] if robu["Calmar"] else np.nan
        linhas.append({"Ativo": ativo, "grupo": grupo(ativo), "calmar_pico": pico["Calmar"],
                       "calmar_robu": robu["Calmar"], "perde_%": perde, "firmeza": firmeza})
        print(f"  {ativo:14} {pico['Calmar']:10.2f} {robu['Calmar']:10.2f} "
              f"{perde:7.1f} {firmeza:8.2f}")
    dd = pd.DataFrame(linhas)
    print("\n  Medias por grupo (firmeza mais perto de 1.0 = otimo mais confiavel):")
    for g in ("veterana", "nova"):
        sub = dd[dd["grupo"] == g]
        print(f"    {g:9}: firmeza media {sub['firmeza'].mean():.2f} | "
              f"desiste de {sub['perde_%'].mean():4.1f}% do Calmar-pico pra ganhar robustez")
    print("\n  >> LEITURA: firmeza alta = os vizinhos do otimo tambem sao bons (plato); firmeza")
    print("     baixa = otimo isolado (pico de sorte). Veteranas costumam ter plato mais firme.")
    return dd


# ---------------------------------------------------------------------------
# 3) PARAMETROS TRAVADOS vs LIVRES (dispersao no top-100 por grupo)
# ---------------------------------------------------------------------------
def entropia_norm(valores, dominio):
    counts = pd.Series(valores).value_counts()
    p = counts / counts.sum()
    H = -(p * np.log(p)).sum()
    Hmax = np.log(len(dominio)) if len(dominio) > 1 else 1.0
    return H / Hmax if Hmax > 0 else 0.0  # 0=travado num valor, 1=espalhado uniforme


def analise_travados_vs_livres(csvs):
    print("\n" + "=" * 90)
    print("3) QUAIS PARAMETROS A ESTRATEGIA DEPENDE (travados) vs LIVRES?")
    print("=" * 90)
    print("   entropia normalizada no top-100: 0.00 = sempre o mesmo valor (parametro CRITICO)")
    print("                                    1.00 = qualquer valor serve (parametro LIVRE)\n")
    tops = {"veterana": [], "nova": []}
    for ativo, df in csvs.items():
        tops[grupo(ativo)].append(df.nlargest(100, "Calmar"))
    for g in ("veterana", "nova"):
        todos = pd.concat(tops[g], ignore_index=True)
        print(f"  [{g.upper()}]")
        entes = []
        for col in PARAM_COLS:
            e = entropia_norm(todos[col], GRID[col])
            entes.append((col, e))
        for col, e in sorted(entes, key=lambda x: x[1]):
            barra = "#" * int(round(e * 30))
            tag = "<- CRITICO" if e < 0.55 else ("<- livre" if e > 0.9 else "")
            print(f"    {col:28} {e:4.2f} |{barra:<30}| {tag}")
        print()
    print("  >> LEITURA: os parametros com entropia baixa sao os que a estrategia REALMENTE")
    print("     usa; os de entropia alta podem ser fixados em qualquer valor sensato sem perda.")


# ---------------------------------------------------------------------------
# 4) A RECEITA-OURO CAI EM CHAO FIRME? (lookup no grid completo de cada ativo)
# ---------------------------------------------------------------------------
def analise_ouro_no_grid(csvs):
    print("\n" + "=" * 90)
    print("4) A RECEITA-OURO CAI EM CHAO FIRME, ATIVO A ATIVO?")
    print("=" * 90)
    print("   Calmar_ouro = Calmar da receita do grupo naquele ativo (dentro do grid dev)")
    print("   percentil   = posicao da receita-ouro entre as 28.035 combinacoes do ativo")
    print("   (100 = a receita-ouro seria a melhor possivel; 50 = mediana)\n")
    print(f"  {'Ativo':14} {'grupo':9} {'Calmar_ouro':>11} {'Calmar_max':>10} {'percentil':>9}")
    print("  " + "-" * 58)
    linhas = []
    for ativo, df in csvs.items():
        g = grupo(ativo)
        alvo = OURO[g]
        mask = np.ones(len(df), dtype=bool)
        for col, val in alvo.items():
            mask &= (df[col] == val)
        sub = df[mask]
        if sub.empty:
            print(f"  {ativo:14} {g:9} {'(combo ausente no grid)':>30}")
            continue
        calmar_ouro = sub["Calmar"].iloc[0]
        calmar_max = df["Calmar"].max()
        pct = (df["Calmar"] < calmar_ouro).mean() * 100
        linhas.append({"Ativo": ativo, "grupo": g, "calmar_ouro": calmar_ouro,
                       "calmar_max": calmar_max, "percentil": pct})
        print(f"  {ativo:14} {g:9} {calmar_ouro:11.2f} {calmar_max:10.2f} {pct:9.1f}")
    dd = pd.DataFrame(linhas)
    print("\n  Percentil medio da receita-ouro por grupo (quanto maior, melhor a receita generaliza):")
    for g in ("veterana", "nova"):
        sub = dd[dd["grupo"] == g]
        if not sub.empty:
            print(f"    {g:9}: percentil medio {sub['percentil'].mean():5.1f}  "
                  f"(em {len(sub)} ativos)")
    print("\n  >> LEITURA: percentil alto = a receita unica do grupo cai num bom ponto tambem")
    print("     pra aquele ativo especifico. Percentil baixo = aquele ativo e a excecao que")
    print("     realmente precisa de parametros proprios.")
    dd.to_csv("analise_padroes_profunda_ouro_no_grid.csv", index=False)
    return dd


def main():
    resumo, csvs = carrega_tudo()
    analise_estabilidade(csvs)
    analise_dsr(resumo)
    analise_plato_vs_pico(csvs)
    analise_travados_vs_livres(csvs)
    analise_ouro_no_grid(csvs)

    # receita robusta (corte largo 5% por robustez) vs receita-ouro do top-100
    print("\n" + "=" * 90)
    print("5) RECEITA ROBUSTA (corte 5% por Score_Robustez) vs RECEITA-OURO (moda top-100)")
    print("=" * 90)
    rec_robusta = derivar_receita_robusta(csvs, frac=0.05, metrica="Score_Robustez")
    for g in ("veterana", "nova"):
        print(f"\n  [{g}]")
        print(f"    ouro (top-100 Calmar):  {OURO[g]}")
        print(f"    robusta (5% Robustez):  {rec_robusta[g]}")
    print("\n>> csv salvo: analise_padroes_profunda_ouro_no_grid.csv")
    return rec_robusta


if __name__ == "__main__":
    main()
