# -*- coding: utf-8 -*-
# HOLDOUT V4 - validação final no período lacrado (ÚLTIMOS 12 MESES)
# ----------------------------------------------------------------------------
# ATENÇÃO: este script SÓ deve ser executado quando o usuário pedir
# explicitamente, depois de revisar os resultados do período de
# desenvolvimento (otimizador_v4.py + portfolio_v4.py). Ele é o ÚNICO lugar
# em todo o v4 que toca no período lacrado (holdout).
#
# Trava técnica: rodar "python holdout_v4.py" sem o flag abaixo NÃO faz nada
# além de imprimir este aviso. Só executa de verdade com:
#   python holdout_v4.py --eu-confirmo-holdout-final
#
# ----------------------------------------------------------------------------
# SOBRE O QUE ESTE HOLDOUT REALMENTE TESTA (documentado agora, pra quando
# chegarmos nessa etapa):
#
# Os últimos 12 meses (aproximadamente 2025-07 a 2026-07, a partir de quando
# este arquivo foi escrito) foram, no geral, um período de mercado de
# BAIXA/LATERALIZAÇÃO pra maior parte das criptos do portfólio — não um
# período de captura de alta. Isso já aparece nas janelas mais recentes do
# walk-forward do v2 (walkforward_janelas_TODOS_ATIVOS.csv): por exemplo BTC
# 2026 (parcial) buy&hold -27,56%, XRP 2025 buy&hold -11,58%, FET 2025
# buy&hold -84,39%, BNB 2026 (parcial) buy&hold -34,53%.
#
# Ou seja: este holdout testa principalmente a CAPACIDADE DE PROTEÇÃO DE
# CAPITAL da estratégia (perder pouco ou nada quando o mercado não sobe) — e
# NÃO a capacidade de capturar uma alta forte, que já foi validada nas
# janelas antigas do walk-forward do v2 com buy&hold bem positivo (ex: SOL
# 2023 +920%, BNB 2021 +1268%, XRP 2021 +277%, FET 2023 +634%).
#
# Por isso, quando este script rodar de verdade, ele SEMPRE mostra as duas
# metades lado a lado:
#   1) proteção em baixa/lateral -> resultado do holdout (este script)
#   2) captura em alta -> as janelas de walkforward_janelas_TODOS_ATIVOS.csv
#      que tiveram Lucro_BuyHold_TESTE_% positivo (do v2)
# Julgar a estratégia só por um dos dois lados dá uma imagem incompleta.
#
# Como rodar (só quando pedido explicitamente):
#   python holdout_v4.py --eu-confirmo-holdout-final

import sys
import warnings
import numpy as np
import pandas as pd

from config_v4 import CANDLES_POR_DIA, carregar_dados, separar_periodos, classificar_liquidez
from otimizador_v4 import executar_backtest_v4

warnings.simplefilter(action="ignore", category=FutureWarning)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

RESUMO_CSV = "otimizador_v4_RESUMO_ATIVOS.csv"
PESOS_CSV = "portfolio_v4_pesos.csv"
V2_JANELAS_CSV = "walkforward_janelas_TODOS_ATIVOS.csv"


def buy_and_hold(abertura: np.ndarray, fechamento: np.ndarray) -> float:
    if len(abertura) < 2 or abertura[0] <= 0:
        return 0.0
    return round(((fechamento[-1] - abertura[0]) / abertura[0]) * 100.0, 2)


def _metricas_portfolio(retorno_diario: pd.Series, datas: pd.DatetimeIndex) -> dict:
    equity = (1 + retorno_diario).cumprod()
    retorno_total = equity.iloc[-1] - 1.0
    dias = (datas[-1] - datas[0]).days
    anos = dias / 365.25 if dias > 0 else None
    retorno_anualizado = (1 + retorno_total) ** (1 / anos) - 1 if anos and anos > 0 else None
    running_max = equity.cummax()
    dd_series = (running_max - equity) / running_max
    max_dd = dd_series.max()
    calmar = retorno_anualizado / max_dd if (max_dd and max_dd > 0 and retorno_anualizado is not None) else None
    return {
        "Periodo_Comum_Inicio": datas[0].date(),
        "Periodo_Comum_Fim": datas[-1].date(),
        "N_Dias_Comuns": len(datas),
        "Retorno_Total_%": round(retorno_total * 100, 2),
        "Retorno_Anualizado_%": round(retorno_anualizado * 100, 2) if retorno_anualizado is not None else None,
        "DD_%": round(max_dd * 100, 2),
        "Calmar_Portfolio": round(calmar, 3) if calmar is not None else None,
    }


def validar_holdout_final():
    """A ÚNICA função de todo o v4 que lê o período lacrado (holdout). Nunca
    é chamada automaticamente por nenhum outro script v4 — só roda via
    `python holdout_v4.py --eu-confirmo-holdout-final`."""
    df_resumo = pd.read_csv(RESUMO_CSV)
    if df_resumo.empty:
        print(f"[ERRO] {RESUMO_CSV} vazio. Rode otimizador_v4.py primeiro.")
        return

    linhas = []
    ret_diario_estrategia = {}
    ret_diario_buyhold = {}
    for _, row in df_resumo.iterrows():
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
        df = carregar_dados(ativo, interval_str)
        if df.empty:
            print(f"[AVISO] sem dados para {ativo}, pulando.")
            continue
        periodos = separar_periodos(df["t_abert"])
        idx_dev_fim = periodos["idx_dev_fim"]
        df_holdout = df.iloc[idx_dev_fim:].reset_index(drop=True)
        if len(df_holdout) < 30:
            print(f"[AVISO] holdout muito curto pra {ativo} ({len(df_holdout)} candles), pulando.")
            continue

        df_fast = {
            "abertura": df_holdout["abertura"].values,
            "minima": df_holdout["minima"].values,
            "fechamento": df_holdout["fechamento"].values,
            "t_abert": df_holdout["t_abert"].values,
        }
        df_fast[f"ma_{params['media_rapida']}"] = (
            df_holdout["fechamento"].rolling(params["media_rapida"]).mean().values
        )
        df_fast[f"ma_{params['media_lenta']}"] = (
            df_holdout["fechamento"].rolling(params["media_lenta"]).mean().values
        )
        df_fast[f"ma_f_{params['media_filtro']}"] = (
            df_holdout["fechamento"].rolling(params["media_filtro"]).mean().values
        )
        tr = pd.concat(
            [
                df_holdout["maxima"] - df_holdout["minima"],
                (df_holdout["maxima"] - df_holdout["fechamento"].shift()).abs(),
                (df_holdout["minima"] - df_holdout["fechamento"].shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        df_fast[f"atr_{params['atr_periodo']}"] = tr.rolling(params["atr_periodo"]).mean().values

        candles_dia = CANDLES_POR_DIA[interval_str]
        res = executar_backtest_v4(
            df_fast, params, 0, len(df_fast["fechamento"]), info_liq["taxa"], info_liq["slippage"], candles_dia,
            incluir_equity=True,
        )
        bh = buy_and_hold(df_fast["abertura"], df_fast["fechamento"])

        linhas.append(
            {
                "Ativo": ativo,
                "Periodo_Holdout_Inicio": periodos["holdout_inicio"].date() if periodos["holdout_inicio"] is not None else None,
                "Periodo_Holdout_Fim": periodos["holdout_fim"].date() if periodos["holdout_fim"] is not None else None,
                "Retorno_Estrategia_TESTE_%": res["retorno_total_pct"],
                "Retorno_BuyHold_TESTE_%": bh,
                "DD_TESTE_%": res["drawdown_pct"],
                "Num_Trades": res["num_trades"],
            }
        )

        # séries diárias (por data de calendário) pra combinar no portfólio
        # completo do holdout, com os MESMOS pesos já calculados em
        # portfolio_v4.py — nada é reotimizado ou reajustado aqui.
        datas_holdout = pd.to_datetime(df_fast["t_abert"])
        eq_estrategia = pd.Series(res["equity"], index=datas_holdout).resample("1D").last().dropna()
        ret_diario_estrategia[ativo] = eq_estrategia.pct_change().dropna()

        preco_serie = pd.Series(df_fast["fechamento"], index=datas_holdout).resample("1D").last().dropna()
        ret_diario_buyhold[ativo] = preco_serie.pct_change().dropna()

    df_holdout_resultado = pd.DataFrame(linhas)
    df_holdout_resultado.to_csv("holdout_v4_resultado.csv", index=False)

    print("\n" + "=" * 78)
    print("HOLDOUT V4 - período lacrado (últimos 12 meses) - PROTEÇÃO DE CAPITAL")
    print("=" * 78)
    print(df_holdout_resultado.to_string(index=False))

    # ---- Portfólio completo (22 ativos, pesos já calculados em portfolio_v4.py) no holdout ----
    try:
        df_pesos = pd.read_csv(PESOS_CSV)
        pesos_originais = df_pesos.set_index("Ativo")["Peso_Portfolio_%"] / 100.0

        ativos_com_serie = [a for a in ret_diario_estrategia if a in pesos_originais.index]
        pesos_subset = pesos_originais.reindex(ativos_com_serie)
        pesos_normalizados = pesos_subset / pesos_subset.sum()
        if len(ativos_com_serie) < len(pesos_originais):
            faltando = sorted(set(pesos_originais.index) - set(ativos_com_serie))
            print(
                f"\n[AVISO] {len(faltando)} ativo(s) sem série de holdout válida, "
                f"pesos renormalizados entre os {len(ativos_com_serie)} restantes: {', '.join(faltando)}"
            )

        datas_comuns = None
        for ativo in ativos_com_serie:
            idx = ret_diario_estrategia[ativo].index
            datas_comuns = idx if datas_comuns is None else datas_comuns.intersection(idx)
        datas_comuns = datas_comuns.sort_values()

        df_ret_estrategia = pd.DataFrame(
            {a: ret_diario_estrategia[a].reindex(datas_comuns) for a in ativos_com_serie}
        )
        df_ret_buyhold = pd.DataFrame(
            {a: ret_diario_buyhold[a].reindex(datas_comuns) for a in ativos_com_serie}
        )
        pesos_alinhados = pesos_normalizados.reindex(df_ret_estrategia.columns)

        ret_portfolio_estrategia = (df_ret_estrategia * pesos_alinhados).sum(axis=1)
        ret_portfolio_buyhold = (df_ret_buyhold * pesos_alinhados).sum(axis=1)

        metricas_estrategia = _metricas_portfolio(ret_portfolio_estrategia, datas_comuns)
        metricas_buyhold = _metricas_portfolio(ret_portfolio_buyhold, datas_comuns)

        df_portfolio_holdout = pd.DataFrame(
            [
                {**metricas_estrategia, "Versao": "Estrategia_V4"},
                {**metricas_buyhold, "Versao": "Buy&Hold"},
            ]
        )
        cols_ordem = ["Versao"] + [c for c in df_portfolio_holdout.columns if c != "Versao"]
        df_portfolio_holdout = df_portfolio_holdout[cols_ordem]
        df_portfolio_holdout.to_csv("holdout_v4_portfolio_resultado.csv", index=False)

        print("\n" + "=" * 78)
        print(
            f"PORTFÓLIO COMPLETO NO HOLDOUT ({len(ativos_com_serie)} ativos, mesmos "
            "pesos de portfolio_v4_pesos.csv) - Estratégia vs Buy&Hold"
        )
        print("=" * 78)
        print(df_portfolio_holdout.to_string(index=False))
    except FileNotFoundError:
        print(f"\n[AVISO] {PESOS_CSV} não encontrado — sem combinação de portfólio no holdout.")

    try:
        df_v2 = pd.read_csv(V2_JANELAS_CSV)
        df_v2_alta = df_v2[df_v2["Lucro_BuyHold_TESTE_%"] > 0]
        print("\n" + "=" * 78)
        print("PRA COMPARAÇÃO: janelas do v2 (walk-forward antigo) com buy&hold")
        print("POSITIVO — captura de alta, período diferente do holdout acima")
        print("=" * 78)
        cols = [
            "Ativo",
            "Periodo_Teste",
            "Lucro_Estrategia_TESTE_%",
            "Lucro_BuyHold_TESTE_%",
            "DD_TESTE_%",
        ]
        print(df_v2_alta[cols].to_string(index=False))
    except FileNotFoundError:
        print(f"\n[AVISO] {V2_JANELAS_CSV} não encontrado — sem comparação com janelas de alta antigas.")

    print(
        "\nLEMBRETE: o holdout acima cobre um período predominantemente de "
        "baixa/lateral pra maior parte do portfólio — ele mede proteção de "
        "capital, não captura de alta. Ver comentário no topo deste arquivo."
    )


if __name__ == "__main__":
    if "--eu-confirmo-holdout-final" not in sys.argv:
        print(
            "Este script só deve rodar quando você pedir explicitamente.\n"
            "Ele é o único lugar do v4 que toca no período lacrado (holdout).\n\n"
            "Pra rodar de verdade:\n"
            "  python holdout_v4.py --eu-confirmo-holdout-final\n"
        )
        raise SystemExit(0)
    validar_holdout_final()
