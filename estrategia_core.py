# -*- coding: utf-8 -*-
# ESTRATEGIA CORE - fonte única da verdade da lógica de sinal/posição
# ----------------------------------------------------------------------------
# Antes, a MESMA regra de entrada/saída/stop vivia duplicada em dois lugares:
#   - executar_backtest_v4() (otimizador_v4.py) -> P&L do backtest
#   - estado_atual_posicao() (app_v4.py)         -> sinal ao vivo do radar
# Se alguém mudasse uma e esquecesse a outra, o radar passaria a mostrar sinais
# diferentes dos que a estratégia realmente testou — silenciosamente.
#
# Este módulo centraliza essa lógica em funções PURAS (só numpy, sem I/O, sem
# custos): `calcular_sinais` (condições de cruzamento + filtro) e
# `simular_posicao` (caminhada de posição/stop). Ambos os chamadores passam a
# usar exatamente estas funções — não há como divergir. Os custos (taxa,
# slippage no P&L) ficam na camada de cima (executar_backtest_v4), porque não
# fazem parte da lógica de posição — exceto o slippage de ENTRADA, que desloca
# o preço de entrada e portanto o nível do stop, e por isso entra aqui como
# parâmetro explícito.
#
# Coberto por tests/test_estrategia_core.py (golden-master + concordância +
# ausência de look-ahead).

import numpy as np


def calcular_sinais(m_rapida, m_lenta, m_filtro, fechamento):
    """Sinais de COMPRA e VENDA por cruzamento de médias.

    Compra no candle i: média rápida cruza a lenta PARA CIMA (rápida[i] > lenta[i]
    e rápida[i-1] <= lenta[i-1]) E o fechamento está acima do filtro de tendência.
    Venda no candle i: rápida cruza a lenta PARA BAIXO.

    Só usa dados até o índice i (sem look-ahead). Devolve dois arrays booleanos
    do tamanho da série (posição 0 sempre False)."""
    m_rapida = np.asarray(m_rapida)
    m_lenta = np.asarray(m_lenta)
    m_filtro = np.asarray(m_filtro)
    fechamento = np.asarray(fechamento)
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


def simular_posicao(abertura, minima, atr, sinais_compra, sinais_venda, multi_atr, slippage=0.0):
    """Caminha os candles e devolve a TIMELINE de posições (sem fees/P&L).

    Regras (idênticas às do v2/v4):
      - Entrada: se está fora e houve sinal de compra no candle ANTERIOR
        (`sinais_compra[i-1]`) e a abertura é válida (>0), entra na abertura do
        candle i, ajustada por slippage: `abertura[i] * (1 + slippage)`.
      - Stop (fixo, definido na entrada): `preco_entrada - atr[i-1] * multi_atr`.
      - Saída: se está posicionado e a mínima do candle fura o stop
        (`minima[i] < stop`) OU houve cruzamento contrário no candle anterior
        (`sinais_venda[i-1]`). Preço bruto de saída = min(stop, abertura[i]) no
        caso de stop, senão abertura[i]. (O slippage de SAÍDA e as taxas são
        aplicados pela camada de P&L, não aqui.)

    Entrada e saída nunca ocorrem no mesmo candle (estrutura if/elif). Devolve:
      eventos: lista de tuplas (tipo, indice, preco, stop), tipo em
               {"entrada","saida"}, em ordem cronológica.
      estado_final: dict {posicionado, stop, entrada_idx, entrada_preco}.
    """
    abertura = np.asarray(abertura)
    minima = np.asarray(minima)
    atr = np.asarray(atr)
    n = len(abertura)
    eventos = []
    posicionado = False
    stop = 0.0
    entrada_idx = None
    entrada_preco = None

    for i in range(1, n):
        if not posicionado and sinais_compra[i - 1] and abertura[i] > 0:
            entrada_preco = abertura[i] * (1 + slippage)
            stop = entrada_preco - atr[i - 1] * multi_atr
            entrada_idx = i
            posicionado = True
            eventos.append(("entrada", i, entrada_preco, stop))
        elif posicionado:
            furou_stop = minima[i] < stop
            if furou_stop or sinais_venda[i - 1]:
                preco_bruto = min(stop, abertura[i]) if furou_stop else abertura[i]
                eventos.append(("saida", i, preco_bruto, stop))
                posicionado = False

    estado_final = {
        "posicionado": posicionado,
        "stop": stop if posicionado else None,
        "entrada_idx": entrada_idx if posicionado else None,
        "entrada_preco": entrada_preco if posicionado else None,
    }
    return eventos, estado_final


def simular_posicao_filtro_adx(
    abertura, minima, atr, adx, plus_di, minus_di, sinais_compra, sinais_venda, multi_atr,
    adx_limiar=20.0, slippage=0.0,
):
    """Como simular_posicao, mas o cruzamento contrario SO fecha a posicao se
    a forca da tendencia (ADX) ja tiver enfraquecido OU a tendencia forte for
    de BAIXA (nao de alta). Motivacao (v6): a fraqueza conhecida da
    RECEITA_ROBUSTA e sair cedo demais em bull forte — o cruzamento contrario
    dispara em pullbacks temporarios dentro de uma tendencia de ALTA ainda
    viva. Um filtro de forca de tendencia tenta distinguir "a tendencia
    realmente acabou" (ADX baixo, honra o cruzamento) de "so um pullback
    dentro de uma alta forte" (ADX alto E +DI>-DI, ignora o cruzamento e
    segue posicionado ate o stop ATR).

    CORRECAO (v6b) sobre a primeira versao: ADX sozinho mede so a FORCA da
    tendencia, nao a direcao — uma queda forte tambem tem ADX alto. A
    primeira versao (so ADX) mantinha posicoes compradas perdedoras presas
    durante quedas fortes (ADX alto = "tendencia forte" foi mal-interpretado
    como motivo pra nao sair), degradando o desempenho em BEAR. Agora exige
    tambem que +DI > -DI (a tendencia forte tem que ser de ALTA) pra ignorar
    o cruzamento — do contrario (tendencia forte de BAIXA, ou tendencia
    fraca), o cruzamento contrario e honrado normalmente, protegendo o lado
    defensivo.

    Regras (identicas a simular_posicao, exceto o item 3):
      1) Entrada: igual a simular_posicao — abertura[i]*(1+slippage), stop =
         entrada - atr[i-1]*multi_atr.
      2) Stop: furou a minima[i] < stop -> sai (mesma prioridade/preco de
         simular_posicao), independente do ADX — o stop e proteção de risco,
         nao deve ser filtrado por forca/direcao de tendencia.
      3) Cruzamento contrario (sinais_venda[i-1]): SO e IGNORADO (posicao
         continua aberta) se adx[i-1] >= adx_limiar E plus_di[i-1] >
         minus_di[i-1] (tendencia forte E de alta). Em qualquer outro caso
         (tendencia fraca, tendencia forte de baixa, ou indicadores nao
         finitos no warm-up) o cruzamento e honrado normalmente — mesmo
         comportamento conservador de simular_posicao.

    Retorna os mesmos tipos que simular_posicao: (eventos, estado_final)."""
    abertura = np.asarray(abertura)
    minima = np.asarray(minima)
    atr = np.asarray(atr)
    adx = np.asarray(adx)
    plus_di = np.asarray(plus_di)
    minus_di = np.asarray(minus_di)
    n = len(abertura)
    eventos = []
    posicionado = False
    stop = 0.0
    entrada_idx = None
    entrada_preco = None

    for i in range(1, n):
        if not posicionado and sinais_compra[i - 1] and abertura[i] > 0:
            entrada_preco = abertura[i] * (1 + slippage)
            stop = entrada_preco - atr[i - 1] * multi_atr
            entrada_idx = i
            posicionado = True
            eventos.append(("entrada", i, entrada_preco, stop))
        elif posicionado:
            furou_stop = minima[i] < stop
            adx_ant, pdi_ant, mdi_ant = adx[i - 1], plus_di[i - 1], minus_di[i - 1]
            indicadores_validos = np.isfinite(adx_ant) and np.isfinite(pdi_ant) and np.isfinite(mdi_ant)
            tendencia_forte_de_alta = indicadores_validos and (adx_ant >= adx_limiar) and (pdi_ant > mdi_ant)
            cruzou_venda = sinais_venda[i - 1] and not tendencia_forte_de_alta
            if furou_stop or cruzou_venda:
                preco_bruto = min(stop, abertura[i]) if furou_stop else abertura[i]
                eventos.append(("saida", i, preco_bruto, stop))
                posicionado = False

    estado_final = {
        "posicionado": posicionado,
        "stop": stop if posicionado else None,
        "entrada_idx": entrada_idx if posicionado else None,
        "entrada_preco": entrada_preco if posicionado else None,
    }
    return eventos, estado_final


def simular_posicao_scale_out(
    abertura, minima, atr, sinais_compra, sinais_venda, multi_atr,
    fracao_saida_parcial=0.5, slippage=0.0,
):
    """Como simular_posicao, mas o PRIMEIRO cruzamento contrario fecha so uma
    FRACAO da posicao (fracao_saida_parcial), nao tudo — o resto continua
    aberto com o MESMO stop ATR, sem depender de prever se o cruzamento foi
    um pullback temporario ou uma reversao real (ao contrario do filtro ADX,
    v6, que tentou prever isso e nao funcionou). Ideia: sempre realiza uma
    parte do lucro no primeiro sinal de saida, mas deixa uma fracao "correr"
    a tendencia se ela continuar.

    Regras:
      1) Entrada: igual a simular_posicao.
      2) Stop: furou a minima[i] < stop -> fecha TODA a fracao restante de
         uma vez (protecao de risco nao e parcial).
      3) Cruzamento contrario (sinais_venda[i-1]):
         - Se ainda nao houve saida parcial nesta posicao: fecha
           fracao_saida_parcial da posicao original (evento "saida_parcial"),
           mantem o resto aberto com o MESMO stop.
         - Se ja houve saida parcial (so resta a fracao remanescente): um
           NOVO cruzamento contrario fecha o restante todo (evento "saida"
           normal).

    Eventos: entrada = ("entrada", i, preco, stop) igual a simular_posicao.
    Saida parcial = ("saida_parcial", i, preco, stop, fracao_fechada) onde
    fracao_fechada e relativa ao tamanho ORIGINAL da posicao (ex.: 0.5).
    Saida final = ("saida", i, preco, stop) fecha o que sobrou (seja 1.0 se
    nunca teve saida parcial -- caso o stop bata primeiro -- ou o restante
    apos a parcial).

    estado_final ganha uma chave extra: "fracao_aberta" (1.0 se nunca saiu
    parcial, senao 1.0 - fracao_saida_parcial, ou None se fechado)."""
    abertura = np.asarray(abertura)
    minima = np.asarray(minima)
    atr = np.asarray(atr)
    n = len(abertura)
    eventos = []
    posicionado = False
    stop = 0.0
    entrada_idx = None
    entrada_preco = None
    fracao_aberta = 0.0
    ja_saiu_parcial = False

    for i in range(1, n):
        if not posicionado and sinais_compra[i - 1] and abertura[i] > 0:
            entrada_preco = abertura[i] * (1 + slippage)
            stop = entrada_preco - atr[i - 1] * multi_atr
            entrada_idx = i
            posicionado = True
            fracao_aberta = 1.0
            ja_saiu_parcial = False
            eventos.append(("entrada", i, entrada_preco, stop))
        elif posicionado:
            furou_stop = minima[i] < stop
            if furou_stop:
                preco_bruto = min(stop, abertura[i])
                eventos.append(("saida", i, preco_bruto, stop))
                posicionado = False
                fracao_aberta = 0.0
            elif sinais_venda[i - 1]:
                if not ja_saiu_parcial:
                    eventos.append(("saida_parcial", i, abertura[i], stop, fracao_saida_parcial))
                    fracao_aberta -= fracao_saida_parcial
                    ja_saiu_parcial = True
                else:
                    eventos.append(("saida", i, abertura[i], stop))
                    posicionado = False
                    fracao_aberta = 0.0

    estado_final = {
        "posicionado": posicionado,
        "stop": stop if posicionado else None,
        "entrada_idx": entrada_idx if posicionado else None,
        "entrada_preco": entrada_preco if posicionado else None,
        "fracao_aberta": fracao_aberta if posicionado else None,
    }
    return eventos, estado_final


def simular_posicao_scale_out_trailing(
    abertura, minima, maxima, atr, sinais_compra, sinais_venda, multi_atr,
    fracao_saida_parcial=0.5, mult_trailing=2.0, slippage=0.0,
):
    """Como simular_posicao_scale_out, mas a partir da saida parcial, a
    fracao REMANESCENTE passa a usar um stop TRAILING (em vez do stop fixo
    original) -- ideia: o trailing puro ja falhou na posicao INTEIRA (v3 e
    Fase 2A), mas na metade que sobra depois de realizar lucro na primeira
    metade, o contexto de risco e diferente (e "dinheiro que ja era lucro"),
    entao um trailing mais apertado pode fazer sentido so nessa parte.

    Regras:
      1) Entrada: igual a simular_posicao. Stop fixo = entrada - atr[i-1]*multi_atr.
      2) ANTES da saida parcial: comportamento identico a simular_posicao_scale_out
         (stop fixo, cruzamento contrario fecha fracao_saida_parcial).
      3) NO MOMENTO da saida parcial: o ATR daquele instante fica CONGELADO
         (atr_pos_parcial) e o trailing e ativado imediatamente pra fracao
         remanescente -- stop = max(stop_fixo_original, maxima_pos_parcial -
         atr_pos_parcial * mult_trailing), so sobe.
      4) DEPOIS da saida parcial: a fracao remanescente sai por trailing stop
         (minima[i] < stop) OU por um SEGUNDO cruzamento contrario -- o que
         vier primeiro (mesma prioridade de sempre: stop primeiro).

    Retorna os mesmos tipos que simular_posicao_scale_out."""
    abertura = np.asarray(abertura)
    minima = np.asarray(minima)
    maxima = np.asarray(maxima)
    atr = np.asarray(atr)
    n = len(abertura)
    eventos = []
    posicionado = False
    stop = 0.0
    entrada_idx = None
    entrada_preco = None
    fracao_aberta = 0.0
    ja_saiu_parcial = False
    trailing_ativo = False
    atr_pos_parcial = 0.0
    maxima_desde_parcial = 0.0

    for i in range(1, n):
        if not posicionado and sinais_compra[i - 1] and abertura[i] > 0:
            entrada_preco = abertura[i] * (1 + slippage)
            stop = entrada_preco - atr[i - 1] * multi_atr
            entrada_idx = i
            posicionado = True
            fracao_aberta = 1.0
            ja_saiu_parcial = False
            trailing_ativo = False
            eventos.append(("entrada", i, entrada_preco, stop))
        elif posicionado:
            if trailing_ativo:
                if maxima[i] > maxima_desde_parcial:
                    maxima_desde_parcial = maxima[i]
                stop_trail = maxima_desde_parcial - atr_pos_parcial * mult_trailing
                if stop_trail > stop:
                    stop = stop_trail

            furou_stop = minima[i] < stop
            if furou_stop:
                preco_bruto = min(stop, abertura[i])
                eventos.append(("saida", i, preco_bruto, stop))
                posicionado = False
                fracao_aberta = 0.0
                trailing_ativo = False
            elif sinais_venda[i - 1]:
                if not ja_saiu_parcial:
                    eventos.append(("saida_parcial", i, abertura[i], stop, fracao_saida_parcial))
                    fracao_aberta -= fracao_saida_parcial
                    ja_saiu_parcial = True
                    trailing_ativo = True
                    atr_pos_parcial = float(atr[i - 1]) if np.isfinite(atr[i - 1]) else 0.0
                    maxima_desde_parcial = maxima[i]
                else:
                    eventos.append(("saida", i, abertura[i], stop))
                    posicionado = False
                    fracao_aberta = 0.0
                    trailing_ativo = False

    estado_final = {
        "posicionado": posicionado,
        "stop": stop if posicionado else None,
        "entrada_idx": entrada_idx if posicionado else None,
        "entrada_preco": entrada_preco if posicionado else None,
        "fracao_aberta": fracao_aberta if posicionado else None,
        "trailing_ativo": trailing_ativo if posicionado else False,
    }
    return eventos, estado_final


def simular_posicao_trailing(
    abertura, minima, maxima, atr,
    sinais_compra, sinais_venda,
    multi_atr,
    ativacao_x=1.5,
    mult_trailing=3.0,
    usar_saida_cruzamento=False,
    slippage=0.0,
):
    """Como simular_posicao, mas com stop TRAILING após lucro atingir ativacao_x
    × risco_inicial.

    Regras:
      - Entrada: igual ao simular_posicao (abertura[i] × (1+slippage), stop fixo
        = entrada − atr[i-1] × multi_atr).
      - Stop fixo fica ativo até o lucro flutuante atingir ativacao_x × risco_inicial.
      - Quando ativa o trailing, o stop passa a ser:
            max(stop_atual, maxima_desde_entrada − atr_entrada × mult_trailing)
        O stop NUNCA desce. O ATR usado no trailing é o ATR NO MOMENTO DA ENTRADA
        (constante para aquela posição), evitando que uma queda brusca no ATR
        mantenha o stop muito próximo.
      - Saída por cruzamento contrário (sinais_venda): só acontece se
        usar_saida_cruzamento=True. Se False, a posição só fecha por stop.
        (Padrão False porque o trailing stop já é a saída principal — manter o
        cruzamento poderia fechar cedo numa correção parcial dentro de uma alta
        maior, que é justamente o que queremos capturar.)

    Retorna os mesmos tipos que simular_posicao: (eventos, estado_final)."""
    abertura = np.asarray(abertura)
    minima   = np.asarray(minima)
    maxima   = np.asarray(maxima)
    atr      = np.asarray(atr)
    n = len(abertura)

    eventos = []
    posicionado    = False
    stop           = 0.0
    entrada_idx    = None
    entrada_preco  = None
    atr_entrada    = 0.0
    risco_inicial  = 0.0
    max_preco      = 0.0
    trailing_ativo = False

    for i in range(1, n):
        if not posicionado and sinais_compra[i - 1] and abertura[i] > 0:
            entrada_preco  = abertura[i] * (1 + slippage)
            atr_entrada    = float(atr[i - 1]) if np.isfinite(atr[i - 1]) else 0.0
            stop           = entrada_preco - atr_entrada * multi_atr
            risco_inicial  = entrada_preco - stop
            max_preco      = entrada_preco
            trailing_ativo = False
            entrada_idx    = i
            posicionado    = True
            eventos.append(("entrada", i, entrada_preco, stop))
        elif posicionado:
            if maxima[i] > max_preco:
                max_preco = maxima[i]

            # Ativa trailing quando lucro flutuante >= ativacao_x * risco_inicial
            if not trailing_ativo and risco_inicial > 0:
                lucro_float = max_preco - entrada_preco
                if lucro_float >= ativacao_x * risco_inicial:
                    trailing_ativo = True

            # Atualiza stop (só sobe)
            if trailing_ativo and atr_entrada > 0:
                stop_trail = max_preco - atr_entrada * mult_trailing
                if stop_trail > stop:
                    stop = stop_trail

            # Verifica saida
            furou_stop   = minima[i] < stop
            cruzou_venda = usar_saida_cruzamento and sinais_venda[i - 1]
            if furou_stop or cruzou_venda:
                preco_bruto = min(stop, abertura[i]) if furou_stop else abertura[i]
                eventos.append(("saida", i, preco_bruto, stop))
                posicionado    = False
                trailing_ativo = False

    estado_final = {
        "posicionado":   posicionado,
        "stop":          stop if posicionado else None,
        "trailing_ativo": trailing_ativo if posicionado else False,
        "entrada_idx":   entrada_idx if posicionado else None,
        "entrada_preco": entrada_preco if posicionado else None,
    }
    return eventos, estado_final


def estado_posicao_atual(df_fast, params, slippage=0.0):
    """Estado da posição no ÚLTIMO candle, para o radar ao vivo. Usa exatamente
    a mesma lógica (`calcular_sinais` + `simular_posicao`) que o backtest, então
    o radar nunca pode divergir do que a estratégia testou.

    `df_fast` é o dict de arrays já com as médias/ATR calculados (ver
    montar_df_fast). Devolve dict com: posicionado, preco_entrada, stop_atual,
    preco_atual, preco_sinal (preço bruto no candle da última transição) e
    data_mudanca (valor cru de t_abert nesse candle; o chamador embrulha em
    pd.Timestamp se quiser)."""
    m_rapida = df_fast[f"ma_{params['media_rapida']}"]
    m_lenta = df_fast[f"ma_{params['media_lenta']}"]
    m_filtro = df_fast[f"ma_f_{params['media_filtro']}"]
    abertura = np.asarray(df_fast["abertura"])
    minima = df_fast["minima"]
    fechamento = np.asarray(df_fast["fechamento"])
    atr = df_fast[f"atr_{params['atr_periodo']}"]
    t_abert = df_fast["t_abert"]
    multi_atr = params["atr_multiplicador"]
    n = len(fechamento)

    vazio = {"posicionado": False, "preco_entrada": None, "stop_atual": None,
             "preco_atual": None, "preco_sinal": None, "data_mudanca": None}
    if n < 5:
        return vazio

    compra, venda = calcular_sinais(m_rapida, m_lenta, m_filtro, fechamento)
    eventos, estado = simular_posicao(abertura, minima, atr, compra, venda, multi_atr, slippage)

    data_mudanca = preco_sinal = None
    if eventos:
        idx_ult = eventos[-1][1]
        data_mudanca = t_abert[idx_ult]
        preco_sinal = float(abertura[idx_ult])

    preco_entrada = stop_atual = None
    if estado["posicionado"]:
        preco_entrada = float(abertura[estado["entrada_idx"]])
        stop_atual = estado["stop"]

    return {
        "posicionado": estado["posicionado"],
        "preco_entrada": preco_entrada,
        "stop_atual": stop_atual,
        "preco_atual": float(fechamento[-1]),
        "preco_sinal": preco_sinal,
        "data_mudanca": data_mudanca,
    }
