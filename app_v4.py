# -*- coding: utf-8 -*-
# APP V4 - Radar de sinais + portfólio (Streamlit)
# ----------------------------------------------------------------------------
# App novo e independente. NÃO altera nada do Código Ômega original (backend/
# frontend), do v2, do v3, nem dos scripts de otimização/validação do v4
# (config_v4.py, otimizador_v4.py, portfolio_v4.py, holdout_v4.py) — só LÊ os
# CSVs que eles já geraram e reusa executar_backtest_v4 pra desenhar os gráficos
# de capital (com `incluir_trades=True` pro diário de trades — flag só de
# leitura, não muda nenhum número do backtest).
#
# O cálculo do "sinal atual" (ALTA/BAIXA) e a data desse sinal são feitos por
# uma função local deste arquivo (estado_atual_posicao) que espelha a MESMA
# lógica de entrada/saída do v4 (cruzamento de médias + filtro de tendência;
# stop ATR fixo OU cruzamento contrário — NÃO o trailing stop do v3, que foi
# descartado) — ela não reaproveita o motor de backtest porque precisamos
# saber se a posição está aberta AGORA, no candle mais recente, e desde
# quando, o que otimizador_v4.py não expõe.
#
# Esta revisão só mexe em apresentação (tema escuro, cards em HTML/CSS,
# gráficos de área semi-transparente, ordenação por data do sinal) — nenhuma
# lógica de cálculo mudou.
#
# Pré-requisitos (rodar antes de abrir o app):
#   python otimizador_v4.py
#   python portfolio_v4.py
#   python holdout_v4.py --eu-confirmo-holdout-final   (opcional — o app
#       funciona sem isso, só não mostra a seção de holdout)
#
# Como rodar:
#   streamlit run app_v4.py

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import altair as alt
import streamlit as st

from config_v4 import (
    CANDLES_POR_DIA,
    CAPITAL_INICIAL,
    ATIVOS_PORTFOLIO_V4,
    DATA_CORTE_HOLDOUT,
    RECEITA_ROBUSTA,
    grupo_ouro,
    carregar_dados,
    separar_periodos,
    classificar_liquidez,
)
from otimizador_v4 import executar_backtest_v4
from estrategia_core import estado_posicao_atual
import sombra_analise

st.set_page_config(page_title="Estratégia V4 - Radar & Portfólio", layout="wide", page_icon="📡")
alt.data_transformers.disable_max_rows()  # séries de anos em 4h passam do limite padrão (5000 linhas)

RESUMO_CSV = "otimizador_v4_RESUMO_ATIVOS.csv"  # manifesto: lista de ativos, interval, grupo

# Significância estatística por ativo, medida sobre a RECEITA_ROBUSTA — a
# estratégia que o app realmente executa (walkforward_robusta_v4.py, bootstrap
# do Sharpe em 2000 reamostragens).
#
# ANTES o app usava o Deflated Sharpe Ratio (DSR) do otimizador_v4 pra este
# aviso, e estava ERRADO por dois motivos:
#   1. O DSR é calculado para os parâmetros OTIMIZADOS POR ATIVO — que o app
#      não usa desde a migração para a RECEITA_ROBUSTA. Era a estatística de
#      uma estratégia diferente da que gera os sinais na tela.
#   2. O DSR penaliza por múltiplas tentativas (28.035 combinações do grid).
#      Essa penalidade NÃO se aplica à receita robusta, que é fixa por grupo e
#      nunca foi escolhida olhando o ativo — não houve busca, logo não há o
#      viés que o DSR corrige.
# Resultado prático: SOL aparecia como "sem significância (DSR 0,0%)" quando o
# bootstrap da receita que realmente roda dá p = 0,000 (altamente significativo).
BOOTSTRAP_CSV = "walkforward_robusta_v4_resumo.csv"
P_VALOR_LIMITE = 0.05   # p >= isso: não confirmado estatisticamente
MIN_JANELAS_CONFIAVEL = 4  # menos que isso, a amostra é curta demais pra concluir
# (os antigos CSVs de portfólio/pesos não são mais lidos: tudo é calculado ao
#  vivo pela receita robusta em computar_portfolio_robusto)

COR_ALTA = "#22c55e"
COR_BAIXA = "#ef4444"
COR_DSR_ALERTA = "#f59e0b"
COR_LIQUIDO = "#3b82f6"
COR_MENOS_LIQUIDO = "#a855f7"
COR_ESTRATEGIA = "#34d399"
COR_BUYHOLD = "#fbbf24"

# RECEITA_ROBUSTA e grupo_ouro agora vivem no config_v4 (fonte única da verdade,
# importados acima) — antes estavam duplicados aqui.

# Candles e registros vêm em UTC; o app EXIBE em horário de Brasília. Offset
# fixo: o Brasil não tem horário de verão desde 2019.
FUSO_BRT = pd.Timedelta(hours=-3)

# O app recarrega preços da Binance se o cache tiver mais que isto (o monitor
# horário também atualiza o cache em hh:05, então muitas vezes já está fresco).
MAX_IDADE_PRECO_HORAS = 0.25

# Acima disto de atraso no último candle, o app avisa que os dados estão velhos
# (caso típico: Streamlit Cloud, onde a Binance bloqueia por região e só sobra
# o cache que foi pro Git).
LIMITE_DADOS_VELHOS_HORAS = 6


def brt(ts):
    """Timestamp UTC (naive) -> horário de Brasília (naive), só pra exibição."""
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts) + FUSO_BRT

# ----------------------------------------------------------------------------
# CSS - tema escuro tipo dashboard (o base=dark já vem de .streamlit/config.toml)
# ----------------------------------------------------------------------------
st.markdown(
    """
    <style>
    .block-container { padding-top: 2rem; padding-bottom: 3rem; }
    div[data-testid="stMetric"] {
        background: #161b26;
        border: 1px solid #262d3d;
        border-radius: 10px;
        padding: 10px 14px;
    }
    .sinal-card {
        border-radius: 12px;
        padding: 14px 16px 10px 16px;
        margin-bottom: 6px;
    }
    .sinal-badge {
        display: inline-block;
        padding: 3px 12px;
        border-radius: 999px;
        font-weight: 700;
        font-size: 0.82rem;
        letter-spacing: 0.02em;
    }
    .chip {
        display: inline-block;
        padding: 2px 9px;
        border-radius: 999px;
        font-size: 0.74rem;
        font-weight: 600;
        margin-right: 4px;
    }
    .dsr-aviso {
        margin-top: 8px;
        padding: 5px 9px;
        border-radius: 7px;
        font-size: 0.78rem;
        font-weight: 600;
    }
    .preco-atual {
        font-size: 1.25rem;
        font-weight: 700;
        color: #f8fafc;
        font-variant-numeric: tabular-nums;
    }
    .variacao-chip {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 999px;
        font-size: 0.78rem;
        font-weight: 700;
        margin-left: 8px;
        font-variant-numeric: tabular-nums;
        vertical-align: middle;
    }
    .meta-sinal {
        font-size: 0.76rem;
        color: #94a3b8;
        margin-top: 2px;
    }
    .stop-row {
        margin-top: 7px;
        font-size: 0.8rem;
        color: #e2e8f0;
        font-variant-numeric: tabular-nums;
    }
    .stats-row {
        margin-top: 10px;
        font-size: 0.85rem;
        color: #cbd5e1;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ----------------------------------------------------------------------------
# CARREGAMENTO DE CSVs JÁ GERADOS (não roda nenhuma otimização/validação nova)
# ----------------------------------------------------------------------------
@st.cache_data(ttl=1800)
def carregar_csv(caminho):
    if not os.path.exists(caminho):
        return None
    return pd.read_csv(caminho)


@st.cache_data
def carregar_resumo_filter():
    """Métricas do re-backtest real com sizes dinâmicos por regime BTC."""
    try:
        return pd.read_csv("backtest_btc_filter_v4_resumo_ponderado.csv").set_index("Modo")
    except FileNotFoundError:
        return None


@st.cache_data
def carregar_fronteira():
    """Tabela comparativa Baseline / Vol-target / BTC-Filtrado / BTC-Agressivo."""
    try:
        df = pd.read_csv("fronteira_modos_v4.csv")
        return df[df["Estrategia"] != "BTC-Defensivo"].reset_index(drop=True)
    except FileNotFoundError:
        return None


@st.cache_data
def carregar_bootstrap():
    """Significância por ativo da RECEITA_ROBUSTA (a estratégia que roda).
    Indexado por Ativo; colunas relevantes: Sharpe_boot, P_value_boot, N_Janelas."""
    try:
        return pd.read_csv(BOOTSTRAP_CSV).set_index("Ativo")
    except FileNotFoundError:
        return None


def _avaliar_significancia(boot_df, ativo: str) -> dict | None:
    """Traduz o bootstrap num veredito legível. Devolve None se não há dado.

    Três estados possíveis, nesta ordem de prioridade:
      - amostra_curta: menos de MIN_JANELAS_CONFIAVEL janelas anuais -> não dá
        pra concluir nada, nem a favor nem contra (é o caso dos ativos
        listados há pouco tempo);
      - nao_confirmado: janelas suficientes, mas p >= 0,05;
      - confirmado: p < 0,05.
    """
    if boot_df is None or ativo not in boot_df.index:
        return None
    r = boot_df.loc[ativo]
    p = r.get("P_value_boot")
    sharpe = r.get("Sharpe_boot")
    n_jan = r.get("N_Janelas")
    if pd.isna(p):
        return None
    n_jan = int(n_jan) if pd.notna(n_jan) else 0
    if n_jan < MIN_JANELAS_CONFIAVEL:
        estado = "amostra_curta"
    elif p < P_VALOR_LIMITE:
        estado = "confirmado"
    else:
        estado = "nao_confirmado"
    return {"estado": estado, "p": float(p), "n_janelas": n_jan,
            "sharpe": float(sharpe) if pd.notna(sharpe) else None}


@st.cache_data
def carregar_modos_margem():
    """Checagem de margem por modo (riqueza_terminal_v6_exposicao.py): quanto do
    tempo a exposição agregada do portfólio passa de 100% do capital, e o
    resultado Spot-compatível (com teto) vs o resultado com margem.

    Existe porque o modo Agressivo (1.5× em BULL) NÃO é executável em Binance
    Spot puro — ele exige empréstimo em ~28% do tempo, e os números "históricos"
    dele embutem essa alavancagem. Sem este aviso o app induziria ao erro."""
    try:
        return pd.read_csv("modos_margem_v6.csv").set_index("Modo")
    except FileNotFoundError:
        return None


# ----------------------------------------------------------------------------
# SINAL ATUAL - mesma lógica de entrada/saída do v4 (cruzamento + filtro +
# stop ATR fixo OU cruzamento contrário), calculada localmente pra saber se a
# posição está aberta no candle mais recente, e desde quando (data do último
# cruzamento que mudou o estado — usada só pra ordenar/exibir os cards).
# ----------------------------------------------------------------------------
def estado_atual_posicao(df_fast: dict, params: dict) -> dict:
    """Wrapper fino sobre estrategia_core.estado_posicao_atual — a MESMA lógica
    de entrada/saída/stop que o backtest (executar_backtest_v4) usa, agora numa
    fonte única. O radar não pode mais divergir do que a estratégia testou.
    Só embrulha data_mudanca em pd.Timestamp para o resto do app formatar."""
    d = estado_posicao_atual(df_fast, params, slippage=0.0)
    if d["data_mudanca"] is not None:
        d["data_mudanca"] = pd.Timestamp(d["data_mudanca"])
    return d


def montar_df_fast(df: pd.DataFrame, params: dict) -> dict:
    df_fast = {
        "abertura": df["abertura"].values,
        "minima": df["minima"].values,
        "fechamento": df["fechamento"].values,
        "t_abert": df["t_abert"].values,
    }
    df_fast[f"ma_{params['media_rapida']}"] = df["fechamento"].rolling(params["media_rapida"]).mean().values
    df_fast[f"ma_{params['media_lenta']}"] = df["fechamento"].rolling(params["media_lenta"]).mean().values
    df_fast[f"ma_f_{params['media_filtro']}"] = df["fechamento"].rolling(params["media_filtro"]).mean().values
    tr = pd.concat(
        [
            df["maxima"] - df["minima"],
            (df["maxima"] - df["fechamento"].shift()).abs(),
            (df["minima"] - df["fechamento"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    df_fast[f"atr_{params['atr_periodo']}"] = tr.rolling(params["atr_periodo"]).mean().values
    return df_fast


INTERVALO_TD = {"4h": pd.Timedelta(hours=4), "6h": pd.Timedelta(hours=6),
                "8h": pd.Timedelta(hours=8), "12h": pd.Timedelta(hours=12),
                "1d": pd.Timedelta(days=1)}


@st.cache_data(ttl=300, show_spinner="Atualizando preços na Binance...")
def atualizar_precos_ao_vivo() -> dict:
    """Traz os 22 caches pro candle de agora (em paralelo: em série levava ~25s)
    e mede o atraso de cada ativo. Depois disto, todo o resto do app lê o cache
    já fresco. `versao` muda a cada atualização e invalida os caches derivados
    (sinais, portfólio, curvas) — sem isso eles segurariam o preço velho."""
    def _um(item):
        ativo, itv = item
        try:
            df = carregar_dados(ativo, itv, max_idade_horas=MAX_IDADE_PRECO_HORAS)
            return ativo, pd.Timestamp(df["t_abert"].iloc[-1]) if df is not None and len(df) else None
        except Exception:
            return ativo, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        ultimos = dict(pool.map(_um, ATIVOS_PORTFOLIO_V4.items()))

    agora = pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None)
    atrasos = {}
    for ativo, ult in ultimos.items():
        if ult is None:
            atrasos[ativo] = None
            continue
        # o último candle é o que está ABERTO agora: atraso real = quanto o
        # candle esperado agora passou do último que temos
        atrasos[ativo] = max((agora - ult - INTERVALO_TD[ATIVOS_PORTFOLIO_V4[ativo]]).total_seconds() / 3600, 0.0)
    validos = [a for a in atrasos.values() if a is not None]
    return {
        "versao": agora.isoformat(timespec="seconds"),
        "atualizado_em": agora,
        "pior_atraso_h": max(validos) if validos else None,
        "ultimo_candle": max((u for u in ultimos.values() if u is not None), default=None),
        "sem_dados": [a for a, u in ultimos.items() if u is None],
    }


@st.cache_data(ttl=1800)
def calcular_sinal(ativo: str, interval_str: str, params: dict, versao: str = "") -> dict:
    df = carregar_dados(ativo, interval_str)
    if df.empty:
        return {"posicionado": False, "preco_entrada": None, "stop_atual": None, "preco_atual": None, "preco_sinal": None, "data_mudanca": None}
    df_fast = montar_df_fast(df, params)
    return estado_atual_posicao(df_fast, params)


@st.cache_data(ttl=1800)
def curva_capital(ativo: str, interval_str: str, params: dict, taxa: float, slippage: float,
                  versao: str = ""):
    """Devolve (df_dev, df_holdout) com colunas Estrategia/Buy&Hold indexadas
    por data, cada um recomeçando em 1000 no início do seu próprio período —
    exatamente como otimizador_v4.py e holdout_v4.py calculam oficialmente.
    O corte é o REAL (DATA_CORTE_HOLDOUT): o holdout cresce com o tempo."""
    df = carregar_dados(ativo, interval_str)
    if df.empty:
        return None, None
    periodos = separar_periodos(df["t_abert"], corte=DATA_CORTE_HOLDOUT)
    idx_dev_fim = periodos["idx_dev_fim"]
    candles_dia = CANDLES_POR_DIA[interval_str]

    resultados = {}
    for nome, fatia in (("dev", df.iloc[:idx_dev_fim]), ("holdout", df.iloc[idx_dev_fim:])):
        fatia = fatia.reset_index(drop=True)
        if len(fatia) < 10:
            resultados[nome] = None
            continue
        df_fast = montar_df_fast(fatia, params)
        res = executar_backtest_v4(
            df_fast, params, 0, len(df_fast["fechamento"]), taxa, slippage, candles_dia, incluir_equity=True
        )
        datas = pd.to_datetime(df_fast["t_abert"])
        equity_estrategia = pd.Series(res["equity"], index=datas)
        preco = fatia["fechamento"].values
        equity_buyhold = pd.Series(preco / preco[0] * 1000.0, index=datas)
        df_curva = pd.DataFrame({"Estrategia": equity_estrategia, "Buy&Hold": equity_buyhold})
        # resample pra granularidade diária só pra exibição no gráfico (mais
        # legível e evita o limite de linhas do Altair em séries de anos em 4h)
        resultados[nome] = df_curva.resample("1D").last().dropna()

    return resultados.get("dev"), resultados.get("holdout")


@st.cache_data(ttl=1800)
def curva_capital_intervalo(ativo: str, interval_str: str, params: dict, taxa: float,
                            slippage: float, data_inicio, data_fim, versao: str = ""):
    """Curva de capital + métricas recalculadas SOMENTE na janela [data_inicio,
    data_fim] escolhida no calendário. Os indicadores são calculados sobre o
    histórico COMPLETO (warm-up), e só a simulação/capital recomeça em 1000 no
    início da janela — ou seja: "se você tivesse começado a operar a estratégia
    nesta data, com os indicadores já aquecidos, o que teria acontecido até a
    data final?". Buy&Hold também recomeça em 1000 no início da janela."""
    df = carregar_dados(ativo, interval_str)
    if df.empty:
        return None, None
    candles_dia = CANDLES_POR_DIA[interval_str]
    df_fast = montar_df_fast(df, params)  # warm-up no histórico inteiro
    datas = pd.to_datetime(df["t_abert"]).reset_index(drop=True)
    ini_ts = pd.Timestamp(data_inicio)
    fim_ts = pd.Timestamp(data_fim) + pd.Timedelta(days=1)  # inclui o dia final
    mask = (datas >= ini_ts) & (datas < fim_ts)
    idxs = np.where(mask.values)[0]
    if len(idxs) < 10:
        return None, None
    ini, fim = int(idxs[0]), int(idxs[-1]) + 1
    res = executar_backtest_v4(
        df_fast, params, ini, fim, taxa, slippage, candles_dia,
        incluir_equity=True, incluir_trades=True,
    )
    datas_janela = datas.iloc[ini:fim].reset_index(drop=True)
    eq = pd.Series(res["equity"], index=datas_janela)
    preco = df["fechamento"].values[ini:fim]
    bh = pd.Series(preco / preco[0] * CAPITAL_INICIAL, index=datas_janela)
    df_curva = pd.DataFrame({"Estrategia": eq, "Buy&Hold": bh}).resample("1D").last().dropna()
    bh_ret = (preco[-1] / preco[0] - 1) * 100.0

    trades = []
    for t in res.get("trades") or []:
        saida_dt = datas_janela.iloc[t["saida_idx"]] if t["saida_idx"] is not None else datas_janela.iloc[-1]
        trades.append({
            "entrada": datas_janela.iloc[t["entrada_idx"]],
            "saida": saida_dt,
            "preco_entrada": t["preco_entrada"],
            "preco_saida": t["preco_saida"],
            "motivo": t["motivo"],
            "capital_entrada": t["capital_entrada"],
            "capital_saida": t["capital_saida"],
            "resultado_pct": (t["capital_saida"] / t["capital_entrada"] - 1) * 100.0,
        })
    metricas = {
        "retorno_estrategia_%": res["retorno_total_pct"],
        "retorno_buyhold_%": round(bh_ret, 2),
        "dd_%": res["drawdown_pct"],
        "calmar": res["calmar"],
        "num_trades": res["num_trades"],
        "data_ini": datas_janela.iloc[0],
        "data_fim": datas_janela.iloc[-1],
        "trades": trades,
    }
    return df_curva, metricas


# ----------------------------------------------------------------------------
# PORTFÓLIO DA ESTRATÉGIA OURO ROBUSTA (calculado ao vivo, mesmo motor e mesmos
# pesos) — substitui a leitura dos CSVs de portfólio, que eram da versão
# OTIMIZADA por ativo (que decidimos não usar mais). Assim o resumo do topo bate
# exatamente com os sinais e gráficos, todos na receita robusta por grupo.
# ----------------------------------------------------------------------------
def _ret_diario_robusto(ativo: str, interval_str: str):
    """Retornos diários da estratégia robusta (receita do grupo) em dev e
    holdout, em fatias separadas — mesmo tratamento do holdout_v4."""
    params = RECEITA_ROBUSTA[grupo_ouro(ativo)]
    info_liq = classificar_liquidez(ativo)
    df = carregar_dados(ativo, interval_str)
    if df is None or df.empty:
        return None
    periodos = separar_periodos(df["t_abert"], corte=DATA_CORTE_HOLDOUT)
    idx = periodos["idx_dev_fim"]
    candles_dia = CANDLES_POR_DIA[interval_str]
    out = {"dev": None, "holdout": None}
    for nome, fatia in (("dev", df.iloc[:idx]), ("holdout", df.iloc[idx:])):
        fatia = fatia.reset_index(drop=True)
        if len(fatia) < max(params["media_filtro"], 30) + 5:
            continue
        df_fast = montar_df_fast(fatia, params)
        res = executar_backtest_v4(
            df_fast, params, 0, len(df_fast["fechamento"]),
            info_liq["taxa"], info_liq["slippage"], candles_dia, incluir_equity=True,
        )
        datas = pd.to_datetime(df_fast["t_abert"])
        eq = pd.Series(res["equity"], index=datas).resample("1D").last().dropna()
        preco = pd.Series(df_fast["fechamento"], index=datas).resample("1D").last().dropna()
        out[nome] = {"ret": eq.pct_change().dropna(), "ret_bh": preco.pct_change().dropna(), "res": res}
    return out


def _metricas_portfolio_local(ret_por_ativo: dict, pesos: pd.Series) -> dict:
    """Combina retornos diários de vários ativos com pesos, na janela comum."""
    ativos = [a for a in ret_por_ativo if a in pesos.index and len(ret_por_ativo[a]) > 0]
    if not ativos:
        return None
    datas = None
    for a in ativos:
        idx = ret_por_ativo[a].index
        datas = idx if datas is None else datas.intersection(idx)
    if datas is None or len(datas) < 5:
        return None
    datas = datas.sort_values()
    dfret = pd.DataFrame({a: ret_por_ativo[a].reindex(datas) for a in ativos}).fillna(0.0)
    p = pesos.reindex(ativos)
    p = p / p.sum()
    retp = (dfret * p).sum(axis=1)
    eq = (1 + retp).cumprod()
    rt = eq.iloc[-1] - 1.0
    dias = (datas[-1] - datas[0]).days
    anos = dias / 365.25 if dias > 0 else None
    ra = (1 + rt) ** (1 / anos) - 1 if anos and anos > 0 else None
    dd = ((eq.cummax() - eq) / eq.cummax()).max()
    calmar = ra / dd if (dd and dd > 0 and ra is not None) else None
    return {
        "eq": eq * CAPITAL_INICIAL,
        "ret_total_%": round(rt * 100, 2),
        "ret_anual_%": round(ra * 100, 2) if ra is not None else None,
        "dd_%": round(dd * 100, 2),
        "calmar": round(calmar, 3) if calmar is not None else None,
        "ini": datas[0], "fim": datas[-1], "n_dias": len(datas), "n_ativos": len(ativos),
    }


def _pesos_inverse_vol(ret_dev_por_ativo: dict) -> dict:
    """Pesos por volatilidade inversa (mesma metodologia do portfolio_v4.py):
    vol diária de cada ativo na janela comum a TODOS, peso_i = (1/vol_i)/soma.
    Aqui sobre os retornos da estratégia ROBUSTA (não a otimizada)."""
    datas = None
    for s in ret_dev_por_ativo.values():
        idx = s.index
        datas = idx if datas is None else datas.intersection(idx)
    if datas is None or len(datas) < 5:
        return {}
    datas = datas.sort_values()
    dfret = pd.DataFrame({a: s.reindex(datas) for a, s in ret_dev_por_ativo.items()})
    vol = dfret.std(ddof=1)
    inv = 1.0 / vol
    pesos = inv / inv.sum()
    return {a: round(float(p) * 100, 2) for a, p in pesos.items()}


@st.cache_data(ttl=1800, show_spinner=False)
def computar_portfolio_robusto(versao: str = ""):
    """Faz UM passo por todos os 22 ativos com a receita robusta e devolve, de
    uma vez: os pesos (inverse-vol da estratégia robusta, período de dev) e as
    métricas de portfólio — DEV só veteranas (janela longa ~5 anos) e HOLDOUT
    com os 22 (12 meses fora da amostra). Tudo com os MESMOS pesos robustos."""
    ret_dev_all, ret_dev_vet, ret_dev_vet_bh, ret_hold, ret_hold_bh = {}, {}, {}, {}, {}
    for ativo, interval in ATIVOS_PORTFOLIO_V4.items():
        r = _ret_diario_robusto(ativo, interval)
        if r is None:
            continue
        if r["dev"]:
            ret_dev_all[ativo] = r["dev"]["ret"]
            if grupo_ouro(ativo) == "veterana":
                ret_dev_vet[ativo] = r["dev"]["ret"]
                ret_dev_vet_bh[ativo] = r["dev"]["ret_bh"]
        if r["holdout"]:
            ret_hold[ativo] = r["holdout"]["ret"]
            ret_hold_bh[ativo] = r["holdout"]["ret_bh"]

    weights_pct = _pesos_inverse_vol(ret_dev_all)
    pesos = pd.Series({a: p / 100.0 for a, p in weights_pct.items()})
    return {
        "weights_pct": weights_pct,
        "dev_vet": _metricas_portfolio_local(ret_dev_vet, pesos),
        "dev_vet_bh": _metricas_portfolio_local(ret_dev_vet_bh, pesos),
        "hold": _metricas_portfolio_local(ret_hold, pesos),
        "hold_bh": _metricas_portfolio_local(ret_hold_bh, pesos),
    }


def _camada_trades(trades: list, eixo_x, eixo_y):
    """Marcadores dos trades sobre a linha da estratégia: ▲ compra, ▼ venda
    (verde = trade lucrativo, vermelho = prejuízo). Rótulos em inglês, como o
    resto do gráfico."""
    linhas = []
    for n, t in enumerate(trades, start=1):
        ganho = t["resultado_pct"] >= 0
        linhas.append({"Date": t["entrada"], "When": brt(t["entrada"]), "Capital": t["capital_entrada"],
                       "Trade": n, "Event": "Buy", "Price": t["preco_entrada"], "Result": None,
                       "Kind": "buy"})
        if t["motivo"] != "aberto":
            linhas.append({"Date": t["saida"], "When": brt(t["saida"]), "Capital": t["capital_saida"], "Trade": n,
                           "Event": "Sell (stop)" if t["motivo"] == "stop" else "Sell (crossover)",
                           "Price": t["preco_saida"], "Result": t["resultado_pct"],
                           "Kind": "win" if ganho else "loss"})
    if not linhas:
        return []
    dados = pd.DataFrame(linhas)
    cores = alt.Scale(domain=["buy", "win", "loss"], range=["#38bdf8", COR_ALTA, COR_BAIXA])
    formas = alt.Scale(domain=["buy", "win", "loss"], range=["triangle-up", "triangle-down", "triangle-down"])
    return [
        alt.Chart(dados).mark_point(filled=True, size=70, opacity=0.95, stroke="#0b1020", strokeWidth=0.6)
        .encode(
            x=eixo_x, y=eixo_y,
            color=alt.Color("Kind:N", scale=cores, legend=None),
            shape=alt.Shape("Kind:N", scale=formas, legend=None),
            tooltip=[
                alt.Tooltip("Trade:Q", title="Trade #"),
                alt.Tooltip("Event:N", title="Event"),
                alt.Tooltip("When:T", title="Date (Brasília)", format="%d %b %Y %H:%M"),
                alt.Tooltip("Price:Q", title="Price", format=",.6~g"),
                alt.Tooltip("Capital:Q", title="Capital (USD)", format=",.0f"),
                alt.Tooltip("Result:Q", title="Trade result (%)", format="+.2f"),
            ],
        )
    ]


def grafico_area(df: pd.DataFrame, altura: int = 340, inicio_holdout=None, titulo: str = None,
                 trades: list = None):
    """Curva de capital Estratégia vs Buy&Hold.

    Rótulos em INGLÊS de propósito: o usuário tira print desses gráficos pra
    publicar (ex.: Binance), e público internacional entende sem tradução.

    Tooltip mostra o capital em USD **e** o retorno % desde o início da janela
    — é o % que torna a comparação com o Buy&Hold imediata (US$ 1.394 não diz
    nada sozinho; "+39,4%" ao lado de "+12,1%" diz tudo).

    `inicio_holdout`: se informado, sombreia a região de holdout e desenha uma
    linha divisória rotulada — deixa óbvio no print o que é dado fora da amostra.
    """
    df = df.rename(columns={"Estrategia": "Strategy", "Buy&Hold": "Buy & Hold"})
    base_valores = df.iloc[0]
    df_pct = (df / base_valores - 1.0) * 100.0

    df_long = (
        df.reset_index(names="Date")
        .melt("Date", var_name="Series", value_name="Capital")
        .merge(
            df_pct.reset_index(names="Date").melt("Date", var_name="Series", value_name="Return"),
            on=["Date", "Series"],
        )
    )

    escala_cores = alt.Scale(domain=["Strategy", "Buy & Hold"], range=[COR_ESTRATEGIA, COR_BUYHOLD])
    eixo_x = alt.X("Date:T", title=None, axis=alt.Axis(format="%b %Y", labelAngle=0))
    eixo_y = alt.Y("Capital:Q", stack=None, title="Capital (USD)",
                   axis=alt.Axis(format="~s"))

    base = alt.Chart(df_long)
    area = base.mark_area(opacity=0.22, interpolate="monotone", line=False).encode(
        x=eixo_x, y=eixo_y,
        color=alt.Color("Series:N", scale=escala_cores,
                        legend=alt.Legend(title=None, orient="top", symbolType="stroke")),
    )
    linha = base.mark_line(interpolate="monotone", strokeWidth=2.4).encode(
        x=eixo_x, y=eixo_y, color=alt.Color("Series:N", scale=escala_cores, legend=None),
    )

    # hover: régua vertical + pontos + tooltip com USD e %
    hover = alt.selection_point(fields=["Date"], nearest=True, on="pointerover",
                                 empty=False, clear="pointerout")
    seletor = base.mark_rule(opacity=0).encode(x=eixo_x).add_params(hover)
    regua = base.mark_rule(color="#94a3b8", strokeWidth=1).encode(x=eixo_x).transform_filter(hover)
    pontos = base.mark_circle(size=70).encode(
        x=eixo_x, y=eixo_y,
        color=alt.Color("Series:N", scale=escala_cores, legend=None),
        opacity=alt.condition(hover, alt.value(1), alt.value(0)),
        tooltip=[
            alt.Tooltip("Date:T", title="Date", format="%d %b %Y"),
            alt.Tooltip("Series:N", title="Series"),
            alt.Tooltip("Capital:Q", title="Capital (USD)", format=",.0f"),
            alt.Tooltip("Return:Q", title="Return", format="+.2f"),
        ],
    )

    camadas = [area, linha, seletor, regua, pontos]
    if trades:
        camadas += _camada_trades(trades, eixo_x, eixo_y)

    if inicio_holdout is not None:
        marca = pd.DataFrame({"Date": [pd.Timestamp(inicio_holdout)]})
        fim = pd.DataFrame({"Date": [pd.Timestamp(inicio_holdout)],
                            "Date2": [pd.Timestamp(df.index[-1])]})
        sombra = (
            alt.Chart(fim)
            .mark_rect(opacity=0.10, color="#e2e8f0")
            .encode(x="Date:T", x2="Date2:T")
        )
        divisor = (
            alt.Chart(marca)
            .mark_rule(color="#e2e8f0", strokeDash=[5, 4], strokeWidth=1.5)
            .encode(x="Date:T")
        )
        rotulo = (
            alt.Chart(marca)
            .mark_text(text="Holdout (out-of-sample)", align="left", dx=6, dy=-6,
                       baseline="top", color="#cbd5e1", fontSize=11, fontWeight=600)
            .encode(x="Date:T")
        )
        camadas = [sombra] + camadas + [divisor, rotulo]

    grafico = (alt.layer(*camadas)
               .resolve_scale(color="independent", shape="independent")
               .properties(height=altura, background="transparent"))
    if titulo:
        grafico = grafico.properties(title=alt.TitleParams(
            titulo, color="#e2e8f0", fontSize=13, fontWeight=600, anchor="start", offset=8))
    return (
        grafico
        .configure_axis(gridColor="#262d3d", domainColor="#3a4256",
                        labelColor="#9aa4b8", titleColor="#9aa4b8", labelFontSize=11)
        .configure_view(strokeWidth=0)
        .configure_legend(labelColor="#e5e7eb", labelFontSize=12)
    )


def tabela_trades(trades: list, ativo: str):
    """Lista de todos os trades da simulação (o 'diário' do código antigo):
    entrada, saída, motivo, duração, resultado e capital acumulado."""
    st.markdown(f"#### 🧾 Diário de trades — {len(trades)} operações")
    if not trades:
        st.info("Nenhum trade nesta janela.")
        return
    fechados = [t for t in trades if t["motivo"] != "aberto"]
    ganhos = [t["resultado_pct"] for t in fechados if t["resultado_pct"] >= 0]
    perdas = [t["resultado_pct"] for t in fechados if t["resultado_pct"] < 0]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Taxa de acerto", f"{len(ganhos) / len(fechados) * 100:.0f}%" if fechados else "—",
              help=f"{len(ganhos)} lucrativos de {len(fechados)} fechados")
    k2.metric("Ganho médio", f"{np.mean(ganhos):+.1f}%" if ganhos else "—")
    k3.metric("Perda média", f"{np.mean(perdas):+.1f}%" if perdas else "—")
    payoff = (np.mean(ganhos) / abs(np.mean(perdas))) if ganhos and perdas else None
    k4.metric("Payoff (ganho/perda)", f"{payoff:.2f}×" if payoff else "—",
              help="Quantas vezes o ganho médio é maior que a perda média. Estratégias de "
                   "tendência costumam acertar menos da metade e compensar com payoff alto.")

    motivos = {"stop": "🛑 Stop", "cruzamento": "↘️ Cruzamento", "aberto": "⏳ Em aberto"}
    df_t = pd.DataFrame([{
        "#": n,
        "Compra": brt(t["entrada"]),
        "Preço compra": t["preco_entrada"],
        "Venda": None if t["motivo"] == "aberto" else brt(t["saida"]),
        "Preço venda": t["preco_saida"],
        "Saída por": motivos[t["motivo"]],
        "Dias": round((t["saida"] - t["entrada"]).total_seconds() / 86400, 1),
        "Resultado (%)": round(t["resultado_pct"], 2),
        "Resultado ($)": round(t["capital_saida"] - t["capital_entrada"], 2),
        "Capital ($)": round(t["capital_saida"], 2),
    } for n, t in enumerate(trades, start=1)]).iloc[::-1]

    def _cor(v):
        return f"color: {COR_ALTA}" if v >= 0 else f"color: {COR_BAIXA}"

    st.dataframe(
        df_t.style.map(_cor, subset=["Resultado (%)", "Resultado ($)"]),
        hide_index=True, width="stretch", height=min(420, 38 + 35 * len(df_t)),
        column_config={
            "Compra": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm"),
            "Venda": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm"),
            "Dias": st.column_config.NumberColumn(format="%.1f"),
            "Preço compra": st.column_config.NumberColumn(format="%.6g"),
            "Preço venda": st.column_config.NumberColumn(format="%.6g"),
            "Resultado (%)": st.column_config.NumberColumn(format="%+.2f%%"),
            "Resultado ($)": st.column_config.NumberColumn(format="$%+.2f"),
            "Capital ($)": st.column_config.NumberColumn(format="$%.2f"),
        },
    )
    st.caption("Mais recente no topo. Preços já incluem slippage; resultado já desconta taxa nas "
               "duas pontas. Capital = quanto os $1000 iniciais viraram ao fim de cada trade. "
               "Horários de Brasília.")
    st.download_button("⬇️ Baixar diário (CSV)", df_t.iloc[::-1].to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"trades_{ativo}.csv", mime="text/csv", key=f"dl_{ativo}")


def badge_html(texto: str, cor: str) -> str:
    return f'<span class="sinal-badge" style="background:{cor}22; color:{cor}; border:1px solid {cor}66;">{texto}</span>'


def chip_html(texto: str, cor: str) -> str:
    return f'<span class="chip" style="background:{cor}22; color:{cor}; border:1px solid {cor}55;">{texto}</span>'


def formatar_preco(v: float) -> str:
    """Preços de cripto variam de $0.0000001 (memecoin) a $100000+ (BTC) —
    ajusta as casas decimais pra sempre mostrar algo com precisão útil."""
    if v >= 1:
        return f"{v:,.2f}"
    if v >= 0.01:
        return f"{v:.4f}"
    return f"{v:.8f}"


# ----------------------------------------------------------------------------
# UI
# ----------------------------------------------------------------------------
st.title("📡 Estratégia Ouro Robusta — Radar de Sinais & Portfólio")
st.caption(
    "Entrada = cruzamento de médias + filtro de tendência · Saída = stop ATR fixo OU cruzamento "
    "contrário. Sinais, gráficos e portfólio usam a **receita robusta por grupo** (veterana/nova) "
    "— não os parâmetros otimizados por ativo, que se mostraram overfitting."
)

df_resumo = carregar_csv(RESUMO_CSV)

if df_resumo is None:
    st.error(f"Não encontrei {RESUMO_CSV}. Rode `python otimizador_v4.py` primeiro.")
    st.stop()

ao_vivo = atualizar_precos_ao_vivo()
VERSAO = ao_vivo["versao"]
AGORA_UTC = ao_vivo["atualizado_em"]

_atraso = ao_vivo["pior_atraso_h"]
if _atraso is None or _atraso > LIMITE_DADOS_VELHOS_HORAS:
    _ult = ao_vivo["ultimo_candle"]
    _ult_txt = brt(_ult).strftime("%d/%m/%Y %H:%M") if _ult is not None else "—"
    st.warning(
        f"⚠️ **Dados desatualizados**: o candle mais recente disponível é de **{_ult_txt}** "
        f"(Brasília). A Binance não respondeu daqui (no Streamlit Cloud isso é o bloqueio por "
        f"região) e o app está usando o último cache salvo. **Os sinais abaixo refletem essa "
        f"data, não agora.**"
    )
else:
    st.caption(
        f"🟢 Preços ao vivo da Binance · atualizados às {brt(AGORA_UTC).strftime('%d/%m %H:%M')} "
        f"· todos os horários do app estão em **horário de Brasília**"
    )

with st.spinner("Calculando portfólio e pesos da estratégia robusta..."):
    resultado_rob = computar_portfolio_robusto(VERSAO)
# pesos calculados AO VIVO pela volatilidade inversa da estratégia robusta
pesos_map = resultado_rob["weights_pct"] if resultado_rob else {}
# significância medida sobre a receita que realmente roda (ver BOOTSTRAP_CSV)
boot_df = carregar_bootstrap()

if "ativo_selecionado" not in st.session_state:
    st.session_state["ativo_selecionado"] = None


@st.cache_data(ttl=300, show_spinner=False)
def carregar_sombra(versao: str = ""):
    try:
        return sombra_analise.resumo()
    except Exception as e:  # arquivo sendo regravado pela tarefa horária etc.
        return {"erro": str(e)}


def _fmt_dt(ts, com_hora=True):
    """Com hora: converte UTC -> Brasília. Só data (séries diárias): não desloca,
    senão um dia que começa 00:00 UTC apareceria como o dia anterior."""
    if ts is None or pd.isna(ts):
        return "—"
    return brt(ts).strftime("%d/%m/%Y %H:%M") if com_hora else pd.Timestamp(ts).strftime("%d/%m/%Y")


def _cor_resultado(v):
    if v is None or pd.isna(v):
        return ""
    return f"color: {COR_ALTA}" if v >= 0 else f"color: {COR_BAIXA}"


# ============================================================================
# 📡 RADAR
# ============================================================================
def secao_radar():
    st.header("Radar de Sinais (22 ativos)")
    st.caption(
        "🟢 ALTA = posicionado agora · 🔴 BAIXA = fora do mercado. O selo de significância em cada "
        "card vem do **bootstrap do Sharpe da receita robusta** (a estratégia que gera estes sinais), "
        "não do DSR dos parâmetros otimizados, que o app não usa."
    )

    # Os sinais usam a RECEITA OURO ROBUSTA do grupo do ativo (veterana/nova) —
    # NÃO os parâmetros otimizados por ativo (overfitting).
    cartoes = []
    for _, row in df_resumo.iterrows():
        ativo = row["Ativo"]
        params = RECEITA_ROBUSTA[grupo_ouro(ativo)]
        sinal = calcular_sinal(ativo, row["Interval"], params, VERSAO)
        var = None
        if sinal["preco_sinal"] and sinal["preco_atual"] is not None:
            var = (sinal["preco_atual"] / sinal["preco_sinal"] - 1) * 100
        dist_stop = None
        if sinal["posicionado"] and sinal["stop_atual"] is not None and sinal["preco_atual"]:
            dist_stop = (sinal["preco_atual"] / sinal["stop_atual"] - 1) * 100
        dm = sinal["data_mudanca"]
        novo = dm is not None and (AGORA_UTC - dm) <= pd.Timedelta(hours=48)
        cartoes.append({"row": row, "params": params, "sinal": sinal, "var": var,
                        "dist_stop": dist_stop, "novo": novo,
                        "veterana": row["Grupo_Liquidez"] == "liquido",
                        "peso": pesos_map.get(ativo)})

    n_alta = sum(c["sinal"]["posicionado"] for c in cartoes)
    n_novos = sum(c["novo"] for c in cartoes)
    st.markdown(
        f"**🟢 {n_alta} em ALTA · 🔴 {len(cartoes) - n_alta} em BAIXA · 🆕 {n_novos} "
        f"sinal{'is' if n_novos != 1 else ''} nas últimas 48h**"
    )

    f1, f2, f3, f4 = st.columns([1.25, 1.35, 0.9, 1.5])
    with f1:
        f_sinal = st.segmented_control("Sinal", ["Todos", "🟢 ALTA", "🔴 BAIXA"],
                                       default="Todos", key="f_sinal") or "Todos"
    with f2:
        f_grupo = st.segmented_control("Grupo", ["Todos", "💧 Veteranas", "🪙 Novas"],
                                       default="Todos", key="f_grupo") or "Todos"
    with f3:
        st.write("")
        f_novos = st.toggle("🆕 Só sinais novos (48h)", key="f_novos")
    with f4:
        f_ordem = st.selectbox("Ordenar por", [
            "Data do sinal (mais recente primeiro)",
            "Variação desde o sinal (maior primeiro)",
            "Peso no portfólio (maior primeiro)",
            "Distância do stop (mais perto primeiro)",
        ], key="f_ordem")

    vis = cartoes
    if f_sinal == "🟢 ALTA":
        vis = [c for c in vis if c["sinal"]["posicionado"]]
    elif f_sinal == "🔴 BAIXA":
        vis = [c for c in vis if not c["sinal"]["posicionado"]]
    if f_grupo == "💧 Veteranas":
        vis = [c for c in vis if c["veterana"]]
    elif f_grupo == "🪙 Novas":
        vis = [c for c in vis if not c["veterana"]]
    if f_novos:
        vis = [c for c in vis if c["novo"]]

    menos_inf = float("-inf")
    if f_ordem.startswith("Variação"):
        vis = sorted(vis, key=lambda c: c["var"] if c["var"] is not None else menos_inf, reverse=True)
    elif f_ordem.startswith("Peso"):
        vis = sorted(vis, key=lambda c: c["peso"] if c["peso"] is not None else menos_inf, reverse=True)
    elif f_ordem.startswith("Distância"):
        vis = sorted(vis, key=lambda c: c["dist_stop"] if c["dist_stop"] is not None else float("inf"))
    else:
        vis = sorted(vis, key=lambda c: c["sinal"]["data_mudanca"] or pd.Timestamp.min, reverse=True)

    if not vis:
        st.info("Nenhum ativo com esses filtros.")

    N_COLS = 4
    for i in range(0, len(vis), N_COLS):
        cols = st.columns(N_COLS)
        for col, item in zip(cols, vis[i: i + N_COLS]):
            with col:
                _card(item)

    st.divider()
    secao_detalhe()


def _card(item):
    row, sinal = item["row"], item["sinal"]
    ativo = row["Ativo"]
    with st.container(border=True):
        posicionado = sinal["posicionado"]
        cor_sinal = COR_ALTA if posicionado else COR_BAIXA
        texto_sinal = "🟢 ALTA" if posicionado else "🔴 BAIXA"
        data_txt = (_fmt_dt(sinal["data_mudanca"]) if sinal["data_mudanca"] is not None
                    else "sem sinal no histórico")

        variacao_chip = ""
        if item["var"] is not None:
            cv = COR_ALTA if item["var"] >= 0 else COR_BAIXA
            variacao_chip = (f'<span class="variacao-chip" style="background:{cv}22; color:{cv}; '
                             f'border:1px solid {cv}55;">{item["var"]:+.2f}%</span>')
        novo_chip = (f'<span class="variacao-chip" style="background:#38bdf822; color:#38bdf8; '
                     f'border:1px solid #38bdf855;">🆕 NOVO</span>') if item["novo"] else ""

        preco_txt = formatar_preco(sinal["preco_atual"]) if sinal["preco_atual"] is not None else "—"

        stop_html = ""
        if item["dist_stop"] is not None:
            stop_html = (
                f'<div class="stop-row">🛑 <b>Stop fixo desde a entrada:</b> {formatar_preco(sinal["stop_atual"])} '
                f'<span style="color:#94a3b8;">(faltam {abs(sinal["stop_atual"] / sinal["preco_atual"] - 1) * 100:.2f}% '
                f'de queda a partir de agora pra atingi-lo)</span></div>'
            )

        cor_grupo = COR_LIQUIDO if item["veterana"] else COR_MENOS_LIQUIDO
        texto_grupo = "💧 Veterana" if item["veterana"] else "🪙 Nova"
        peso = item["peso"]

        html = f"""
        <div class="sinal-card" style="background:{cor_sinal}14; border:1px solid {cor_sinal}40; border-left:4px solid {cor_sinal};">
            <div style="display:flex; flex-wrap:wrap; justify-content:space-between; align-items:center; gap:4px 8px;">
                <span style="font-size:1.05rem; font-weight:700; color:#f1f5f9; overflow-wrap:anywhere;">{ativo}</span>
                {badge_html(texto_sinal, cor_sinal)}
            </div>
            <div style="margin-top:8px;">
                <span class="preco-atual">{preco_txt}</span>{variacao_chip}{novo_chip}
            </div>
            <div class="meta-sinal">sinal desde {data_txt} · preço no sinal: {formatar_preco(sinal["preco_sinal"]) if sinal["preco_sinal"] else "—"}</div>
            {stop_html}
            <div style="margin-top:8px;">
                {chip_html(texto_grupo, cor_grupo)}
                {chip_html(f"peso {peso:.2f}%" if peso is not None else "peso n/d", "#64748b")}
            </div>
        """
        sig = _avaliar_significancia(boot_df, ativo)
        if sig is not None:
            if sig["estado"] == "confirmado":
                html += f"""
                <div class="dsr-aviso" style="background:{COR_ALTA}18; color:{COR_ALTA}; border:1px solid {COR_ALTA}44;">
                    ✅ Edge confirmado no histórico — Sharpe {sig['sharpe']:+.2f}, p={sig['p']:.3f} ({sig['n_janelas']} janelas anuais)
                </div>
                """
            elif sig["estado"] == "amostra_curta":
                html += f"""
                <div class="dsr-aviso" style="background:{COR_DSR_ALERTA}22; color:{COR_DSR_ALERTA}; border:1px solid {COR_DSR_ALERTA}55;">
                    ⚠️ Histórico curto ({sig['n_janelas']} janela{'s' if sig['n_janelas'] != 1 else ''} anual) — sem dados suficientes pra confirmar ou descartar o edge
                </div>
                """
            else:
                html += f"""
                <div class="dsr-aviso" style="background:{COR_DSR_ALERTA}22; color:{COR_DSR_ALERTA}; border:1px solid {COR_DSR_ALERTA}55;">
                    ⚠️ Edge não confirmado — p={sig['p']:.3f} (acima de 0,05) em {sig['n_janelas']} janelas; tratar com cautela
                </div>
                """
        html += "</div>"
        html = "\n".join(linha.strip() for linha in html.strip().splitlines())
        st.markdown(html, unsafe_allow_html=True)

        if st.button("Ver detalhes", key=f"btn_{ativo}", width="stretch"):
            st.session_state["ativo_selecionado"] = ativo


def secao_detalhe():
    st.header("Análise Detalhada por Ativo")
    ativo_sel = st.session_state["ativo_selecionado"]
    if ativo_sel is None:
        st.info("Clique em \"Ver detalhes\" em algum card acima pra ver o gráfico de capital e o diário de trades.")
        return
    row = df_resumo[df_resumo["Ativo"] == ativo_sel].iloc[0]
    info_liq = classificar_liquidez(ativo_sel)
    interval_sel = row["Interval"]
    grupo_a = grupo_ouro(ativo_sel)
    params = RECEITA_ROBUSTA[grupo_a]

    st.subheader(f"{ativo_sel} ({interval_sel}) — Estratégia Ouro Robusta ({grupo_a})")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Grupo", grupo_a)
    c2.metric("Peso no portfólio", f"{pesos_map.get(ativo_sel, float('nan')):.2f}%")
    c3.metric("Média lenta / filtro", f"{params['media_lenta']}/{params['media_filtro']}")
    c4.metric("ATR", f"{params['atr_periodo']}×{params['atr_multiplicador']}")
    sig_sel = _avaliar_significancia(boot_df, ativo_sel)
    if sig_sel is not None:
        if sig_sel["estado"] == "confirmado":
            st.success(
                f"✅ **Edge confirmado**: Sharpe {sig_sel['sharpe']:+.2f} com p={sig_sel['p']:.3f} "
                f"(bootstrap de 2.000 reamostragens sobre {sig_sel['n_janelas']} janelas anuais da "
                f"receita robusta). Significa que o resultado dificilmente é sorte."
            )
        elif sig_sel["estado"] == "amostra_curta":
            st.warning(
                f"⚠️ **Histórico curto**: só {sig_sel['n_janelas']} janela(s) anual(is) disponível(is). "
                f"Não dá pra confirmar nem descartar o edge — os sinais existem, mas sem lastro "
                f"estatístico ainda."
            )
        else:
            st.warning(
                f"⚠️ **Edge não confirmado**: p={sig_sel['p']:.3f} (acima do limite de 0,05) em "
                f"{sig_sel['n_janelas']} janelas anuais. O retorno histórico pode ser sorte — "
                f"tratar com cautela extra."
            )
    st.caption(
        f"Parâmetros em uso: rápida={params['media_rapida']} · lenta={params['media_lenta']} · "
        f"filtro={params['media_filtro']} · ATR={params['atr_periodo']}×{params['atr_multiplicador']} · "
        f"Grupo: {info_liq['grupo']} (taxa {info_liq['taxa']*100:.2f}% + slippage {info_liq['slippage']*100:.3f}%)"
    )

    # ---- Gráfico principal: histórico completo, com holdout demarcado ----
    st.markdown("### 📈 Evolução do capital")
    df_datas = carregar_dados(ativo_sel, interval_sel)
    if df_datas is None or df_datas.empty:
        st.info("Sem dados de preço pra este ativo.")
        return
    datas_all = pd.to_datetime(df_datas["t_abert"])
    dmin, dmax = datas_all.min().date(), datas_all.max().date()
    periodos_a = separar_periodos(datas_all, corte=DATA_CORTE_HOLDOUT)
    inicio_hold = (periodos_a["holdout_inicio"].date()
                   if periodos_a["holdout_inicio"] is not None else None)

    colcal1, colcal2 = st.columns([2, 1])
    with colcal1:
        intervalo = st.date_input(
            "Período exibido", value=(dmin, dmax), min_value=dmin, max_value=dmax,
            key=f"cal_{ativo_sel}", format="DD/MM/YYYY",
        )
    with colcal2:
        st.caption(f"Padrão = histórico completo. A faixa sombreada à direita é o holdout (desde "
                   f"{DATA_CORTE_HOLDOUT:%d/%m/%Y}, nunca visto na criação da receita). Arraste as "
                   f"datas pra recalcular em qualquer janela.")

    if not (isinstance(intervalo, (list, tuple)) and len(intervalo) == 2):
        st.info("Escolha as duas datas (início e fim) no calendário acima.")
        return
    data_ini, data_fim = intervalo
    with st.spinner("Recalculando na janela escolhida..."):
        df_janela, met = curva_capital_intervalo(
            ativo_sel, interval_sel, params, info_liq["taxa"], info_liq["slippage"],
            data_ini, data_fim, VERSAO,
        )
    if df_janela is None or met is None:
        st.warning("Janela muito curta pra simular (precisa de pelo menos ~10 candles). Escolha um período maior.")
        return

    m1, m2, m3, m4 = st.columns(4)
    delta_vs_bh = met["retorno_estrategia_%"] - met["retorno_buyhold_%"]
    m1.metric("Estratégia (janela)", f"{met['retorno_estrategia_%']:.2f}%",
              delta=f"{delta_vs_bh:+.2f}% vs Buy&Hold")
    m2.metric("Buy & Hold (janela)", f"{met['retorno_buyhold_%']:.2f}%")
    m3.metric("Drawdown máx", f"{met['dd_%']:.2f}%")
    m4.metric("Trades", f"{int(met['num_trades'])}")
    calmar_txt = f"{met['calmar']:.2f}" if met["calmar"] is not None else "—"
    st.caption(
        f"Janela: {_fmt_dt(met['data_ini'], False)} a {_fmt_dt(met['data_fim'], False)} "
        f"· Calmar {calmar_txt} · indicadores aquecidos com o histórico anterior à janela "
        f"(capital recomeça em ${CAPITAL_INICIAL:.0f} no início do período escolhido)."
    )
    marca_hold = inicio_hold if (inicio_hold is not None and data_ini < inicio_hold < data_fim) else None
    trades_janela = met.get("trades") or []
    mostrar_trades = st.toggle(
        f"Marcar os {len(trades_janela)} trades no gráfico (▲ compra · ▼ venda)",
        value=True, key=f"tog_trades_{ativo_sel}",
    )
    st.altair_chart(
        grafico_area(
            df_janela, altura=380, inicio_holdout=marca_hold,
            titulo=f"{ativo_sel} · {interval_sel} — Strategy vs Buy & Hold",
            trades=trades_janela if mostrar_trades else None,
        ),
        width="stretch",
    )
    tabela_trades(trades_janela, ativo_sel)
    _sombra_do_ativo(ativo_sel)

    # ---- Janelas oficiais fixas (dev | holdout), pra referência ----
    with st.expander("🔬 Janelas oficiais de validação (metodologia isolada)", expanded=False):
        st.caption(
            "Diferente do gráfico acima: aqui cada período é simulado do zero (capital recomeça "
            f"em ${CAPITAL_INICIAL:.0f} e os indicadores são calculados só com dados daquele "
            "período, sem aquecimento). São os números oficiais de validação — por isso podem "
            "diferir um pouco do gráfico principal."
        )
        with st.spinner("Reconstruindo curvas de capital..."):
            df_dev, df_holdout = curva_capital(ativo_sel, interval_sel, params, info_liq["taxa"],
                                               info_liq["slippage"], VERSAO)
        col_g1, col_g2 = st.columns(2)
        with col_g1:
            st.markdown(f"**Desenvolvimento** — até {DATA_CORTE_HOLDOUT:%d/%m/%Y}, onde a receita foi derivada")
            if df_dev is not None:
                st.altair_chart(grafico_area(df_dev, altura=260, titulo="Development period"),
                                width="stretch")
            else:
                st.info("Sem dados suficientes de desenvolvimento pra este ativo.")
        with col_g2:
            st.markdown(f"**Holdout** 🔒 — desde {DATA_CORTE_HOLDOUT:%d/%m/%Y}, nunca visto na derivação")
            if df_holdout is not None:
                st.altair_chart(grafico_area(df_holdout, altura=260, titulo="Holdout (out-of-sample)"),
                                width="stretch")
            else:
                st.info("Sem dados de holdout pra este ativo ainda.")


def _sombra_do_ativo(ativo):
    """O que a Sombra (Fase 4) fez de verdade neste ativo, ao lado do que o
    backtest assumiu."""
    r = carregar_sombra(VERSAO)
    if not r or "erro" in r:
        return
    fech = [t for t in r["fechados"] if t["ativo"] == ativo]
    abertos = [t for t in r["abertos"] if t["ativo"] == ativo]
    if not fech and not abertos:
        return
    st.markdown(f"#### 🕶️ Sombra neste ativo — {len(fech)} fechado(s), {len(abertos)} aberto(s)")
    linhas = [{
        "Compra": brt(t["compra"]), "Venda": brt(t["venda"]),
        "Sombra (%)": round(t["resultado_pct"], 2),
        "Backtest (%)": round(t["resultado_bt_pct"], 2),
        "Diferença (pp)": round(t["resultado_pct"] - t["resultado_bt_pct"], 2),
        "Resultado ($)": round(t["resultado_usd"], 2),
    } for t in fech] + [{
        "Compra": brt(t["compra"]), "Venda": None,
        "Sombra (%)": round(t["resultado_pct"], 2), "Backtest (%)": None,
        "Diferença (pp)": None, "Resultado ($)": round(t["resultado_usd"], 2),
    } for t in abertos]
    df = pd.DataFrame(linhas)
    st.dataframe(
        df.style.map(_cor_resultado, subset=["Sombra (%)", "Backtest (%)", "Resultado ($)"]),
        hide_index=True, width="stretch",
        column_config={
            "Compra": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm"),
            "Venda": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm"),
            "Sombra (%)": st.column_config.NumberColumn(format="%+.2f%%"),
            "Backtest (%)": st.column_config.NumberColumn(format="%+.2f%%"),
            "Diferença (pp)": st.column_config.NumberColumn(format="%+.2f"),
            "Resultado ($)": st.column_config.NumberColumn(format="$%+.2f"),
        },
    )
    st.caption("Sombra = preço real no instante em que o robô decidiria. Backtest = abertura do candle "
               "do sinal (o que a simulação assume). Venda vazia = posição ainda aberta.")


# ============================================================================
# 📊 PORTFÓLIO
# ============================================================================
def _rotulo_mercado(bh_anual):
    if bh_anual is None:
        return ""
    if bh_anual < -20:
        return "de baixa"
    if bh_anual > 20:
        return "de alta"
    return "lateral"


def _grafico_portfolio(m, m_bh, titulo):
    if m is None or m_bh is None:
        return
    df = pd.concat({"Estrategia": m["eq"], "Buy&Hold": m_bh["eq"]}, axis=1).dropna()
    if len(df) > 5:
        st.altair_chart(grafico_area(df, altura=240, titulo=titulo), width="stretch")


def secao_portfolio():
    st.header("Resumo do Portfólio — Estratégia Ouro Robusta")
    st.caption(
        "Receita robusta por grupo (veterana nas líquidas/antigas, nova nas recentes), pesos por "
        "volatilidade inversa. Curvas partindo de $1000, carteira inteira contra o Buy & Hold dos "
        "mesmos ativos com os mesmos pesos."
    )
    col_dev, col_holdout = st.columns(2)

    with col_dev:
        m = resultado_rob["dev_vet"] if resultado_rob else None
        m_bh = resultado_rob["dev_vet_bh"] if resultado_rob else None
        anos = (m["fim"] - m["ini"]).days / 365.25 if m is not None else None
        st.subheader(f"📈 Desenvolvimento (veteranas, {anos:.1f} anos)" if anos else "📈 Desenvolvimento")
        st.caption(f"Até {DATA_CORTE_HOLDOUT:%d/%m/%Y}, só as 8 líquidas/antigas (janela longa e "
                   f"confiável). A receita foi derivada aqui: não é validação cega.")
        if m is not None:
            c1, c2, c3 = st.columns(3)
            c1.metric("Retorno Anualizado", f"{m['ret_anual_%']:.2f}%")
            c2.metric("Drawdown Máximo", f"{m['dd_%']:.2f}%")
            c3.metric("Calmar", f"{m['calmar']:.3f}")
            bh_txt = (f" · Buy&Hold: {m_bh['ret_anual_%']:.1f}% anual, DD {m_bh['dd_%']:.1f}%, Calmar {m_bh['calmar']}"
                      if m_bh is not None else "")
            st.caption(f"{_fmt_dt(m['ini'], False)} a {_fmt_dt(m['fim'], False)} ({m['n_dias']} dias, "
                       f"{m['n_ativos']} veteranas){bh_txt}")
            _grafico_portfolio(m, m_bh, "Portfolio — development (8 veterans)")
        else:
            st.info("Sem dados suficientes pra compor o portfólio de desenvolvimento.")

    with col_holdout:
        m = resultado_rob["hold"] if resultado_rob else None
        m_bh = resultado_rob["hold_bh"] if resultado_rob else None
        st.subheader("🔒 Holdout — VALIDAÇÃO REAL")
        if m is not None and m_bh is not None:
            meses = (m["fim"] - DATA_CORTE_HOLDOUT).days / 30.44
            mercado = _rotulo_mercado(m_bh["ret_anual_%"])
            st.caption(
                f"Desde {DATA_CORTE_HOLDOUT:%d/%m/%Y} ({meses:.0f} meses, e crescendo): nada disso foi "
                f"visto na criação da receita. Mercado no período: **{mercado}** (Buy & Hold "
                f"{m_bh['ret_anual_%']:+.0f}%/ano)"
                + (". Aqui o que se mede é proteção de capital, não captura de alta." if mercado == "de baixa" else ".")
            )
            c1, c2, c3 = st.columns(3)
            c1.metric("Retorno Anualizado", f"{m['ret_anual_%']:.2f}%",
                      delta=f"{m['ret_anual_%'] - m_bh['ret_anual_%']:.2f}% vs Buy&Hold")
            c2.metric("Drawdown Máximo", f"{m['dd_%']:.2f}%")
            c3.metric("Calmar", f"{m['calmar']:.3f}" if m["calmar"] is not None else "—")
            st.caption(
                f"Buy&Hold no mesmo período: {m_bh['ret_anual_%']:.2f}% anualizado, "
                f"DD {m_bh['dd_%']:.2f}% · {_fmt_dt(m['ini'], False)} a {_fmt_dt(m['fim'], False)} "
                f"({m['n_ativos']} ativos)"
            )
            _grafico_portfolio(m, m_bh, "Portfolio — holdout (22 assets)")
        else:
            st.info("Sem dados de holdout pra compor o portfólio.")

    st.divider()
    secao_modo_alocacao()


@st.cache_data(ttl=3600)
def regime_btc_atual(versao: str = ""):
    """Retorna o regime BTC atual (BULL/LATERAL/BEAR) baseado no retorno dos
    últimos 365 dias (mesma classificação usada na Fase 1 walk-forward)."""
    df_btc = carregar_dados("BTCUSDT", "6h")
    if df_btc is None or df_btc.empty or len(df_btc) < 2:
        return None, None, None
    preco_atual = float(df_btc["fechamento"].iloc[-1])
    data_atual = pd.to_datetime(df_btc["t_abert"].iloc[-1])
    df_12m = df_btc[pd.to_datetime(df_btc["t_abert"]) >= data_atual - pd.Timedelta(days=365)]
    if df_12m.empty:
        return None, None, None
    ret_12m = (preco_atual / float(df_12m["fechamento"].iloc[0]) - 1) * 100
    regime = "BULL" if ret_12m > 25 else ("BEAR" if ret_12m < -25 else "LATERAL")
    return regime, ret_12m, preco_atual


def secao_modo_alocacao():
    regime_btc, ret_btc_12m, preco_btc = regime_btc_atual(VERSAO)
    resumo_filter = carregar_resumo_filter()
    modos_margem = carregar_modos_margem()

    st.subheader("🧭 Modo de Alocação — Filtro de Regime BTC")
    st.caption(
        "Com base no retorno do BTC nos últimos 12 meses, ajuste o tamanho das suas posições. "
        "Métricas históricas: re-backtest real com sizes dinâmicos por trade (período de desenvolvimento)."
    )
    if regime_btc is None:
        st.info("Não foi possível calcular o regime BTC atual (sem conexão com a API).")
        return

    cor_regime = {"BULL": "#22c55e", "BEAR": "#ef4444"}.get(regime_btc, "#f59e0b")
    emoji_regime = {"BULL": "🐂", "BEAR": "🐻"}.get(regime_btc, "↔️")
    st.markdown(
        f'<div style="background:{cor_regime}18; border:1px solid {cor_regime}55; border-left:4px solid {cor_regime}; '
        f'border-radius:10px; padding:10px 16px; margin-bottom:12px;">'
        f'<span style="font-size:1.05rem; font-weight:700; color:{cor_regime};">{emoji_regime} Regime BTC atual: {regime_btc}</span>'
        f'<br><span style="font-size:0.85rem; color:#94a3b8;">BTC/USDT ${preco_btc:,.0f} · retorno 12m: {ret_btc_12m:+.1f}%</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    def _linha_media(nome):
        if resumo_filter is None or nome not in resumo_filter.index:
            return "Execute backtest_btc_filter_v4.py para ver métricas históricas."
        r = resumo_filter.loc[nome]
        return (f"<b>Média por ativo:</b> {r['ret_anual_pct']:+.1f}%/ano · DD {r['drawdown_pct']:.1f}% "
                f"· Calmar {r['calmar']:.3f}")

    def _linha_carteira(nome):
        if modos_margem is None or nome not in modos_margem.index:
            return ""
        r = modos_margem.loc[nome]
        cor = "#22c55e"
        if str(r["exige_margem"]).strip().lower() == "sim":
            cor = COR_DSR_ALERTA
            return (
                f'<div style="margin-top:8px; padding:6px 9px; border-radius:7px; background:{cor}22; '
                f'color:{cor}; border:1px solid {cor}55; font-size:0.76rem; font-weight:600;">'
                f'⚠️ EXIGE MARGEM em {r["pct_dias_acima_100"]:.0f}% do tempo — não é Spot puro.<br>'
                f'<span style="font-weight:500;">Carteira composta: {r["cagr_margem_pct"]:+.1f}%/ano com '
                f'margem; limitada a 100% do capital (Spot): <b>{r["cagr_spot_pct"]:+.1f}%/ano</b>, '
                f'DD {r["dd_spot_pct"]:.1f}%.</span></div>'
            )
        return (
            f'<div style="margin-top:8px; padding:6px 9px; border-radius:7px; background:{cor}18; '
            f'color:{cor}; border:1px solid {cor}44; font-size:0.76rem; font-weight:600;">'
            f'✅ Cabe em Spot puro (exposição mediana {r["exposicao_mediana"]:.0%}, nunca passa de 100%).<br>'
            f'<span style="font-weight:500;">Carteira composta: <b>{r["cagr_spot_pct"]:+.1f}%/ano</b>, '
            f'DD {r["dd_spot_pct"]:.1f}%.</span></div>'
        )

    modos = [
        ("🛡️ Defensivo", "DEFENSIVO", "Sempre usa 100% dos pesos calculados. Linha de base.", "#64748b"),
        ("⚖️ Filtrado", "FILTRADO", "Em anos BEAR do BTC, metade do capital fica em cash.", "#3b82f6"),
        ("🚀 Agressivo", "AGRESSIVO", "Mais retorno em BULL, proteção em BEAR. Drawdown maior.", "#a855f7"),
    ]
    multi_recomendado = {
        "BULL":    {"DEFENSIVO": "1×", "FILTRADO": "1×", "AGRESSIVO": "<b>1.5×</b> ← agora"},
        "LATERAL": {"DEFENSIVO": "1×", "FILTRADO": "1×", "AGRESSIVO": "1×"},
        "BEAR":    {"DEFENSIVO": "1×", "FILTRADO": "<b>0.5×</b> ← agora", "AGRESSIVO": "<b>0.5×</b> ← agora"},
    }
    for col, (nome, chave, desc, cor) in zip(st.columns(3), modos):
        with col:
            st.markdown(
                f'<div style="background:{cor}14; border:1px solid {cor}44; border-radius:10px; padding:10px 14px;">'
                f'<div style="font-weight:700; color:{cor}; font-size:0.95rem;">{nome}</div>'
                f'<div style="font-size:0.78rem; color:#cbd5e1; margin-top:4px;">{desc}</div>'
                f'<div style="font-size:0.78rem; color:#94a3b8; margin-top:6px;">{_linha_media(chave)}</div>'
                f'{_linha_carteira(chave)}'
                f'<div style="margin-top:8px; font-size:0.85rem; color:#f1f5f9;"><b>Agora ({regime_btc}):</b> '
                f'{multi_recomendado[regime_btc][chave]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.info(
        "**Por que cada modo tem dois números?** São duas perguntas diferentes. **Média por ativo** = "
        "cada uma das 22 moedas simulada sozinha, e a média dos resultados ponderada pelo peso. O "
        "drawdown é alto porque cada moeda isolada cai muito. **Carteira composta** = as 8 veteranas "
        "juntas numa carteira só, compondo dia a dia (ago/2020 a ago/2025). O drawdown é menor porque "
        "as moedas não caem todas ao mesmo tempo. A carteira composta é a mais próxima do que você "
        "teria na conta."
    )
    if modos_margem is not None and "AGRESSIVO" in modos_margem.index:
        ag, de = modos_margem.loc["AGRESSIVO"], modos_margem.loc["DEFENSIVO"]
        st.warning(
            f"**Atenção ao modo Agressivo:** o multiplicador de 1.5× em BULL faz a exposição agregada "
            f"do portfólio passar de 100% do capital em **{ag['pct_dias_acima_100']:.0f}% do tempo**. "
            f"Isso exige margem (empréstimo), que a conta Spot da Binance não oferece. Respeitando o "
            f"limite de 100% do capital, o Agressivo rende **{ag['cagr_spot_pct']:+.1f}%/ano em vez de "
            f"{ag['cagr_margem_pct']:+.1f}%**, praticamente empatado com o Defensivo "
            f"({de['cagr_spot_pct']:+.1f}%). Custo de juros e risco de liquidação da margem **não "
            f"estão modelados em nenhum número deste app**. Ver `RELATORIO_RIQUEZA_TERMINAL.md`."
        )


# ============================================================================
# 🔬 VALIDAÇÃO
# ============================================================================
def secao_validacao():
    st.header("📊 Validação Multi-Regime — o que a estratégia faz bem (e o que não faz)")
    df_wf = carregar_csv("walkforward_robusta_v4_janelas.csv")
    if df_wf is None:
        st.info("Rode `python walkforward_robusta_v4.py` para gerar os dados de validação.")
        return
    n_total = len(df_wf)
    st.caption(
        f"Walk-forward sobre {n_total} janelas anuais × ativo no período de desenvolvimento (até "
        f"{DATA_CORTE_HOLDOUT:%d/%m/%Y}), receita robusta fixa, sem re-otimização por janela."
    )
    col_r1, col_r2, col_r3, col_r4 = st.columns(4)
    col_r1.metric("Janelas analisadas", f"{n_total}")
    col_r2.metric("Janelas positivas", f"{float((df_wf['Retorno_%'] > 0).mean() * 100):.0f}%")
    col_r3.metric("Bate Buy&Hold", f"{float((df_wf['Retorno_%'] > df_wf['BH_%']).mean() * 100):.0f}%")
    col_r4.metric("Retorno mediano/janela", f"{float(df_wf['Retorno_%'].median()):+.1f}%")

    st.markdown("#### Por regime de mercado (regime do ativo naquele ano)")
    linhas_regime, por_regime = [], {}
    for regime in ["BULL", "LATERAL", "BEAR"]:
        sub = df_wf[df_wf["Regime"] == regime]
        if sub.empty:
            continue
        rv, bv = sub["Retorno_%"].values, sub["BH_%"].values
        por_regime[regime] = {"est": float(np.median(rv)), "bh": float(np.median(bv)),
                              "bate": float(np.mean(rv > bv) * 100), "n": len(rv)}
        linhas_regime.append({
            "Regime": regime, "Janelas": len(rv),
            "Strat. mediana": f"{float(np.median(rv)):+.1f}%",
            "B&H mediana": f"{float(np.median(bv)):+.1f}%",
            "% positivas": f"{float(np.mean(rv > 0) * 100):.0f}%",
            "Bate B&H": f"{float(np.mean(rv > bv) * 100):.0f}%",
        })
    if linhas_regime:
        st.dataframe(pd.DataFrame(linhas_regime).set_index("Regime"), width="stretch")

    leitura = []
    if "BEAR" in por_regime:
        r = por_regime["BEAR"]
        leitura.append(f"- 🐻 **BEAR (queda):** estratégia {r['est']:+.0f}% (mediana) contra "
                       f"{r['bh']:+.0f}% do B&H; bate o B&H em {r['bate']:.0f}% das janelas. É onde ela "
                       f"mais protege.")
    if "LATERAL" in por_regime:
        r = por_regime["LATERAL"]
        leitura.append(f"- ↔️ **LATERAL:** estratégia {r['est']:+.0f}% contra {r['bh']:+.0f}% do B&H; "
                       f"bate em {r['bate']:.0f}% das janelas.")
    if "BULL" in por_regime:
        r = por_regime["BULL"]
        leitura.append(f"- 🐂 **BULL (alta):** estratégia {r['est']:+.0f}% contra {r['bh']:+.0f}% do "
                       f"B&H; bate em só {r['bate']:.0f}% das janelas. A saída por cruzamento corta os "
                       f"vencedores cedo: é a principal fraqueza.")
    st.markdown("**Leitura (calculada dos dados acima):**\n" + "\n".join(leitura))

    st.markdown(
        """
**Resultados das pesquisas complementares** (rodadas em jul–ago/2026, período de desenvolvimento,
números fixos dos relatórios):

- **Trailing stop:** testado nas mesmas janelas, **piorou** em todos os regimes (−50pp em BULL). O
  cruzamento + stop ATR fixo é a saída mais eficiente para esses parâmetros.
- **Filtro de regime BTC:** sizes dinâmicos por trade (1.5× BULL / 1× LATERAL / 0.5× BEAR BTC).
  Agressivo: +60.7%→+79.7%/ano, Calmar 1.158→1.316, mas exige margem (ver aba Portfólio).
- **Vol-targeting:** arriscar uma fração fixa R do capital por trade. R=15% melhora Calmar de
  1.158→1.314 com +61.4%/ano; drawdown cai de 62.3% para 55.5%.
- **Slippage realista:** saídas de stop com slippage extra (5% do ATR nas líquidas, 25% nas
  ilíquidas) custam −3.1pp/ano. A proteção em BEAR se sustenta: a estratégia sai geralmente por
  cruzamento (antes do crash), não por stop.
        """.strip()
    )

    df_front = carregar_fronteira()
    if df_front is not None:
        st.markdown("#### Fronteira de modos — resumo comparativo")
        notas = {
            "BTC-Agressivo":    "↑↑ retorno · ↑↑ DD · leverage 1.5× em BULL BTC",
            "Vol-target R=15%": "= retorno · ↓↓ DD · melhor Sharpe · sizing pelo ATR",
            "BTC-Filtrado":     "↑ retorno moderado · ↓ DD · proteção em BEAR BTC sem leverage",
            "Baseline":         "referência — 100% do capital em cada trade",
        }
        df_disp = df_front.copy()
        df_disp["Ret/ano"] = df_disp["ret_anual_pct"].apply(lambda x: f"+{x:.1f}%")
        df_disp["Drawdown"] = df_disp["drawdown_pct"].apply(lambda x: f"{x:.1f}%")
        df_disp["Calmar"] = df_disp["calmar"].apply(lambda x: f"{x:.3f}")
        df_disp["Sharpe"] = df_disp["sharpe"].apply(lambda x: f"{x:.3f}")
        df_disp["Perfil"] = df_disp["Estrategia"].map(notas).fillna("")
        st.dataframe(
            df_disp[["Estrategia", "Ret/ano", "Drawdown", "Calmar", "Sharpe", "Perfil"]].set_index("Estrategia"),
            width="stretch",
        )
        st.caption(
            "Vol-target e BTC-filter testados separadamente — não existe backtest da combinação dos dois. "
            "Período de desenvolvimento, médias ponderadas pelos 22 ativos. "
            "BTC-Agressivo e Vol-target R=15% têm Calmar quase idêntico mas perfis opostos: "
            "um dá mais retorno com mais DD, o outro dá o mesmo retorno com menos DD."
        )

    st.markdown("#### Distribuição dos retornos anuais (estratégia vs B&H)")
    # rótulos em inglês: o usuário tira print destes gráficos pra publicar
    df_melt = (df_wf[["Retorno_%", "BH_%"]]
               .rename(columns={"Retorno_%": "Strategy", "BH_%": "Buy & Hold"})
               .melt(var_name="Series", value_name="Return"))
    if not df_melt.empty:
        hist = (
            alt.Chart(df_melt)
            .mark_bar(opacity=0.65, binSpacing=1)
            .encode(
                x=alt.X("Return:Q", bin=alt.Bin(maxbins=40), title="Annual return (%)"),
                y=alt.Y("count():Q", title="Asset-year windows"),
                color=alt.Color("Series:N",
                                scale=alt.Scale(domain=["Strategy", "Buy & Hold"], range=[COR_ESTRATEGIA, COR_BUYHOLD]),
                                legend=alt.Legend(title=None, orient="top")),
                tooltip=[alt.Tooltip("Series:N", title="Series"), alt.Tooltip("count():Q", title="Windows")],
            )
            .properties(height=230, background="transparent",
                        title=alt.TitleParams(f"Annual return distribution — {n_total} asset-year windows",
                                              color="#e2e8f0", fontSize=13, fontWeight=600,
                                              anchor="start", offset=8))
            .configure_axis(gridColor="#262d3d", labelColor="#9aa4b8", titleColor="#9aa4b8")
            .configure_view(strokeWidth=0)
            .configure_legend(labelColor="#e5e7eb", labelFontSize=12)
        )
        st.altair_chart(hist, width="stretch")


# ============================================================================
# 🕶️ SOMBRA & EXECUÇÃO
# ============================================================================
SLIPPAGE_MAX_ASSUMIDO_PCT = 0.175   # o maior slippage por lado que o backtest assume
DISPONIBILIDADE_MINIMA = 0.95       # Fase 5 com dinheiro real exige o robô de pé


def secao_sombra():
    st.header("🕶️ Sombra & Execução — Fase 4")
    st.caption(
        "A Sombra roda de hora em hora junto com o monitor de e-mail. Ela decide de verdade, com os "
        "sinais de verdade, e registra a ordem que **teria** enviado, sem enviar nada. É ela que "
        "mostra se dá pra ir pra Fase 5 (dinheiro real)."
    )
    r = carregar_sombra(VERSAO)
    if r is None:
        st.info("Os dados da Sombra só existem no PC onde ela roda (os arquivos não vão pro Git). "
                "Abra o Radar pelo atalho no seu computador pra ver esta aba.")
        return
    if "erro" in r:
        st.warning(f"Não consegui ler os arquivos da Sombra agora ({r['erro']}). Tente de novo em instantes.")
        return

    disp = r["disponibilidade"]
    ex = r["execucao"]
    n_fech = len(r["fechados"])

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Resultado (com custos)", f"${r['resultado_usd']:+.2f}",
              delta=f"{r['resultado_pct']:+.2f}% sobre ${r['capital']:.0f}")
    k2.metric("Trades fechados", f"{r['acertos']}/{n_fech} positivos" if n_fech else "0",
              help=f"Fechados: ${r['resultado_fechados_usd']:+.2f} · abertos: ${r['resultado_abertos_usd']:+.2f}")
    k3.metric("PC ligado", f"{disp['pct']:.0%}" if disp else "—",
              help=f"{disp['rodadas']} de {disp['esperadas']} rodadas horárias aconteceram" if disp else None)
    k4.metric("Custo de execução", f"{ex.get('custo_execucao_no_horario_pct', 0):+.3f}%",
              help="Quanto, em média, o preço real da decisão foi pior que o preço do backtest, nas "
                   "decisões tomadas no horário (até 1h depois do sinal). Positivo = pior.")

    # ---- semáforo da Fase 5 ----
    st.markdown("#### 🚦 Pronto pra Fase 5 (dinheiro real)?")
    # início = primeira rodada da sombra (relógio do PC); sem log, a 1ª decisão (UTC)
    inicio = disp["inicio"] if disp else (brt(r["inicio"]) if r["inicio"] is not None else None)
    dias = (datetime.now() - inicio).days if inicio is not None else 0
    criterios = [
        (dias >= 28, f"Tempo mínimo de 4 semanas de Sombra: **{dias} dias** desde "
                     f"{inicio:%d/%m/%Y}" if inicio is not None else "Sem data de início"),
        ((ex.get("custo_execucao_no_horario_pct") or 0) <= SLIPPAGE_MAX_ASSUMIDO_PCT,
         f"Execução fiel ao backtest: custo médio de **{ex.get('custo_execucao_no_horario_pct', 0):+.3f}%** "
         f"por decisão no horário (limite: o slippage que o backtest assume, até {SLIPPAGE_MAX_ASSUMIDO_PCT}%)"),
        (bool(disp) and disp["pct"] >= DISPONIBILIDADE_MINIMA,
         f"Robô sempre de pé: **{disp['pct']:.0%}** das horas (mínimo {DISPONIBILIDADE_MINIMA:.0%}). "
         f"Com dinheiro real, PC desligado = venda que não acontece" if disp else "Sem dados de disponibilidade"),
    ]
    for ok, texto in criterios:
        st.markdown(f"{'✅' if ok else '❌'} {texto}")
    if all(ok for ok, _ in criterios):
        st.success("Todos os critérios atendidos: dá pra conversar sobre a Fase 5.")
    else:
        st.error("Ainda não. Os critérios em ❌ precisam ser resolvidos antes de colocar dinheiro real.")

    # ---- disponibilidade por dia ----
    if disp:
        st.markdown("#### 🖥️ Quanto o PC ficou ligado, dia a dia")
        horas = pd.Series(1, index=pd.DatetimeIndex(disp["horas_com_rodada"]))
        por_dia = horas.groupby(horas.index.normalize()).sum()
        d_ini, d_fim = pd.Timestamp(disp["inicio"]), pd.Timestamp(disp["fim"])
        dias_idx = pd.date_range(d_ini.normalize(), d_fim.normalize(), freq="D")
        esperadas = []
        for d in dias_idx:
            ini_d = max(d, d_ini)
            fim_d = min(d + pd.Timedelta(hours=23), d_fim)
            esperadas.append(int((fim_d - ini_d) / pd.Timedelta(hours=1)) + 1)
        df_up = pd.DataFrame({"Dia": dias_idx, "Esperadas": esperadas,
                              "Rodadas": por_dia.reindex(dias_idx).fillna(0).astype(int).values})
        df_up["Ligado (%)"] = (df_up["Rodadas"] / df_up["Esperadas"] * 100).clip(upper=100)
        df_up["Faixa"] = np.where(df_up["Ligado (%)"] >= 95, "ok", np.where(df_up["Ligado (%)"] >= 60, "parcial", "ruim"))
        df_up["Data"] = df_up["Dia"].dt.strftime("%d/%m")
        graf = (
            alt.Chart(df_up).mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
            .encode(
                x=alt.X("Data:O", sort=None, title=None, axis=alt.Axis(labelAngle=-45)),
                y=alt.Y("Ligado (%):Q", scale=alt.Scale(domain=[0, 100]), title="% das horas"),
                color=alt.Color("Faixa:N", legend=None, scale=alt.Scale(
                    domain=["ok", "parcial", "ruim"], range=[COR_ALTA, COR_DSR_ALERTA, COR_BAIXA])),
                tooltip=[alt.Tooltip("Dia:T", format="%d/%m/%Y"), "Rodadas:Q", "Esperadas:Q",
                         alt.Tooltip("Ligado (%):Q", format=".0f")],
            )
            .properties(height=200, background="transparent")
            .configure_axis(gridColor="#262d3d", labelColor="#9aa4b8", titleColor="#9aa4b8")
            .configure_view(strokeWidth=0)
        )
        st.altair_chart(graf, width="stretch")
        grandes = [b for b in disp["buracos"] if b["horas"] >= 3]
        if grandes:
            with st.expander(f"Períodos desligado (3h ou mais): {len(grandes)}"):
                st.dataframe(pd.DataFrame([{
                    "De": b["de"], "Até": b["ate"] + pd.Timedelta(minutes=59), "Horas": b["horas"]
                } for b in grandes]).iloc[::-1], hide_index=True, width="stretch",
                    column_config={"De": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm"),
                                   "Até": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})

    # ---- posições abertas ----
    st.markdown(f"#### 📂 Posições abertas na Sombra: {len(r['abertos'])}")
    if r["abertos"]:
        df_a = pd.DataFrame([{
            "Ativo": t["ativo"], "Desde": brt(t["compra"]), "Valor ($)": t["valor_usd"],
            "Preço compra": t["preco_compra"], "Preço agora": t["preco_atual"],
            "Stop": t["stop"], "Resultado (%)": round(t["resultado_pct"], 2),
            "Resultado ($)": round(t["resultado_usd"], 2),
        } for t in r["abertos"]]).sort_values("Desde", ascending=False)
        st.dataframe(df_a.style.map(_cor_resultado, subset=["Resultado (%)", "Resultado ($)"]),
                     hide_index=True, width="stretch", column_config={
                         "Desde": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm"),
                         "Valor ($)": st.column_config.NumberColumn(format="$%.2f"),
                         "Preço compra": st.column_config.NumberColumn(format="%.6g"),
                         "Preço agora": st.column_config.NumberColumn(format="%.6g"),
                         "Stop": st.column_config.NumberColumn(format="%.6g"),
                         "Resultado (%)": st.column_config.NumberColumn(format="%+.2f%%"),
                         "Resultado ($)": st.column_config.NumberColumn(format="$%+.2f"),
                     })
    else:
        st.caption("Nenhuma posição aberta agora.")

    # ---- backtest x sombra, trade a trade ----
    st.markdown(f"#### ⚖️ Backtest × Sombra, trade a trade: {n_fech} fechados")
    if r["fechados"]:
        df_f = pd.DataFrame([{
            "Ativo": t["ativo"], "Compra": brt(t["compra"]), "Venda": brt(t["venda"]),
            "Valor ($)": t["valor_usd"],
            "Backtest (%)": round(t["resultado_bt_pct"], 2), "Sombra (%)": round(t["resultado_pct"], 2),
            "Diferença (pp)": round(t["resultado_pct"] - t["resultado_bt_pct"], 2),
            "Resultado ($)": round(t["resultado_usd"], 2),
        } for t in r["fechados"]]).sort_values("Venda", ascending=False)
        st.dataframe(df_f.style.map(_cor_resultado, subset=["Backtest (%)", "Sombra (%)", "Diferença (pp)", "Resultado ($)"]),
                     hide_index=True, width="stretch",
                     height=min(460, 38 + 35 * len(df_f)), column_config={
                         "Compra": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm"),
                         "Venda": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm"),
                         "Valor ($)": st.column_config.NumberColumn(format="$%.2f"),
                         "Backtest (%)": st.column_config.NumberColumn(format="%+.2f%%"),
                         "Sombra (%)": st.column_config.NumberColumn(format="%+.2f%%"),
                         "Diferença (pp)": st.column_config.NumberColumn(format="%+.2f"),
                         "Resultado ($)": st.column_config.NumberColumn(format="$%+.2f"),
                     })
        dif = df_f["Diferença (pp)"]
        st.caption(
            f"Diferença média: {dif.mean():+.2f}pp por trade (positivo = a Sombra foi melhor que o "
            f"backtest). Backtest = abertura do candle do sinal; Sombra = preço real no instante da "
            f"decisão. Os dois já descontam taxa + slippage do grupo do ativo."
        )

    # ---- qualidade da execução ----
    with st.expander(f"🔎 Todas as {ex.get('n', 0)} decisões registradas (preço, atraso, desvio)"):
        st.caption(
            f"Atraso mediano: {ex.get('atraso_mediano_h', 0) * 60:.0f} min · {ex.get('atrasadas', 0)} "
            f"decisões com mais de 1h de atraso (PC desligado na hora do sinal) · custo médio "
            f"considerando também as atrasadas: {ex.get('custo_execucao_medio_pct', 0):+.3f}% · "
            f"exposição máxima: {ex.get('exposicao_max_pct') or 0:.0f}% do capital."
        )
        reg = r["registro"]
        df_r = pd.DataFrame({
            "Quando": reg["data_hora"].map(brt), "Ativo": reg["ativo"],
            "Ação": reg["acao"].map({"BUY": "🟢 Compra", "SELL": "🔴 Venda"}),
            "Preço backtest": reg["preco_backtest"], "Preço real": reg["preco_agora"],
            "Desvio (%)": reg["desvio_pct"], "Atraso (h)": reg["atraso_h"],
            "Valor ($)": reg["valor_usd"],
        }).iloc[::-1]
        st.dataframe(df_r, hide_index=True, width="stretch", column_config={
            "Quando": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm"),
            "Preço backtest": st.column_config.NumberColumn(format="%.6g"),
            "Preço real": st.column_config.NumberColumn(format="%.6g"),
            "Desvio (%)": st.column_config.NumberColumn(format="%+.2f%%"),
            "Atraso (h)": st.column_config.NumberColumn(format="%.1f"),
            "Valor ($)": st.column_config.NumberColumn(format="$%.2f"),
        })


# ============================================================================
# Navegação: só a seção escolhida é calculada e desenhada (abre mais rápido)
# ============================================================================
SECOES = {
    "📡 Radar": secao_radar,
    "📊 Portfólio": secao_portfolio,
    "🕶️ Sombra & Execução": secao_sombra,
    "🔬 Validação": secao_validacao,
}
escolha = st.segmented_control("Seção", list(SECOES), default="📡 Radar", key="nav",
                               label_visibility="collapsed") or "📡 Radar"
st.divider()
SECOES[escolha]()
