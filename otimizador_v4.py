# -*- coding: utf-8 -*-
# OTIMIZADOR V4 - Calmar Ratio + Robustez + custos por liquidez + Deflated Sharpe
# ----------------------------------------------------------------------------
# Evolução do otimizador_v2_robustez.py. Não altera o v2, o v3, nem o Código
# Ômega original — arquivo novo e independente. Usa config_v4.py (compartilhado
# com portfolio_v4.py e holdout_v4.py).
#
# O que muda em relação ao v2:
#   - Entrada e stop: IGUAIS ao v2 (cruzamento de médias + filtro de
#     tendência; stop = ATR do candle anterior à entrada × atr_multiplicador;
#     saída por stop OU por cruzamento contrário — nada de trailing aqui).
#   - Ranking: em vez de "Lucro (%)", usa Calmar Ratio = retorno anualizado /
#     drawdown máximo (decimal). Combinação com drawdown 0 é descartada
#     (Calmar indefinido).
#   - Score de robustez por vizinhança (raio 3, pesos {1:1.0, 2:0.5, 3:0.25},
#     igual ao v2) é calculado sobre o Calmar, não sobre o lucro.
#   - Custos diferenciados por liquidez (ver config_v4.classificar_liquidez):
#     líquidos = taxa 0,1% + slippage 0,05%; menos líquidos = taxa 0,1% +
#     slippage 0,175%.
#   - Depois de escolher o parâmetro (por Calmar + robustez), calcula o
#     Deflated Sharpe Ratio (Bailey & López de Prado) do combo escolhido,
#     usando o número de combinações válidas testadas como N de tentativas.
#   - Roda SÓ no período de desenvolvimento de cada ativo (ver
#     config_v4.separar_periodos) — o período lacrado (holdout) nunca é
#     tocado aqui. Isso só acontece em holdout_v4.py, e só quando pedido
#     explicitamente.
#   - Usa os 22 ativos do STRATEGY_PORTFOLIO do frontend (config_v4.py), cada
#     um no seu timeframe de produção.
#
# Como rodar:
#   pip install python-binance pandas numpy pyarrow scipy python-dateutil
#   python otimizador_v4.py

import sys
import warnings
import numpy as np
import pandas as pd
from itertools import product
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
from scipy.stats import norm, skew, kurtosis

from config_v4 import (
    ATIVOS_PORTFOLIO_V4,
    CAPITAL_INICIAL,
    CANDLES_POR_DIA,
    carregar_dados,
    separar_periodos,
    classificar_liquidez,
)
from estrategia_core import calcular_sinais, simular_posicao

warnings.simplefilter(action="ignore", category=FutureWarning)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

PARAMS_TEST = {
    "media_rapida": [5, 7, 8, 9, 10, 12, 14, 15, 18, 21],
    "media_lenta": [20, 30, 40, 50, 80, 100, 120, 150, 200],
    "media_filtro": [50, 100, 150, 200, 250],
    "atr_periodo": [5, 7, 10, 14, 20, 25, 30],
    "atr_multiplicador": [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0],
}
PESOS_DISTANCIA = {1: 1.0, 2: 0.5, 3: 0.25}
MIN_TRADES = 5
MIN_CANDLES_DEV = 200  # abaixo disso, nem tenta otimizar (histórico curto demais)
MIN_DIAS_SHARPE = 10  # mínimo de dias de retorno diário pra calcular Sharpe


def executar_backtest_v4(
    df_arrays: dict,
    p: dict,
    inicio: int,
    fim: int,
    taxa: float,
    slippage: float,
    candles_por_dia: int,
    incluir_equity: bool = False,
) -> dict:
    """Entrada, stop e saída IGUAIS ao v2 (stop fixo OU cruzamento contrário).
    Adiciona slippage na execução e calcula retorno anualizado, Calmar e
    Sharpe (pra Deflated Sharpe depois)."""
    m_rapida = df_arrays[f"ma_{p['media_rapida']}"][inicio:fim]
    m_lenta = df_arrays[f"ma_{p['media_lenta']}"][inicio:fim]
    m_filtro = df_arrays[f"ma_f_{p['media_filtro']}"][inicio:fim]
    abertura = df_arrays["abertura"][inicio:fim]
    minima = df_arrays["minima"][inicio:fim]
    fechamento = df_arrays["fechamento"][inicio:fim]
    atr = df_arrays[f"atr_{p['atr_periodo']}"][inicio:fim]
    t_abert = df_arrays["t_abert"][inicio:fim]
    multi_atr = p["atr_multiplicador"]

    n = len(fechamento)
    vazio = {
        "retorno_total_pct": 0.0,
        "retorno_anualizado_pct": None,
        "drawdown_pct": 0.0,
        "num_trades": 0,
        "calmar": None,
        "sharpe": None,
        "equity": None,
    }
    if n < 5:
        return vazio

    # Sinais e timeline de posição vêm do MÓDULO CENTRAL (estrategia_core) — a
    # mesma lógica que o radar ao vivo usa. Aqui só aplicamos P&L (taxa +
    # slippage de saída) e marcamos a equity candle a candle.
    sinais_compra, sinais_venda = calcular_sinais(m_rapida, m_lenta, m_filtro, fechamento)
    eventos, _ = simular_posicao(abertura, minima, atr, sinais_compra, sinais_venda, multi_atr, slippage)

    capital = CAPITAL_INICIAL
    posicionado = False
    quantidade_ativo = 0.0
    num_trades = 0
    equity_curve = np.empty(n)
    equity_curve[0] = capital

    ev_idx = 0
    n_ev = len(eventos)
    for i in range(1, n):
        if ev_idx < n_ev and eventos[ev_idx][1] == i:
            tipo, _idx, preco_ev, _stop = eventos[ev_idx]
            if tipo == "entrada":
                # preco_ev = abertura[i] * (1 + slippage) (definido no core)
                quantidade_ativo = (capital / preco_ev) * (1 - taxa)
                posicionado = True
            else:  # saida
                preco_saida = preco_ev * (1 - slippage)
                capital = (quantidade_ativo * preco_saida) * (1 - taxa)
                posicionado = False
                num_trades += 1
            ev_idx += 1
        equity_curve[i] = capital if not posicionado else (quantidade_ativo * fechamento[i])

    capital_final = equity_curve[-1]
    retorno_total = (capital_final - CAPITAL_INICIAL) / CAPITAL_INICIAL

    running_max = np.maximum.accumulate(equity_curve)
    with np.errstate(invalid="ignore", divide="ignore"):
        dd_series = (running_max - equity_curve) / running_max
    max_dd = float(np.nanmax(dd_series)) if n > 0 else 0.0

    dias = (t_abert[-1] - t_abert[0]) / np.timedelta64(1, "D")
    anos = dias / 365.25 if dias > 0 else None
    retorno_anualizado = (
        (1 + retorno_total) ** (1 / anos) - 1 if anos and anos > 0 else None
    )

    calmar = None
    if max_dd > 0 and retorno_anualizado is not None:
        calmar = retorno_anualizado / max_dd

    sharpe = None
    if candles_por_dia and n >= candles_por_dia * MIN_DIAS_SHARPE:
        eq_diario = equity_curve[::candles_por_dia]
        ret_diario = np.diff(eq_diario) / eq_diario[:-1]
        ret_diario = ret_diario[np.isfinite(ret_diario)]
        if len(ret_diario) >= MIN_DIAS_SHARPE and np.std(ret_diario, ddof=1) > 0:
            sharpe = float(
                (np.mean(ret_diario) / np.std(ret_diario, ddof=1)) * np.sqrt(365.0)
            )

    return {
        "retorno_total_pct": round(retorno_total * 100.0, 2),
        "retorno_anualizado_pct": round(retorno_anualizado * 100.0, 2)
        if retorno_anualizado is not None
        else None,
        "drawdown_pct": round(max_dd * 100.0, 2),
        "num_trades": num_trades,
        "calmar": calmar,
        "sharpe": sharpe,
        "equity": equity_curve if incluir_equity else None,
    }


_WORKER_DF_FAST = None
_WORKER_N_DEV = None
_WORKER_TAXA = None
_WORKER_SLIPPAGE = None
_WORKER_CANDLES_DIA = None


def _init_worker(df_fast, n_dev, taxa, slippage, candles_dia):
    global _WORKER_DF_FAST, _WORKER_N_DEV, _WORKER_TAXA, _WORKER_SLIPPAGE, _WORKER_CANDLES_DIA
    _WORKER_DF_FAST = df_fast
    _WORKER_N_DEV = n_dev
    _WORKER_TAXA = taxa
    _WORKER_SLIPPAGE = slippage
    _WORKER_CANDLES_DIA = candles_dia


def _worker_treino(p: dict) -> dict:
    res = executar_backtest_v4(
        _WORKER_DF_FAST,
        p,
        0,
        _WORKER_N_DEV,
        _WORKER_TAXA,
        _WORKER_SLIPPAGE,
        _WORKER_CANDLES_DIA,
        incluir_equity=False,
    )
    return {"p": p, "res": res}


def calcular_score_robustez(d: pd.DataFrame, cols_idx: list, valor_col: str) -> np.ndarray:
    keys = list(zip(*(d[c] for c in cols_idx)))
    valores = d[valor_col].values
    lookup = dict(zip(keys, valores))
    scores = np.empty(len(d))
    for i, key in enumerate(keys):
        soma_pesos, soma_ponderada = 0.0, 0.0
        for dim in range(len(cols_idx)):
            for dist, peso in PESOS_DISTANCIA.items():
                for sinal in (-1, 1):
                    k2 = list(key)
                    k2[dim] += sinal * dist
                    k2 = tuple(k2)
                    if k2 in lookup:
                        soma_ponderada += lookup[k2] * peso
                        soma_pesos += peso
        scores[i] = (soma_ponderada / soma_pesos) if soma_pesos > 0 else np.nan
    return scores


def deflated_sharpe_ratio(sr_hat: float, sr_all: np.ndarray, T: int, skewness: float, kurt_pearson: float):
    """Deflated Sharpe Ratio (Bailey & López de Prado, 2014).
    sr_hat: Sharpe (anualizado) do combo escolhido.
    sr_all: Sharpe de todas as combinações válidas testadas (estima Var[SR]
            sob N tentativas — N = len(sr_all)).
    T: número de observações de retorno diário usadas pra estimar sr_hat.
    skewness / kurt_pearson: assimetria e curtose (Pearson, normal=3) dos
            retornos diários do combo escolhido.
    Retorna a probabilidade (0-1) de que o Sharpe verdadeiro supere o Sharpe
    máximo esperado por acaso, dado N tentativas (>0.5 sugere que o resultado
    não é só sorte de múltiplos testes)."""
    N = len(sr_all)
    if N < 2 or T < 10:
        return None
    var_sr = float(np.var(sr_all, ddof=1))
    if var_sr <= 0 or not np.isfinite(var_sr):
        return None
    euler_gamma = 0.5772156649015329
    z1 = norm.ppf(1 - 1.0 / N)
    z2 = norm.ppf(1 - 1.0 / (N * np.e))
    sr0 = np.sqrt(var_sr) * ((1 - euler_gamma) * z1 + euler_gamma * z2)
    denom_sq = 1 - skewness * sr_hat + ((kurt_pearson - 1) / 4.0) * sr_hat**2
    if denom_sq <= 0 or not np.isfinite(denom_sq):
        return None
    psr = norm.cdf(((sr_hat - sr0) * np.sqrt(T - 1)) / np.sqrt(denom_sq))
    return float(psr)


def main():
    comb_base = [
        dict(
            media_rapida=mr,
            media_lenta=ml,
            media_filtro=mf,
            atr_periodo=ap,
            atr_multiplicador=am,
        )
        for mr, ml, mf, ap, am in product(
            PARAMS_TEST["media_rapida"],
            PARAMS_TEST["media_lenta"],
            PARAMS_TEST["media_filtro"],
            PARAMS_TEST["atr_periodo"],
            PARAMS_TEST["atr_multiplicador"],
        )
        if ml > mr
    ]
    idx_cols = [
        "media_rapida_per",
        "media_lenta_per",
        "media_filtro_tendencia_per",
        "atr_periodo",
        "atr_multiplicador",
    ]
    n_cores = multiprocessing.cpu_count()
    print(f"Combinações por ativo: {len(comb_base)} | Núcleos: {n_cores}")

    resumo_linhas = []

    for ativo, interval_str in ATIVOS_PORTFOLIO_V4.items():
        print(f"\n{'=' * 70}\n>>> {ativo} ({interval_str}): carregando dados...")
        df = carregar_dados(ativo, interval_str)
        if df.empty:
            print(f"    [AVISO] sem dados para {ativo}, pulando.")
            continue

        info_liq = classificar_liquidez(ativo)
        periodos = separar_periodos(df["t_abert"])
        idx_dev_fim = periodos["idx_dev_fim"]
        if idx_dev_fim < MIN_CANDLES_DEV:
            print(
                f"    [AVISO] período de desenvolvimento muito curto "
                f"({idx_dev_fim} candles) para {ativo}, pulando."
            )
            continue

        df_dev = df.iloc[:idx_dev_fim].reset_index(drop=True)
        df_fast = {
            "abertura": df_dev["abertura"].values,
            "minima": df_dev["minima"].values,
            "fechamento": df_dev["fechamento"].values,
            "t_abert": df_dev["t_abert"].values,
        }
        for m in set(PARAMS_TEST["media_rapida"] + PARAMS_TEST["media_lenta"]):
            df_fast[f"ma_{m}"] = df_dev["fechamento"].rolling(m).mean().values
        for mf in set(PARAMS_TEST["media_filtro"]):
            df_fast[f"ma_f_{mf}"] = df_dev["fechamento"].rolling(mf).mean().values
        tr = pd.concat(
            [
                df_dev["maxima"] - df_dev["minima"],
                (df_dev["maxima"] - df_dev["fechamento"].shift()).abs(),
                (df_dev["minima"] - df_dev["fechamento"].shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        for pa in set(PARAMS_TEST["atr_periodo"]):
            df_fast[f"atr_{pa}"] = tr.rolling(pa).mean().values

        n_dev = len(df_fast["fechamento"])
        candles_dia = CANDLES_POR_DIA[interval_str]
        print(
            f"    Grupo: {info_liq['grupo']} (taxa {info_liq['taxa']*100:.2f}% + "
            f"slippage {info_liq['slippage']*100:.3f}%) | Desenvolvimento: "
            f"{periodos['dev_inicio'].date()} a {periodos['dev_fim'].date()} ({n_dev} candles)"
        )

        resultados = []
        with ProcessPoolExecutor(
            max_workers=n_cores,
            initializer=_init_worker,
            initargs=(df_fast, n_dev, info_liq["taxa"], info_liq["slippage"], candles_dia),
        ) as executor:
            for saida in executor.map(_worker_treino, comb_base, chunksize=200):
                p, res = saida["p"], saida["res"]
                if res["num_trades"] < MIN_TRADES:
                    continue
                if res["calmar"] is None or res["sharpe"] is None:
                    continue
                resultados.append(
                    {
                        "media_rapida_per": p["media_rapida"],
                        "media_lenta_per": p["media_lenta"],
                        "media_filtro_tendencia_per": p["media_filtro"],
                        "atr_periodo": p["atr_periodo"],
                        "atr_multiplicador": p["atr_multiplicador"],
                        "Retorno_Anualizado_%": res["retorno_anualizado_pct"],
                        "DD_%": res["drawdown_pct"],
                        "Num_Trades": res["num_trades"],
                        "Calmar": res["calmar"],
                        "Sharpe": res["sharpe"],
                    }
                )

        df_res = pd.DataFrame(resultados)
        if df_res.empty:
            print(
                "    Sem combinações válidas (Calmar indefinido, Sharpe "
                "indefinido, ou poucos trades) — pulando."
            )
            continue

        uniques = {c: sorted(df_res[c].unique()) for c in idx_cols}
        idx_maps = {c: {v: i for i, v in enumerate(uniques[c])} for c in idx_cols}
        for c in idx_cols:
            df_res[f"_idx_{c}"] = df_res[c].map(idx_maps[c])
        df_res["Score_Robustez"] = calcular_score_robustez(
            df_res, [f"_idx_{c}" for c in idx_cols], "Calmar"
        )
        df_res = df_res.drop(columns=[f"_idx_{c}" for c in idx_cols])
        df_res["Rank_Calmar"] = df_res["Calmar"].rank(ascending=False, method="min").astype(int)
        df_res["Rank_Robustez"] = (
            df_res["Score_Robustez"].rank(ascending=False, method="min").astype(int)
        )
        df_res = df_res.sort_values("Score_Robustez", ascending=False).reset_index(drop=True)

        nome_arquivo = f"otimizador_v4_{ativo}.csv"
        df_res.to_csv(nome_arquivo, index=False)
        print(
            f"    Salvo: {nome_arquivo} ({len(df_res)} combinações válidas de "
            f"{len(comb_base)} testadas)"
        )

        melhor = df_res.iloc[0]
        p_melhor = dict(
            media_rapida=int(melhor["media_rapida_per"]),
            media_lenta=int(melhor["media_lenta_per"]),
            media_filtro=int(melhor["media_filtro_tendencia_per"]),
            atr_periodo=int(melhor["atr_periodo"]),
            atr_multiplicador=float(melhor["atr_multiplicador"]),
        )

        res_detalhado = executar_backtest_v4(
            df_fast,
            p_melhor,
            0,
            n_dev,
            info_liq["taxa"],
            info_liq["slippage"],
            candles_dia,
            incluir_equity=True,
        )
        eq = res_detalhado["equity"]
        eq_diario = eq[::candles_dia]
        ret_diario = np.diff(eq_diario) / eq_diario[:-1]
        ret_diario = ret_diario[np.isfinite(ret_diario)]
        T = len(ret_diario)
        skewness = float(skew(ret_diario, bias=False)) if T > 2 else 0.0
        kurt_pearson = float(kurtosis(ret_diario, fisher=False, bias=False)) if T > 3 else 3.0

        sr_all = df_res["Sharpe"].values
        dsr = deflated_sharpe_ratio(melhor["Sharpe"], sr_all, T, skewness, kurt_pearson)

        resumo_linhas.append(
            {
                "Ativo": ativo,
                "Interval": interval_str,
                "Grupo_Liquidez": info_liq["grupo"],
                "Periodo_Dev_Inicio": periodos["dev_inicio"].date(),
                "Periodo_Dev_Fim": periodos["dev_fim"].date(),
                "media_rapida_per": p_melhor["media_rapida"],
                "media_lenta_per": p_melhor["media_lenta"],
                "media_filtro_tendencia_per": p_melhor["media_filtro"],
                "atr_periodo": p_melhor["atr_periodo"],
                "atr_multiplicador": p_melhor["atr_multiplicador"],
                "Retorno_Anualizado_%": melhor["Retorno_Anualizado_%"],
                "DD_%": melhor["DD_%"],
                "Calmar": round(float(melhor["Calmar"]), 3),
                "Sharpe": round(float(melhor["Sharpe"]), 3),
                "Num_Trades": int(melhor["Num_Trades"]),
                "N_Combinacoes_Validas": len(df_res),
                "N_Combinacoes_Testadas": len(comb_base),
                "DSR": round(dsr, 4) if dsr is not None else None,
                "DSR_%": round(dsr * 100, 2) if dsr is not None else None,
            }
        )
        dsr_str = f"{dsr*100:.1f}%" if dsr is not None else "indefinido"
        print(
            f"    Melhor (Calmar+robustez): Calmar={melhor['Calmar']:.2f} | "
            f"Sharpe={melhor['Sharpe']:.2f} | DSR={dsr_str}"
        )

    df_resumo = pd.DataFrame(resumo_linhas)
    df_resumo.to_csv("otimizador_v4_RESUMO_ATIVOS.csv", index=False)
    print(f"\n✅ Salvo: otimizador_v4_RESUMO_ATIVOS.csv ({len(df_resumo)} ativos)\n")
    print(df_resumo.to_string(index=False))


if __name__ == "__main__":
    main()
