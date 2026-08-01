# -*- coding: utf-8 -*-
# DAYTRADE - GRADE DE MULTIPLICADORES (volume_spike_anterior, 1h, BTC/ETH/BNB)
# ----------------------------------------------------------------------------
# Pedido explicito do usuario: testar variacoes do multiplicador de spike de
# volume (vs candle anterior, nao media movel), com valores INDEPENDENTES pra
# compra e venda, e ver se alguma combinacao da algo mais concreto que o
# resultado de amostra minuscula (5-10 trades) visto com 10x fixo.
#
# CUIDADO METODOLOGICO (por isso este script reporta a GRADE INTEIRA, nao so
# o "melhor" combo): testar N combinacoes e escolher a que teve melhor
# resultado no MESMO periodo e exatamente o padrao de overfitting que o
# projeto inteiro (Score de Robustez do v2, DSR do v4) foi desenhado pra
# evitar. Reportamos:
#   1) a grade completa (nao so o topo),
#   2) contagem de trades por celula (celulas com poucos trades sao ruido,
#      nao resultado),
#   3) uma "pontuacao de robustez" por vizinhanca (media ponderada dos
#      vizinhos na grade) -- mesmo principio de calcular_score_robustez em
#      otimizador_v2_robustez.py/otimizador_v4.py (reimplementado aqui, nao
#      importado, pra manter o desacoplamento entre linhagens).
#
# So periodo de DEV -- holdout continua travado.

import numpy as np
import pandas as pd

from daytrade_walkforward import rodar_combo, tag_regime_tendencia

ATIVOS_TESTE = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
TIMEFRAME_TESTE = "1h"
MULTIPLICADORES = [2, 3, 4, 5, 6, 8, 10, 12, 15, 20]
MIN_TRADES_CONFIAVEL = 20  # abaixo disso, tratamos como "amostra insuficiente"

# monkeypatch temporario da receita pra injetar os multiplicadores do grid --
# ver nota em rodar_combo_grid() abaixo
from daytrade_walkforward import RECEITA_DAYTRADE


def rodar_combo_grid(ativo: str, mult_compra: float, mult_venda: float):
    """Roda UMA celula da grade (mult_compra x mult_venda) num ativo, no
    timeframe 1h, periodo de dev. Reusa rodar_combo() sobrescrevendo
    temporariamente os multiplicadores na RECEITA_DAYTRADE (mais simples que
    duplicar toda a logica de rodar_combo so pra variar 2 numeros)."""
    receita_original = dict(RECEITA_DAYTRADE["volume_spike_anterior"])
    RECEITA_DAYTRADE["volume_spike_anterior"]["multiplicador_compra"] = mult_compra
    RECEITA_DAYTRADE["volume_spike_anterior"]["multiplicador_venda"] = mult_venda
    try:
        return rodar_combo(ativo, TIMEFRAME_TESTE, "volume_spike_anterior", usar_volume=False)
    finally:
        RECEITA_DAYTRADE["volume_spike_anterior"] = receita_original


def calcular_score_robustez_grid(df: pd.DataFrame, valor_col: str) -> np.ndarray:
    """Mesma ideia de calcular_score_robustez do v2/v4 (vizinhanca ponderada),
    reimplementada pra grade 2D (mult_compra, mult_venda). Pesos decrescentes
    por distancia (raio 1 e 2), media dos vizinhos existentes na grade."""
    lookup = {(row["mult_compra"], row["mult_venda"]): row[valor_col] for _, row in df.iterrows()}
    pesos_distancia = {1: 1.0, 2: 0.5}
    scores = np.empty(len(df))
    for i, row in df.iterrows():
        mc, mv = row["mult_compra"], row["mult_venda"]
        idx_mc = MULTIPLICADORES.index(mc)
        idx_mv = MULTIPLICADORES.index(mv)
        soma_pesos, soma_ponderada = 0.0, 0.0
        for dist, peso in pesos_distancia.items():
            for sinal_c in (-1, 1):
                for sinal_v in (-1, 1):
                    novo_idx_c = idx_mc + sinal_c * dist
                    novo_idx_v = idx_mv + sinal_v * dist
                    if 0 <= novo_idx_c < len(MULTIPLICADORES) and 0 <= novo_idx_v < len(MULTIPLICADORES):
                        key = (MULTIPLICADORES[novo_idx_c], MULTIPLICADORES[novo_idx_v])
                        if key in lookup and lookup[key] is not None:
                            soma_ponderada += lookup[key] * peso
                            soma_pesos += peso
        scores[i] = (soma_ponderada / soma_pesos) if soma_pesos > 0 else np.nan
    return scores


def main():
    print("=" * 90)
    print(f"GRADE DE MULTIPLICADORES -- volume_spike_anterior, {TIMEFRAME_TESTE}, "
          f"{', '.join(ATIVOS_TESTE)}")
    print(f"({len(MULTIPLICADORES)}x{len(MULTIPLICADORES)} = {len(MULTIPLICADORES)**2} combinacoes "
          f"x {len(ATIVOS_TESTE)} ativos = {len(MULTIPLICADORES)**2 * len(ATIVOS_TESTE)} backtests)")
    print("=" * 90)

    linhas = []
    for mult_compra in MULTIPLICADORES:
        for mult_venda in MULTIPLICADORES:
            todos_trades = []
            calmars = []
            for ativo in ATIVOS_TESTE:
                r = rodar_combo_grid(ativo, mult_compra, mult_venda)
                if r is None:
                    continue
                todos_trades.extend(r["trades"])
                if r["met_base"]["calmar"] is not None:
                    calmars.append(r["met_base"]["calmar"])

            n_trades = len(todos_trades)
            if n_trades == 0:
                net_pct_medio = None
                win_rate = None
            else:
                net_pcts = np.array([t["net_pct"] for t in todos_trades])
                net_pct_medio = float(np.mean(net_pcts))
                win_rate = float(np.mean(net_pcts > 0)) * 100

            linhas.append({
                "mult_compra": mult_compra, "mult_venda": mult_venda,
                "n_trades": n_trades,
                "net_pct_medio": round(net_pct_medio, 4) if net_pct_medio is not None else None,
                "win_rate_liquido_pct": round(win_rate, 1) if win_rate is not None else None,
                "calmar_medio": round(float(np.mean(calmars)), 3) if calmars else None,
                "confiavel": n_trades >= MIN_TRADES_CONFIAVEL,
            })

    df = pd.DataFrame(linhas)
    df["score_robustez_net_pct"] = calcular_score_robustez_grid(df, "net_pct_medio")
    df.to_csv("daytrade_volume_puro_grid_resultado.csv", index=False)

    print("\n--- Heatmap: net_pct_medio por (mult_compra, mult_venda) ---")
    print("(celulas com < 20 trades marcadas com * -- pouca confianca)")
    piv = df.copy()
    piv["net_pct_str"] = piv.apply(
        lambda r: f"{r['net_pct_medio']:+.2f}{'*' if not r['confiavel'] else ' '}" if r["net_pct_medio"] is not None else "  n/a ",
        axis=1,
    )
    tabela = piv.pivot(index="mult_compra", columns="mult_venda", values="net_pct_str")
    print(tabela.to_string())

    print("\n--- Contagem de trades por celula ---")
    tabela_n = piv.pivot(index="mult_compra", columns="mult_venda", values="n_trades")
    print(tabela_n.to_string())

    df_confiavel = df[df["confiavel"]].copy()
    print(f"\n{len(df_confiavel)} de {len(df)} celulas tem >= {MIN_TRADES_CONFIAVEL} trades (amostra minima).")

    if not df_confiavel.empty:
        print("\n--- Top 5 por net_pct_medio (SO celulas confiaveis) ---")
        top5 = df_confiavel.sort_values("net_pct_medio", ascending=False).head(5)
        print(top5[["mult_compra", "mult_venda", "n_trades", "net_pct_medio", "win_rate_liquido_pct",
                     "calmar_medio", "score_robustez_net_pct"]].to_string(index=False))

        print("\n--- Top 5 por SCORE DE ROBUSTEZ (media da vizinhanca -- mais confiavel que o pico isolado) ---")
        top5_rob = df_confiavel.dropna(subset=["score_robustez_net_pct"]).sort_values(
            "score_robustez_net_pct", ascending=False
        ).head(5)
        print(top5_rob[["mult_compra", "mult_venda", "n_trades", "net_pct_medio",
                         "score_robustez_net_pct", "calmar_medio"]].to_string(index=False))
    else:
        print("\nNenhuma celula com amostra minima confiavel -- grade inteira e ruido estatistico.")

    n_positivos = (df_confiavel["net_pct_medio"] > 0).sum() if not df_confiavel.empty else 0
    print(f"\n{n_positivos} de {len(df_confiavel)} celulas confiaveis tem net_pct_medio positivo "
          f"({n_positivos/len(df_confiavel)*100:.0f}%)." if not df_confiavel.empty else "")
    print(f"\nSalvo: daytrade_volume_puro_grid_resultado.csv ({len(df)} linhas)")
    print(
        "\nAVISO: mesmo o 'melhor' combo aqui foi escolhido DEPOIS de ver "
        f"{len(MULTIPLICADORES)**2} resultados -- isso NAO e validacao, e busca. "
        "Se algum combo parecer bom, o proximo passo honesto seria testa-lo "
        "sozinho, congelado, num walk-forward + holdout novos -- nao usar este "
        "numero como se already validado."
    )


if __name__ == "__main__":
    main()
