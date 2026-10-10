# -*- coding: utf-8 -*-
# CONFIG COMPARTILHADO - V4 (Calmar + Robustez + custos por liquidez + DSR)
# ----------------------------------------------------------------------------
# Módulo de configuração compartilhado por otimizador_v4.py, portfolio_v4.py e
# holdout_v4.py. Não altera nada do v2, do v3 nem do Código Ômega original.
#
# A lista de 22 ativos e os intervals abaixo foram LIDOS (sem alterar nada) de
#   sinalizador interface/VERSÃO DEFINITIVA E OTIMIZADA/frontend/index.html
#   (const STRATEGY_PORTFOLIO), que é a versão mais recente/completa do
#   portfólio do Código Ômega. Cada ativo usa o MESMO timeframe de produção
#   definido lá (ex: BTC em 6h, ETH em 8h) — não padronizamos tudo em 4h,
#   porque o objetivo é reotimizar o que está de fato em uso.
#
# ----------------------------------------------------------------------------
# SEPARAÇÃO DE DADOS (regra central do v4)
# ----------------------------------------------------------------------------
# Para cada ativo:
#   - "período de desenvolvimento": do início do histórico disponível daquele
#     ativo até 12 meses antes de hoje. TODO código de otimização/validação
#     (otimizador_v4.py, portfolio_v4.py) só pode enxergar este período.
#   - "período lacrado" (holdout): os últimos 12 meses. NINGUÉM além de
#     validar_holdout_final() (em holdout_v4.py) pode tocar nesses dados.
#     Esse script só deve ser executado quando o usuário pedir explicitamente
#     — ele tem uma trava por linha de comando (--eu-confirmo-holdout-final)
#     justamente para isso não acontecer sem querer.
#
# AVISO IMPORTANTE sobre o holdout (documentado aqui para quando chegarmos lá):
#   Os últimos 12 meses (2025-07 a 2026-07, a partir da data em que este
#   arquivo foi escrito) foram, no geral, um período de mercado de
#   baixa/lateralização para a maior parte das criptos do portfólio (ver
#   Lucro_BuyHold_TESTE_% negativo ou fraco em várias janelas recentes de
#   walkforward_janelas_TODOS_ATIVOS.csv, ex: BTC 2026 parcial -27,56%, XRP
#   2025 -11,58%, FET 2025 -84,39%). Ou seja: o holdout testa principalmente
#   a CAPACIDADE DE PROTEÇÃO DE CAPITAL da estratégia (perder pouco/nada
#   quando o mercado não sobe), e não a captura de alta — isso já foi testado
#   nas janelas de walk-forward antigas do v2 (várias com buy&hold bem
#   positivo, ex: SOL 2023 +920%, BNB 2021 +1268%, XRP 2021 +277%). Ao
#   reportar o resultado do holdout, sempre mostrar as duas metades lado a
#   lado: proteção em baixa (holdout) + captura em alta (janelas antigas).

import os
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import pandas as pd
from binance.client import Client

CAPITAL_INICIAL = 1000.0
ANOS_DE_DADOS_BACKTEST = 8
CACHE_DIR = "cache_dados"
CACHE_VALIDADE_HORAS = 24

HOLDOUT_MESES = 12

# Corte REAL do holdout lacrado: o instante em que holdout_v4.py rodou (2026-07-28)
# menos 12 meses. Reconstruído dos inícios em holdout_v4_resultado.csv (4h começa
# em 28/07, 6h/8h/12h em 29/07 => corte entre 18:00 e 20:00 UTC). Tudo depois disto
# nunca foi visto na derivação da RECEITA_ROBUSTA. `separar_periodos(..., corte=)`
# usa isto em vez da janela "últimos 12 meses a partir de hoje", que anda com o
# calendário e passaria a chamar de "desenvolvimento" dado que já é fora da amostra.
DATA_CORTE_HOLDOUT = pd.Timestamp("2025-07-28 20:00")

TIMEFRAME_MAP = {
    "4h": Client.KLINE_INTERVAL_4HOUR,
    "6h": Client.KLINE_INTERVAL_6HOUR,
    "8h": Client.KLINE_INTERVAL_8HOUR,
    "12h": Client.KLINE_INTERVAL_12HOUR,
    "1d": Client.KLINE_INTERVAL_1DAY,
}

# candles por dia de calendário, usado pra resample de retornos diários (Sharpe/DSR)
CANDLES_POR_DIA = {
    "4h": 6,
    "6h": 4,
    "8h": 3,
    "12h": 2,
    "1d": 1,
}

# ----------------------------------------------------------------------------
# OS 22 ATIVOS DO PORTFÓLIO (lidos do frontend, ver comentário acima) + o
# interval de produção de cada um.
# ----------------------------------------------------------------------------
ATIVOS_PORTFOLIO_V4 = {
    "1MBABYDOGEUSDT": "4h",
    "API3USDT": "12h",
    "BNBUSDT": "12h",
    "BONKUSDT": "4h",
    "BTCUSDT": "6h",
    "DOGEUSDT": "8h",
    "ETHUSDT": "8h",
    "FETUSDT": "4h",
    "FLOKIUSDT": "4h",
    "HBARUSDT": "8h",
    "IMXUSDT": "6h",
    "INJUSDT": "12h",
    "LINKUSDT": "6h",
    "PENGUUSDT": "4h",
    "PEPEUSDT": "4h",
    "RENDERUSDT": "4h",
    "SOLUSDT": "8h",
    "SUIUSDT": "6h",
    "TAOUSDT": "4h",
    "TRXUSDT": "8h",
    "XRPUSDT": "8h",
    "ZECUSDT": "4h",
}

# ----------------------------------------------------------------------------
# CUSTOS POR LIQUIDEZ
# ----------------------------------------------------------------------------
# "líquidos": BTC/ETH/BNB/SOL/XRP + outros bluechips com livro de ofertas
# profundo na Binance (DOGE, TRX e LINK entram aqui pelo volume/cap, mesmo
# DOGE tendo origem de memecoin). Taxa 0,1% + slippage 0,05% (igual ao que já
# usávamos em backtest_breakout_volume.py).
#
# "menos líquidos": memecoins (PEPE, BONK, FLOKI, 1MBABYDOGE, PENGU) e o
# resto dos altcoins de menor capitalização da lista (API3, FET, HBAR, IMX,
# INJ, RENDER, SUI, TAO, ZEC). Taxa 0,1% + slippage adicional de 0,15-0,2%
# (usamos o ponto médio, 0,175%, como valor único — ajuste
# SLIPPAGE_MENOS_LIQUIDO abaixo se quiser testar as pontas do intervalo).
#
# Isso é uma classificação heurística — se você discordar de algum ativo
# específico, é só mover o símbolo entre os dois sets abaixo.
ATIVOS_LIQUIDOS = {
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "TRXUSDT",
    "LINKUSDT",
}
ATIVOS_MENOS_LIQUIDOS = set(ATIVOS_PORTFOLIO_V4.keys()) - ATIVOS_LIQUIDOS

TAXA_CORRETAGEM = 0.001  # 0,1% — igual para os dois grupos
SLIPPAGE_LIQUIDO = 0.0005  # 0,05%
SLIPPAGE_MENOS_LIQUIDO = 0.00175  # 0,175% (ponto médio de 0,15%-0,20%)


def classificar_liquidez(symbol: str) -> dict:
    """Retorna {'grupo':..., 'taxa':..., 'slippage':...} pro símbolo."""
    if symbol in ATIVOS_LIQUIDOS:
        return {"grupo": "liquido", "taxa": TAXA_CORRETAGEM, "slippage": SLIPPAGE_LIQUIDO}
    return {"grupo": "menos_liquido", "taxa": TAXA_CORRETAGEM, "slippage": SLIPPAGE_MENOS_LIQUIDO}


# ----------------------------------------------------------------------------
# RECEITA OURO ROBUSTA (fonte única da verdade) — a receita ÚNICA POR GRUPO que
# a produção (app_v4) usa nos sinais, gráficos e portfólio. Derivada em
# estrategia_ouro_v5.py + analise_padroes_profunda.py (moda do corte de 5% por
# Score_Robustez, no período de desenvolvimento). NÃO são os parâmetros
# otimizados por ativo (que se mostraram overfitting). Ver RELATORIO_ESTRATEGIA_OURO.md.
#   veterana = ativos líquidos/antigos (ATIVOS_LIQUIDOS); nova = o resto.
# ----------------------------------------------------------------------------
RECEITA_ROBUSTA = {
    "veterana": dict(media_rapida=5, media_lenta=100, media_filtro=50, atr_periodo=7, atr_multiplicador=6.0),
    "nova": dict(media_rapida=12, media_lenta=30, media_filtro=100, atr_periodo=20, atr_multiplicador=5.0),
}


def grupo_ouro(symbol: str) -> str:
    """Grupo da receita ouro robusta: 'veterana' (líquidas/antigas) ou 'nova'."""
    return "veterana" if symbol in ATIVOS_LIQUIDOS else "nova"


# ----------------------------------------------------------------------------
# DADOS
# ----------------------------------------------------------------------------
def carregar_dados(symbol: str, interval_str: str, max_idade_horas: float = None) -> pd.DataFrame:
    """Baixa (ou lê do cache) OHLC + timestamp pro símbolo/interval dados.

    Alguns provedores de nuvem (ex.: Streamlit Community Cloud, hospedado nos
    EUA) têm o IP bloqueado pela Binance ("restricted location"). Nesse caso,
    caímos de volta pro cache local mesmo que esteja velho — é a única fonte
    de dados disponível ali. Um cache inicial (22 ativos) vai versionado no
    repo justamente pra isso (ver cache_dados/ no .gitignore)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{symbol}_{interval_str}.parquet")

    def _ler_cache():
        try:
            df_cache = pd.read_parquet(cache_path)
            if "t_abert" in df_cache.columns:
                return df_cache
        except Exception:
            pass
        return None

    # `max_idade_horas` (opcional) liga o modo AO VIVO, usado pelo radar e pelo
    # monitor de e-mail. Sem ele, vale o CACHE_VALIDADE_HORAS de sempre (24h) --
    # os scripts de pesquisa/backtest continuam exatamente como eram.
    #
    # Por que existe: com 24h de validade, o radar mostrava o preco de ONTEM e o
    # monitor comparava sinais com dado de ontem (alerta ate ~24h atrasado).
    validade = CACHE_VALIDADE_HORAS if max_idade_horas is None else max_idade_horas

    def _atualizar_incremental(df_cache):
        """Busca SO os candles novos desde o ultimo do cache e mescla.

        Baixar 5 anos de novo a cada abertura leva minutos; isto e 1 requisicao
        pequena por ativo. O ultimo candle do cache estava incompleto quando foi
        salvo, entao a busca comeca NELE e ele e substituido pela versao atual --
        o ultimo candle devolvido e o que esta aberto agora, ou seja, o preco do
        momento. Devolve None se falhar (o chamador cai no download completo)."""
        try:
            ultimo = pd.Timestamp(df_cache["t_abert"].iloc[-1])
            inicio_ms = int(ultimo.value // 10**6)
            client = Client(ping=False)
            klines = client.get_historical_klines(symbol, TIMEFRAME_MAP[interval_str], inicio_ms)
            if not klines:
                return None
            cols = ["t_abert", "abertura", "maxima", "minima", "fechamento"]
            novo = pd.DataFrame([k[:5] for k in klines], columns=cols)
            for col in cols[1:]:
                novo[col] = pd.to_numeric(novo[col])
            novo["t_abert"] = pd.to_datetime(novo["t_abert"], unit="ms")
            base = df_cache.loc[df_cache["t_abert"] < novo["t_abert"].iloc[0], cols]
            df = pd.concat([base, novo], ignore_index=True)
            # mesma janela do download completo: sem isso o cache cresceria alem
            # de ANOS_DE_DADOS_BACKTEST e mudaria, em silencio, o historico que
            # os backtests leem do mesmo arquivo.
            corte = pd.Timestamp((datetime.now() - timedelta(days=ANOS_DE_DADOS_BACKTEST * 365)).date())
            df = df[df["t_abert"] >= corte].reset_index(drop=True)
            try:
                df.to_parquet(cache_path)
            except Exception as e:
                print(f"[AVISO] cache não salvo: {e}")
            return df
        except Exception as e:
            print(f"[AVISO] {symbol} {interval_str}: atualização incremental falhou ({e}) — tentando download completo")
            return None

    if os.path.exists(cache_path):
        idade_horas = (datetime.now().timestamp() - os.path.getmtime(cache_path)) / 3600
        if idade_horas < validade:
            df_cache = _ler_cache()
            if df_cache is not None:
                return df_cache
        if max_idade_horas is not None:
            df_cache = _ler_cache()
            if df_cache is not None and len(df_cache):
                df_novo = _atualizar_incremental(df_cache)
                if df_novo is not None:
                    return df_novo

    try:
        client = Client(ping=False)
        interval_val = TIMEFRAME_MAP[interval_str]
        start_date = (
            datetime.now() - timedelta(days=ANOS_DE_DADOS_BACKTEST * 365)
        ).strftime("%d %b, %Y")
        klines = client.get_historical_klines(symbol, interval_val, start_date)
        if not klines:
            raise ValueError("Binance devolveu 0 candles")
        df = pd.DataFrame(
            klines,
            columns=[
                "t_abert",
                "abertura",
                "maxima",
                "minima",
                "fechamento",
                "vol",
                "t_fech",
                "q_vol",
                "n_trades",
                "tk_b",
                "tk_q",
                "ignore",
            ],
        )
        for col in ["abertura", "maxima", "minima", "fechamento"]:
            df[col] = pd.to_numeric(df[col])
        df["t_abert"] = pd.to_datetime(df["t_abert"], unit="ms")
        df = df[["t_abert", "abertura", "maxima", "minima", "fechamento"]]
        try:
            df.to_parquet(cache_path)
        except Exception as e:
            print(f"[AVISO] cache não salvo: {e}")
        return df
    except Exception as e:
        print(f"[ERRO] {symbol} {interval_str}: {e} — tentando cache (mesmo velho)")
        df_cache = _ler_cache()
        if df_cache is not None:
            return df_cache
        return pd.DataFrame()


def separar_periodos(datas: pd.Series, hoje: datetime = None, corte=None) -> dict:
    """Divide a série de timestamps em período de desenvolvimento (tudo menos
    os últimos HOLDOUT_MESES) e período lacrado (holdout). Retorna índices e
    datas de corte. `hoje` é injetável só pra testes; em uso normal usa
    datetime.now(). `corte` (ex.: DATA_CORTE_HOLDOUT) fixa a data de corte e
    ignora `hoje` — o holdout passa a crescer com o tempo em vez de andar."""
    datas = datas.reset_index(drop=True).astype("datetime64[ns]")
    n_total = len(datas)
    if corte is not None:
        corte_data = pd.Timestamp(corte).as_unit("ns")
    else:
        if hoje is None:
            hoje = datetime.now()
        corte_data = pd.Timestamp(hoje).as_unit("ns") - relativedelta(months=HOLDOUT_MESES)
    idx_corte = int(datas.searchsorted(corte_data))
    idx_corte = max(0, min(idx_corte, n_total))
    return {
        "idx_dev_fim": idx_corte,
        "idx_holdout_fim": n_total,
        "data_corte": corte_data,
        "dev_inicio": datas.iloc[0] if n_total > 0 else None,
        "dev_fim": datas.iloc[idx_corte - 1] if idx_corte > 0 else None,
        "holdout_inicio": datas.iloc[idx_corte] if idx_corte < n_total else None,
        "holdout_fim": datas.iloc[-1] if n_total > 0 else None,
    }
