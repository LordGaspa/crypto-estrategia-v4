# -*- coding: utf-8 -*-
# DIAGNÓSTICO - PORTFÓLIO V4 SÓ COM ATIVOS DE HISTÓRICO LONGO (>= 6 ANOS)
# ----------------------------------------------------------------------------
# Script complementar de diagnóstico. Não altera portfolio_v4.py, nem o
# otimizador_v4.py, nem nada do v2/v3/Código Ômega. Só LÊ os resultados que
# esses scripts já geraram.
#
# Por quê: o portfolio_v4_resultado.csv "oficial" usa os 22 ativos do
# portfólio, mas a janela comum entre TODOS eles é curta (223 dias), porque é
# limitada pelo ativo mais novo (PENGUUSDT, listado em dez/2024). Esse
# diagnóstico exclui TEMPORARIAMENTE (só nesta simulação) os ativos com menos
# de 6 anos de histórico completo, pra ver o portfólio numa janela comum bem
# mais longa e estatisticamente mais robusta. Isso NÃO remove nada do
# portfólio real de 22 ativos — é só um teste em paralelo.
#
# O critério de corte (>= 6 anos de histórico total, da 1ª candle disponível
# até hoje) é calculado a partir dos dados de verdade, não uma lista fixa —
# então além dos exemplos citados no pedido (PENGU, BONK, TAO, SUI,
# 1MBABYDOGE), também caem fora API3, FLOKI, IMX, INJ, PEPE, RENDER e SOL
# (todos com bem menos de 6 anos de histórico). O script imprime a lista
# exata de quem ficou de fora e por quê.
#
# Pesos: reaproveita os MESMOS pesos por volatilidade inversa já calculados
# em portfolio_v4_pesos.csv (não recalcula a volatilidade dos ativos
# remanescentes numa janela diferente) — só re-normaliza esses pesos pra
# somar 100% de novo entre os ativos que sobraram, senão o portfólio deixaria
# de estar 100% alocado e o resultado não seria comparável de forma justa com
# o portfolio_v4_resultado.csv original.
#
# NÃO roda o holdout — holdout_v4.py continua exigindo confirmação explícita.
#
# Como rodar (depois do otimizador_v4.py e do portfolio_v4.py):
#   python portfolio_v4_diagnostico_longo.py

import sys
import warnings
import numpy as np
import pandas as pd

from config_v4 import carregar_dados
from portfolio_v4 import curva_diaria_do_ativo, classificar_liquidez

warnings.simplefilter(action="ignore", category=FutureWarning)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

RESUMO_CSV = "otimizador_v4_RESUMO_ATIVOS.csv"
PESOS_CSV = "portfolio_v4_pesos.csv"
ANOS_MINIMOS_HISTORICO = 6.0


def anos_de_historico(ativo: str, interval_str: str) -> float:
    df = carregar_dados(ativo, interval_str)
    if df.empty or len(df) < 2:
        return 0.0
    dias = (df["t_abert"].iloc[-1] - df["t_abert"].iloc[0]).days
    return dias / 365.25


def main():
    df_resumo = pd.read_csv(RESUMO_CSV)
    df_pesos = pd.read_csv(PESOS_CSV)
    if df_resumo.empty or df_pesos.empty:
        print("[ERRO] rode otimizador_v4.py e portfolio_v4.py antes deste diagnóstico.")
        return

    print(f"Checando histórico total de cada ativo (corte: >= {ANOS_MINIMOS_HISTORICO} anos)...")
    qualificados, excluidos = [], []
    for _, row in df_resumo.iterrows():
        ativo, interval_str = row["Ativo"], row["Interval"]
        anos = anos_de_historico(ativo, interval_str)
        if anos >= ANOS_MINIMOS_HISTORICO:
            qualificados.append(ativo)
        else:
            excluidos.append((ativo, round(anos, 2)))

    print(f"\nAtivos EXCLUÍDOS deste diagnóstico (histórico < {ANOS_MINIMOS_HISTORICO} anos):")
    for ativo, anos in sorted(excluidos, key=lambda x: x[1]):
        print(f"    {ativo}: {anos} anos")
    print(f"\nAtivos mantidos ({len(qualificados)} de {len(df_resumo)}): {', '.join(sorted(qualificados))}")

    if len(qualificados) < 2:
        print("[ERRO] menos de 2 ativos qualificados — não dá pra montar portfólio.")
        return

    print(f"\nReconstruindo curva diária dos {len(qualificados)} ativos qualificados...")
    retornos_por_ativo = {}
    for _, row in df_resumo[df_resumo["Ativo"].isin(qualificados)].iterrows():
        ativo = row["Ativo"]
        interval_str = row["Interval"]
        params = dict(
            media_rapida=int(row["media_rapida_per"]),
            media_lenta=int(row["media_lenta_per"]),
            media_filtro=int(row["media_filtro_tendencia_per"]),
            atr_periodo=int(row["atr_periodo"]),
            atr_multiplicador=float(row["atr_multiplicador"]),
        )
        info_liq = classificar_liquidez(ativo)
        print(f"  {ativo} ({interval_str}, {info_liq['grupo']})...")
        try:
            retornos_por_ativo[ativo] = curva_diaria_do_ativo(
                ativo, interval_str, params, info_liq["taxa"], info_liq["slippage"]
            )
        except Exception as e:
            print(f"    [AVISO] falhou pra {ativo}: {e}")

    if len(retornos_por_ativo) < 2:
        print("[ERRO] menos de 2 curvas válidas — não dá pra montar portfólio.")
        return

    datas_comuns = None
    for serie in retornos_por_ativo.values():
        idx = serie.index
        datas_comuns = idx if datas_comuns is None else datas_comuns.intersection(idx)
    datas_comuns = datas_comuns.sort_values()

    # reaproveita os pesos originais (por volatilidade inversa, já calculados
    # em portfolio_v4.py) só dos ativos remanescentes, renormalizados pra
    # somar 100% de novo
    pesos_originais = df_pesos.set_index("Ativo")["Peso_Portfolio_%"] / 100.0
    pesos_subset = pesos_originais.reindex(list(retornos_por_ativo.keys())).dropna()
    pesos_normalizados = pesos_subset / pesos_subset.sum()

    df_ret = pd.DataFrame(
        {ativo: serie.reindex(datas_comuns) for ativo, serie in retornos_por_ativo.items()}
    )
    pesos_alinhados = pesos_normalizados.reindex(df_ret.columns)

    df_pesos_usados = pd.DataFrame(
        {
            "Ativo": pesos_alinhados.index,
            "Peso_Original_%": (pesos_subset.reindex(pesos_alinhados.index) * 100).round(2).values,
            "Peso_Renormalizado_%": (pesos_alinhados * 100).round(2).values,
        }
    ).sort_values("Peso_Renormalizado_%", ascending=False)
    print(f"\nPesos usados (reaproveitados de {PESOS_CSV}, renormalizados p/ 100%):")
    print(df_pesos_usados.to_string(index=False))

    retorno_portfolio_diario = (df_ret * pesos_alinhados).sum(axis=1)
    equity_portfolio = (1 + retorno_portfolio_diario).cumprod()
    retorno_total = equity_portfolio.iloc[-1] - 1.0

    dias = (datas_comuns[-1] - datas_comuns[0]).days
    anos = dias / 365.25 if dias > 0 else None
    retorno_anualizado = (1 + retorno_total) ** (1 / anos) - 1 if anos and anos > 0 else None

    running_max = equity_portfolio.cummax()
    dd_series = (running_max - equity_portfolio) / running_max
    max_dd = dd_series.max()

    calmar_portfolio = (
        retorno_anualizado / max_dd if (max_dd and max_dd > 0 and retorno_anualizado is not None) else None
    )

    resultado = {
        "Periodo_Comum_Inicio": datas_comuns[0].date(),
        "Periodo_Comum_Fim": datas_comuns[-1].date(),
        "N_Dias_Comuns": len(datas_comuns),
        "N_Ativos": len(retornos_por_ativo),
        "Retorno_Total_%": round(retorno_total * 100, 2),
        "Retorno_Anualizado_%": round(retorno_anualizado * 100, 2) if retorno_anualizado is not None else None,
        "DD_%": round(max_dd * 100, 2),
        "Calmar_Portfolio": round(calmar_portfolio, 3) if calmar_portfolio is not None else None,
    }
    df_resultado = pd.DataFrame([resultado])
    df_resultado.to_csv("portfolio_v4_diagnostico_longo_resultado.csv", index=False)
    print(f"\n✅ Salvo: portfolio_v4_diagnostico_longo_resultado.csv\n")
    print(df_resultado.to_string(index=False))

    try:
        df_original = pd.read_csv("portfolio_v4_resultado.csv")
        print("\n" + "=" * 78)
        print("COMPARAÇÃO LADO A LADO")
        print("=" * 78)
        comparacao = pd.concat(
            [
                df_original.assign(Versao="Original (22 ativos)"),
                df_resultado.assign(Versao=f"Diagnóstico (>= {ANOS_MINIMOS_HISTORICO} anos, {len(qualificados)} ativos)"),
            ],
            ignore_index=True,
        )
        cols = ["Versao"] + [c for c in comparacao.columns if c != "Versao"]
        print(comparacao[cols].to_string(index=False))
    except FileNotFoundError:
        print("[AVISO] portfolio_v4_resultado.csv não encontrado — rode portfolio_v4.py pra ter a comparação.")


if __name__ == "__main__":
    main()
