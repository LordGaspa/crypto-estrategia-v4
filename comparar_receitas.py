# -*- coding: utf-8 -*-
# COMPARAR RECEITAS - head-to-head entre receitas candidatas (mesmo motor)
# ----------------------------------------------------------------------------
# Reutiliza o backtest oficial via estrategia_ouro_v5. Nao altera nada.
# Testa varias receitas por grupo e mostra o portfolio lado a lado em:
#   - DEV veteranas (janela longa ~5 anos, a mais confiavel)
#   - HOLDOUT (12 meses lacrados, todos os 22)

import sys
import warnings
import pandas as pd

from config_v4 import ATIVOS_PORTFOLIO_V4
from estrategia_ouro_v5 import backtest_ativo, metricas_portfolio, grupo_do_ativo

warnings.simplefilter(action="ignore", category=FutureWarning)
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# receitas candidatas: cada uma tem params por grupo (veterana / nova)
RECEITAS = {
    "OURO (moda top-100 Calmar)": {
        "veterana": dict(media_rapida=5, media_lenta=80, media_filtro=50, atr_periodo=5, atr_multiplicador=6.0),
        "nova":     dict(media_rapida=10, media_lenta=80, media_filtro=100, atr_periodo=25, atr_multiplicador=1.0),
    },
    "ROBUSTA (5% por Robustez)": {
        "veterana": dict(media_rapida=5, media_lenta=100, media_filtro=50, atr_periodo=7, atr_multiplicador=6.0),
        "nova":     dict(media_rapida=12, media_lenta=30, media_filtro=100, atr_periodo=20, atr_multiplicador=5.0),
    },
    # hibrida: ancoras robustas (filtro + atr_mult largo dos dois grupos) + lenta 80 (comum)
    "HIBRIDA (ancoras robustas)": {
        "veterana": dict(media_rapida=5, media_lenta=80, media_filtro=50, atr_periodo=7, atr_multiplicador=6.0),
        "nova":     dict(media_rapida=12, media_lenta=80, media_filtro=100, atr_periodo=20, atr_multiplicador=5.0),
    },
}


def roda(nome, receita, pesos):
    ret_dev, ret_hold = {}, {}
    ret_dev_bh, ret_hold_bh = {}, {}
    for ativo, interval in ATIVOS_PORTFOLIO_V4.items():
        g = grupo_do_ativo(ativo)
        res = backtest_ativo(ativo, interval, receita[g])
        if res is None:
            continue
        if res["dev"]:
            ret_dev[ativo] = res["dev"]["ret_diario"]
            ret_dev_bh[ativo] = res["dev"]["ret_diario_bh"]
        if res["holdout"]:
            ret_hold[ativo] = res["holdout"]["ret_diario"]
            ret_hold_bh[ativo] = res["holdout"]["ret_diario_bh"]

    def so_vet(d):
        return {a: s for a, s in d.items() if grupo_do_ativo(a) == "veterana"}

    m_dev = metricas_portfolio(so_vet(ret_dev), pesos)
    m_hold = metricas_portfolio(ret_hold, pesos)
    return m_dev, m_hold, ret_dev_bh, ret_hold_bh


def main():
    df_pesos = pd.read_csv("portfolio_v4_pesos.csv")
    pesos = df_pesos.set_index("Ativo")["Peso_Portfolio_%"] / 100.0

    print("=" * 96)
    print("HEAD-TO-HEAD DE RECEITAS  (portfolio, mesmo motor e mesmos pesos)")
    print("=" * 96)

    def linha(tag, m):
        if m is None:
            return f"  {tag:34} (sem dados)"
        return (f"  {tag:34} ret {str(m['retorno_total_%']):>9}%  anual {str(m['retorno_anual_%']):>7}%  "
                f"DD {str(m['dd_%']):>6}%  Calmar {str(m['calmar']):>7}  ({m['n_dias']}d)")

    bh_dev_ref = bh_hold_ref = None
    resultados = {}
    for nome, receita in RECEITAS.items():
        m_dev, m_hold, ret_dev_bh, ret_hold_bh = roda(nome, receita, pesos)
        resultados[nome] = (m_dev, m_hold)
        if bh_dev_ref is None:
            def so_vet(d):
                return {a: s for a, s in d.items() if grupo_do_ativo(a) == "veterana"}
            bh_dev_ref = metricas_portfolio(so_vet(ret_dev_bh), pesos)
            bh_hold_ref = metricas_portfolio(ret_hold_bh, pesos)

    print("\n>> DEV - SO VETERANAS (janela comum ~5 anos) - a comparacao mais confiavel:\n")
    for nome, (m_dev, _) in resultados.items():
        print(linha(nome, m_dev))
    print(linha("BUY & HOLD (referencia)", bh_dev_ref))

    print("\n>> HOLDOUT - 22 ativos (12 meses lacrados, mercado de baixa):\n")
    for nome, (_, m_hold) in resultados.items():
        print(linha(nome, m_hold))
    print(linha("BUY & HOLD (referencia)", bh_hold_ref))

    print("\n  Calmar mais alto no DEV veteranas = melhor retorno por unidade de risco (o alvo).")
    print("  No holdout, 'menos negativo' = melhor protecao de capital.")


if __name__ == "__main__":
    main()
