# -*- coding: utf-8 -*-
# DAYTRADE CORE - sinais + simulacao de posicao das 3 candidatas (Fase 1)
# ----------------------------------------------------------------------------
# Modulo NOVO, paralelo a estrategia_core.py -- NAO importa nem edita nada do
# v4. Mesmo estilo (funcoes puras, so numpy, sem I/O, sem custos, sem
# look-ahead), mas reimplementado do zero porque as candidatas de day-trade
# precisam de stop%/alvo%/time-stop, que o motor do v4 (ATR fixo + cruzamento
# contrario) nao tem -- e estrategia_core.py e off-limits pra edicao.
#
# Convencao de nao-look-ahead (identica a estrategia_core.py): as funcoes
# calcular_sinais_* devolvem, no indice i, a condicao usando dados disponiveis
# ATE o fechamento do candle i. Quem consome (simular_posicao_daytrade) so age
# no candle i usando sinais_compra[i-1]/sinais_venda[i-1] -- ou seja, o sinal
# fechado no candle anterior dispara a ordem na ABERTURA do candle seguinte.
# O calculo dos INDICADORES (RSI, Bollinger, EMA, Donchian, volume) fica fora
# daqui (em daytrade_backtest.py, no mesmo espirito de montar_df_fast no v4) --
# este modulo so consome arrays ja prontos, o que mantem os testes simples
# (arrays sinteticos, sem precisar recalcular indicador dentro do teste).
#
# Coberto por tests/test_daytrade_core.py.

import numpy as np
import pandas as pd


# ----------------------------------------------------------------------------
# CANDIDATA 1 -- REVERSAO A MEDIA
# ----------------------------------------------------------------------------
def calcular_sinais_mean_reversion(fechamento, rsi, banda_inferior, media_reversao,
                                    rsi_entrada=30.0):
    """Compra quando RSI < rsi_entrada OU fechamento fura a banda de Bollinger
    inferior (duas leituras independentes de 'sobrevenda' -- ou uma, ou outra,
    ja conta). So lado comprado (Spot nao permite short) -- limitacao
    estrutural, documentada no relatorio.

    Venda 'natural': fechamento alcanca a media de reversao (SMA central das
    bandas) -- a tese de reversao se realizou. Isso e ADICIONAL ao alvo%/stop%/
    time-stop aplicados em simular_posicao_daytrade, nao substitui."""
    fechamento = np.asarray(fechamento, dtype=float)
    rsi = np.asarray(rsi, dtype=float)
    banda_inferior = np.asarray(banda_inferior, dtype=float)
    media_reversao = np.asarray(media_reversao, dtype=float)
    compra = (rsi < rsi_entrada) | (fechamento < banda_inferior)
    venda = fechamento > media_reversao
    return compra, venda


# ----------------------------------------------------------------------------
# CANDIDATA 2 -- MOMENTUM / ROMPIMENTO
# ----------------------------------------------------------------------------
def calcular_sinais_momentum(m_rapida, m_lenta, m_filtro, fechamento):
    """Cruzamento de medias rapida/lenta pra cima, com filtro de tendencia
    (fechamento acima de uma media mais longa, ex. no 1h). Mesmo *formato* do
    calcular_sinais do v4 (cruzamento + filtro), reimplementado aqui porque o
    horizonte curto pede stop%/alvo%/time-stop em vez de ATR fixo."""
    m_rapida = np.asarray(m_rapida, dtype=float)
    m_lenta = np.asarray(m_lenta, dtype=float)
    m_filtro = np.asarray(m_filtro, dtype=float)
    fechamento = np.asarray(fechamento, dtype=float)
    n = len(fechamento)
    compra = np.zeros(n, dtype=bool)
    venda = np.zeros(n, dtype=bool)
    if n >= 2:
        compra[1:] = (
            (m_rapida[1:] > m_lenta[1:])
            & (m_rapida[:-1] <= m_lenta[:-1])
            & (fechamento[1:] > m_filtro[1:])
        )
        venda[1:] = (m_rapida[1:] < m_lenta[1:]) & (m_rapida[:-1] >= m_lenta[:-1])
    return compra, venda


def calcular_sinais_rompimento(fechamento, maxima_recente):
    """Alternativa de momentum: rompimento de canal Donchian -- fechamento
    supera a maxima dos ultimos N candles (maxima_recente ja vem deslocada em
    1 candle pelo chamador, pra nao incluir o candle atual no proprio canal).
    Sem sinal de venda proprio -- usa só stop%/alvo%/time-stop."""
    fechamento = np.asarray(fechamento, dtype=float)
    maxima_recente = np.asarray(maxima_recente, dtype=float)
    compra = fechamento > maxima_recente
    venda = np.zeros(len(fechamento), dtype=bool)
    return compra, venda


# ----------------------------------------------------------------------------
# CANDIDATA 4 -- VOLUME PURO (sem cruzamento de medias, pedido explicito do
# usuario: so a forca do volume da vela, objetivo identificar movimentacao de
# "big players". Pensada pra horizonte de horas-a-dias, nao minutos.)
# ----------------------------------------------------------------------------
def calcular_sinais_volume_puro(volume, abertura, fechamento, vol_periodo=20, vol_multiplicador=2.5):
    """Compra quando o volume do candle e um spike (>vol_multiplicador x a
    media dos vol_periodo candles ANTERIORES) E o candle fechou em alta
    (fechamento > abertura) -- interpretacao de que volume anormal + vela de
    alta sugere pressao compradora forte (ex.: acumulacao por 'big player'),
    nao apenas ruido. Sem cruzamento de medias, sem RSI, sem canal -- so
    volume + direcao da propria vela, como pedido. Sem sinal de venda proprio
    -- usa só stop%/alvo%/time-stop (mesmo padrao das outras candidatas)."""
    volume_spike = filtro_volume_forte(volume, vol_periodo, vol_multiplicador)
    abertura = np.asarray(abertura, dtype=float)
    fechamento = np.asarray(fechamento, dtype=float)
    vela_alta = fechamento > abertura
    compra = volume_spike & vela_alta
    venda = np.zeros(len(fechamento), dtype=bool)
    return compra, venda


# ----------------------------------------------------------------------------
# CANDIDATA 5 -- VOLUME SPIKE vs CANDLE ANTERIOR (pedido explicito do usuario:
# nao media movel, so comparar com o candle IMEDIATAMENTE anterior)
# ----------------------------------------------------------------------------
def calcular_sinais_volume_spike_anterior(volume, abertura, fechamento,
                                           multiplicador_compra=10.0, multiplicador_venda=10.0):
    """Compra quando volume[i] > multiplicador_compra x volume[i-1] E o candle
    fechou em alta. Sinal de venda (saida) quando volume[i] > multiplicador_venda
    x volume[i-1] E o candle fechou em baixa -- multiplicadores INDEPENDENTES
    pra compra e venda (permite grade assimetrica). Diferente de
    calcular_sinais_volume_puro, que compara com uma MEDIA de N candles; aqui
    e literalmente so o candle anterior, como pedido."""
    volume = np.asarray(volume, dtype=float)
    abertura = np.asarray(abertura, dtype=float)
    fechamento = np.asarray(fechamento, dtype=float)
    n = len(volume)
    spike_compra = np.zeros(n, dtype=bool)
    spike_venda = np.zeros(n, dtype=bool)
    spike_compra[1:] = volume[1:] > multiplicador_compra * volume[:-1]
    spike_venda[1:] = volume[1:] > multiplicador_venda * volume[:-1]
    vela_alta = fechamento > abertura
    vela_baixa = fechamento < abertura
    compra = spike_compra & vela_alta
    venda = spike_venda & vela_baixa
    return compra, venda


# ----------------------------------------------------------------------------
# FILTRO DE VOLUME (candidata 3 = (1) ou (2) + este filtro; tambem usado
# diretamente pela candidata 4 acima)
# ----------------------------------------------------------------------------
def filtro_volume_forte(volume, vol_periodo=20, vol_multiplicador=1.5):
    """True nos candles em que o volume supera vol_multiplicador x a media dos
    vol_periodo candles ANTERIORES (shift(1), nao inclui o candle atual --
    evita a media se auto-inflar pelo proprio spike que estamos detectando)."""
    volume = pd.Series(np.asarray(volume, dtype=float))
    media = volume.shift(1).rolling(vol_periodo).mean()
    return (volume > vol_multiplicador * media).fillna(False).to_numpy()


# ----------------------------------------------------------------------------
# SIMULACAO DE POSICAO -- stop%, alvo%, time-stop, motivo de saida
# ----------------------------------------------------------------------------
def simular_posicao_daytrade(abertura, minima, maxima, sinais_compra, sinais_venda,
                              stop_pct, alvo_pct, max_barras_posicao, slippage=0.0):
    """Como simular_posicao (estrategia_core.py), mas com stop percentual fixo,
    alvo (take-profit) percentual e time-stop -- em vez de ATR fixo.

    Regras:
      - Entrada: fora da posicao e sinais_compra[i-1] True -> entra na abertura
        de i, ajustada por slippage: abertura[i] * (1+slippage).
      - stop = entrada * (1 - stop_pct); alvo = entrada * (1 + alvo_pct).
      - A cada candle posicionado, nesta ordem de prioridade:
          1) minima[i] < stop  -> sai por STOP (preco = min(stop, abertura[i]),
             pior caso -- assume que se stop E alvo bateram no mesmo candle, o
             stop veio primeiro; nao da pra saber a ordem intra-candle a partir
             de OHLC, entao assumimos o cenario conservador)
          2) maxima[i] > alvo  -> sai por ALVO (preco = max(alvo, abertura[i]))
          3) sinais_venda[i-1] -> sai por SINAL (preco = abertura[i])
          4) (i - entrada_idx) >= max_barras_posicao -> sai por TEMPO
             (preco = abertura[i])
      Eventos de saida sao 5-tuplas: (tipo="saida", indice, preco, stop_no_momento,
      motivo). Eventos de entrada continuam 4-tuplas: (tipo="entrada", indice,
      preco, stop) -- mesmo formato de estrategia_core.simular_posicao.

      Retorna (eventos, estado_final)."""
    abertura = np.asarray(abertura, dtype=float)
    minima = np.asarray(minima, dtype=float)
    maxima = np.asarray(maxima, dtype=float)
    n = len(abertura)

    eventos = []
    posicionado = False
    stop = alvo = 0.0
    entrada_idx = None
    entrada_preco = None

    for i in range(1, n):
        if not posicionado and sinais_compra[i - 1] and abertura[i] > 0:
            entrada_preco = abertura[i] * (1 + slippage)
            stop = entrada_preco * (1 - stop_pct)
            alvo = entrada_preco * (1 + alvo_pct)
            entrada_idx = i
            posicionado = True
            eventos.append(("entrada", i, entrada_preco, stop))
        elif posicionado:
            furou_stop = minima[i] < stop
            bateu_alvo = maxima[i] > alvo
            if furou_stop:
                preco_bruto = min(stop, abertura[i])
                eventos.append(("saida", i, preco_bruto, stop, "stop"))
                posicionado = False
            elif bateu_alvo:
                preco_bruto = max(alvo, abertura[i])
                eventos.append(("saida", i, preco_bruto, stop, "alvo"))
                posicionado = False
            elif sinais_venda[i - 1]:
                eventos.append(("saida", i, abertura[i], stop, "sinal"))
                posicionado = False
            elif (i - entrada_idx) >= max_barras_posicao:
                eventos.append(("saida", i, abertura[i], stop, "tempo"))
                posicionado = False

    estado_final = {
        "posicionado": posicionado,
        "stop": stop if posicionado else None,
        "alvo": alvo if posicionado else None,
        "entrada_idx": entrada_idx if posicionado else None,
        "entrada_preco": entrada_preco if posicionado else None,
    }
    return eventos, estado_final
