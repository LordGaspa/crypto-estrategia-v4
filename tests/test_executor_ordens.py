# -*- coding: utf-8 -*-
"""Testes da camada de ordens (executor_ordens).

Nenhum teste toca a rede nem exige chave de API: tudo roda contra uma corretora
falsa que imita o comportamento relevante da Binance — inclusive a recusa de
clientOrderId duplicado, que é a base da idempotência.

Cobrem:
  - leitura dos filtros do par (LOT_SIZE / NOTIONAL, nomes novo e antigo,
    MARKET_LOT_SIZE mais restritivo);
  - dimensionamento: arredondamento pra baixo, nunca gastar mais que o pedido,
    e recusa EXPLÍCITA quando a ordem fica abaixo do mínimo da Binance;
  - idempotência: mesmo (símbolo, candle, lado) => mesmo id => duplicada;
  - envio: preço médio ponderado vindo dos fills reais, e que nenhuma falha da
    corretora escapa como exceção (o ciclo não pode morrer no meio).
"""
from decimal import Decimal

import pytest

from executor_ordens import (
    COMPRA, VENDA, PREFIXO_ORDEM,
    FiltrosPar, ErroCorretora, ResultadoOrdem,
    extrair_filtros, calcular_quantidade, quantidade_para_vender,
    id_ordem, enviar_ordem, comprar, vender_tudo,
)


# ---------------------------------------------------------------------------
# corretora falsa
# ---------------------------------------------------------------------------
def info_par(symbol="BTCUSDT", base="BTC", passo="0.00001", min_qty="0.00001",
             max_qty="9000", min_notional="5", legado=False, market_lot=None):
    filtros = [
        {"filterType": "LOT_SIZE", "stepSize": passo, "minQty": min_qty, "maxQty": max_qty},
        {"filterType": "MIN_NOTIONAL" if legado else "NOTIONAL", "minNotional": min_notional},
    ]
    if market_lot:
        filtros.append({"filterType": "MARKET_LOT_SIZE", **market_lot})
    return {"symbol": symbol, "baseAsset": base, "quoteAsset": "USDT", "filters": filtros}


class CorretoraFake:
    """Imita o que importa da Binance: filtros, preço, saldo, histórico de
    ordens por clientOrderId e envio a mercado.

    IMPORTANTE — comportamento verificado na testnet em 2026-09-08: a Binance
    **aceita** um clientOrderId repetido depois que a ordem anterior preencheu
    (a unicidade só vale enquanto a ordem está ABERTA, e ordem a mercado
    preenche na hora). Esta falsa reproduz isso de propósito: `ordem_mercado`
    NÃO recusa duplicata. Quem protege é a consulta prévia em `enviar_ordem`.
    Uma falsa mais "esperta" que a corretora real esconderia justamente o bug
    que o teste precisa pegar.
    """

    def __init__(self, info=None, preco="50000", saldos=None, falha=None, falha_busca=None):
        self._info = info or info_par()
        self._preco = Decimal(preco)
        self._saldos = saldos or {"BTC": Decimal("0.5")}
        self._falha = falha
        self._falha_busca = falha_busca
        self.ordens = {}
        self.enviadas = []

    def info_symbol(self, symbol):
        return self._info

    def preco(self, symbol):
        return self._preco

    def saldo(self, ativo):
        return self._saldos.get(ativo, Decimal(0))

    def buscar_ordem(self, symbol, client_order_id):
        if self._falha_busca:
            raise self._falha_busca
        return self.ordens.get(client_order_id)

    def ordem_mercado(self, symbol, lado, qty, client_order_id):
        if self._falha:
            raise self._falha
        resposta = {
            "symbol": symbol, "status": "FILLED",
            "clientOrderId": client_order_id,
            "executedQty": str(qty),
            "cummulativeQuoteQty": str(qty * self._preco),
            "fills": [{"qty": str(qty), "price": str(self._preco)}],
        }
        self.ordens[client_order_id] = resposta      # vira historico consultavel
        self.enviadas.append((symbol, lado, qty, client_order_id))
        return resposta


# ---------------------------------------------------------------------------
# filtros
# ---------------------------------------------------------------------------
def test_extrai_filtros_basicos():
    f = extrair_filtros(info_par())
    assert f.passo_qty == Decimal("0.00001")
    assert f.valor_minimo == Decimal("5")


def test_aceita_nome_antigo_min_notional():
    """A API tem `NOTIONAL` hoje e `MIN_NOTIONAL` no legado; os dois valem."""
    assert extrair_filtros(info_par(legado=True)).valor_minimo == Decimal("5")


def test_market_lot_size_mais_restritivo_prevalece():
    """Ordem a mercado obedece MARKET_LOT_SIZE também — vale o mais restritivo,
    porque é o que a corretora vai cobrar."""
    f = extrair_filtros(info_par(market_lot={"stepSize": "0.001", "minQty": "0.002", "maxQty": "100"}))
    assert f.passo_qty == Decimal("0.001")
    assert f.qty_minima == Decimal("0.002")
    assert f.qty_maxima == Decimal("100")


def test_market_lot_size_com_passo_zero_e_ignorado():
    f = extrair_filtros(info_par(market_lot={"stepSize": "0", "minQty": "0", "maxQty": "0"}))
    assert f.passo_qty == Decimal("0.00001")
    assert f.qty_maxima == Decimal("9000")


def test_sem_lot_size_e_erro_explicito():
    with pytest.raises(ValueError, match="LOT_SIZE"):
        extrair_filtros({"symbol": "XXXUSDT", "filters": []})


# ---------------------------------------------------------------------------
# dimensionamento
# ---------------------------------------------------------------------------
def test_arredonda_para_baixo_no_passo():
    f = extrair_filtros(info_par(passo="0.001", min_qty="0.001"))
    q = calcular_quantidade(100, 3000, f)   # 0.03333... -> 0.033
    assert q.aceita and q.qty == Decimal("0.033")


def test_nunca_gasta_mais_do_que_o_pedido():
    """Propriedade central: o valor da ordem tem que ser <= o valor pretendido.
    Arredondar pra cima estouraria o orçamento do ativo."""
    f = extrair_filtros(info_par(passo="0.001", min_qty="0.001", min_notional="0"))
    for valor in (10, 23, 47.55, 100, 999.99):
        for preco in (0.5, 37, 3000, 51234.77):
            q = calcular_quantidade(valor, preco, f)
            if q.aceita:
                assert q.qty * Decimal(str(preco)) <= Decimal(str(valor))


def test_resultado_e_multiplo_exato_do_passo():
    f = extrair_filtros(info_par(passo="0.001", min_qty="0.001", min_notional="0"))
    q = calcular_quantidade(123.45, 678.9, f)
    assert q.aceita
    assert q.qty % Decimal("0.001") == 0


def test_recusa_abaixo_do_minimo_da_binance_com_motivo():
    """O caso da seção 3.4 do plano: com capital espalhado em muitos ativos, a
    ordem fica abaixo do MIN_NOTIONAL. Tem que RECUSAR com motivo legível — se
    passasse batido, a Binance rejeitaria em silêncio e o portfólio real ficaria
    diferente do simulado."""
    f = extrair_filtros(info_par(min_notional="10"))
    q = calcular_quantidade(4, 50000, f)
    assert not q.aceita
    assert "abaixo do mínimo" in q.motivo


def test_nocional_checado_depois_do_arredondamento():
    """Arredondar pra baixo pode empurrar a ordem pra debaixo do mínimo: 10.5/1
    daria 10.5 (passa em 10), mas o passo de 1 corta pra 10... e com preço 1 o
    nocional vira exatamente 10. Com mínimo 11, tem que recusar."""
    f = extrair_filtros(info_par(passo="1", min_qty="1", min_notional="11"))
    q = calcular_quantidade(10.5, 1, f)
    assert not q.aceita and "abaixo do mínimo" in q.motivo


def test_valor_pequeno_demais_para_um_passo():
    f = extrair_filtros(info_par(passo="1", min_qty="1", min_notional="0"))
    q = calcular_quantidade(5, 100, f)      # nem 1 unidade
    assert not q.aceita and "passo" in q.motivo


def test_recusa_acima_do_maximo():
    f = extrair_filtros(info_par(passo="0.001", min_qty="0.001", max_qty="1", min_notional="0"))
    q = calcular_quantidade(1_000_000, 100, f)
    assert not q.aceita and "acima do máximo" in q.motivo


@pytest.mark.parametrize("valor,preco", [(0, 100), (-5, 100), (100, 0), (100, -3)])
def test_entradas_invalidas_sao_recusadas(valor, preco):
    f = extrair_filtros(info_par())
    assert not calcular_quantidade(valor, preco, f).aceita


# ---------------------------------------------------------------------------
# venda
# ---------------------------------------------------------------------------
def test_venda_nunca_excede_o_saldo():
    f = extrair_filtros(info_par(passo="0.001", min_qty="0.001"))
    q = quantidade_para_vender(Decimal("0.0349"), f)
    assert q.aceita and q.qty == Decimal("0.034") and q.qty <= Decimal("0.0349")


def test_poeira_nao_e_vendavel():
    """Saldo menor que o lote mínimo (resto de arredondamento) não dá pra
    vender — precisa ser reportado, não tratado como posição fechada."""
    f = extrair_filtros(info_par(passo="0.001", min_qty="0.001"))
    q = quantidade_para_vender(Decimal("0.0009"), f)
    assert not q.aceita and "poeira" in q.motivo


def test_saldo_zerado():
    assert not quantidade_para_vender(0, extrair_filtros(info_par())).aceita


# ---------------------------------------------------------------------------
# idempotência
# ---------------------------------------------------------------------------
def test_id_e_deterministico():
    a = id_ordem("BTCUSDT", "2026-09-08T12:00", COMPRA)
    b = id_ordem("BTCUSDT", "2026-09-08T12:00", COMPRA)
    assert a == b


def test_id_muda_com_candle_lado_e_ativo():
    base = id_ordem("BTCUSDT", "2026-09-08T12:00", COMPRA)
    assert base != id_ordem("BTCUSDT", "2026-09-08T18:00", COMPRA)   # outro candle
    assert base != id_ordem("BTCUSDT", "2026-09-08T12:00", VENDA)    # outro lado
    assert base != id_ordem("ETHUSDT", "2026-09-08T12:00", COMPRA)   # outro ativo


def test_id_respeita_o_formato_da_binance():
    """Limite de 36 caracteres e charset restrito — um id inválido derrubaria
    toda ordem, inclusive as legítimas."""
    coid = id_ordem("1MBABYDOGEUSDT", "2026-09-08T12:00", COMPRA)
    assert coid.startswith(PREFIXO_ORDEM)
    assert 1 <= len(coid) <= 36
    assert all(c.isalnum() or c in "-_.:/" for c in coid)


def test_lado_invalido_e_rejeitado():
    with pytest.raises(ValueError):
        id_ordem("BTCUSDT", "2026-09-08T12:00", "LONG")


def test_ordem_repetida_no_mesmo_candle_e_recusada_pela_corretora():
    """O cron rodando duas vezes no mesmo candle NÃO pode comprar duas vezes."""
    c = CorretoraFake()
    r1 = comprar(c, "BTCUSDT", 100, "2026-09-08T12:00")
    r2 = comprar(c, "BTCUSDT", 100, "2026-09-08T12:00")
    assert r1.status == "executada"
    assert r2.status == "duplicada"
    assert len(c.enviadas) == 1          # só uma ordem chegou de fato


def test_a_corretora_sozinha_NAO_protege_contra_id_repetido():
    """Regressão do bug achado na testnet: mandar a ordem direto, sem a consulta
    prévia, passa duas vezes. Este teste existe para travar a tentação de
    "simplificar" removendo a verificação — ela é a única proteção real."""
    c = CorretoraFake()
    c.ordem_mercado("BTCUSDT", COMPRA, Decimal("0.002"), "mesmo-id")
    c.ordem_mercado("BTCUSDT", COMPRA, Decimal("0.002"), "mesmo-id")
    assert len(c.enviadas) == 2      # a Binance real se comporta assim


def test_falha_ao_verificar_duplicidade_aborta_o_envio():
    """Fail closed: sem conseguir confirmar que a ordem ainda não existe, é
    melhor perder a entrada do que arriscar comprar em dobro."""
    c = CorretoraFake(falha_busca=ConnectionError("API instável"))
    r = enviar_ordem(c, "BTCUSDT", COMPRA, Decimal("0.002"), "c1")
    assert r.status == "erro"
    assert "NÃO enviada" in r.motivo
    assert not c.enviadas


def test_candle_seguinte_pode_operar_normalmente():
    c = CorretoraFake()
    comprar(c, "BTCUSDT", 100, "2026-09-08T12:00")
    r = comprar(c, "BTCUSDT", 100, "2026-09-08T18:00")
    assert r.status == "executada" and len(c.enviadas) == 2


# ---------------------------------------------------------------------------
# envio
# ---------------------------------------------------------------------------
def test_preco_medio_ponderado_pelos_fills():
    """Ordem a mercado preenche em pedaços a preços diferentes. É esse preço
    médio real que depois vai ser comparado com o slippage assumido."""
    class FillsVariados(CorretoraFake):
        def ordem_mercado(self, symbol, lado, qty, client_order_id):
            return {"symbol": symbol, "status": "FILLED",
                    "fills": [{"qty": "1", "price": "100"},
                              {"qty": "3", "price": "200"}]}

    r = enviar_ordem(FillsVariados(), "BTCUSDT", COMPRA, Decimal("4"), "c1")
    assert r.ok
    assert r.qty == Decimal("4")
    assert r.preco_medio == Decimal("175")     # (1*100 + 3*200) / 4
    assert r.valor == Decimal("700")


def test_sem_fills_usa_campos_agregados():
    class SemFills(CorretoraFake):
        def ordem_mercado(self, symbol, lado, qty, client_order_id):
            return {"status": "FILLED", "executedQty": "2", "cummulativeQuoteQty": "300"}

    r = enviar_ordem(SemFills(), "BTCUSDT", COMPRA, Decimal("2"), "c1")
    assert r.ok and r.preco_medio == Decimal("150")


def test_ordem_aceita_mas_nao_preenchida_e_erro():
    """Aceitar sem preencher não é sucesso — tratar como tal deixaria o sistema
    achando que tem posição quando não tem."""
    class NaoPreenche(CorretoraFake):
        def ordem_mercado(self, symbol, lado, qty, client_order_id):
            return {"status": "EXPIRED", "executedQty": "0", "cummulativeQuoteQty": "0"}

    r = enviar_ordem(NaoPreenche(), "BTCUSDT", COMPRA, Decimal("1"), "c1")
    assert r.status == "erro" and "não preencheu" in r.motivo


def test_falha_de_rede_vira_resultado_nao_excecao():
    """Se uma exceção escapasse, o ciclo morreria e os outros ativos ficariam
    sem processar."""
    c = CorretoraFake(falha=TimeoutError("conexão expirou"))
    r = enviar_ordem(c, "BTCUSDT", COMPRA, Decimal("1"), "c1")
    assert r.status == "erro" and "TimeoutError" in r.motivo


def test_erro_da_corretora_nao_duplicada():
    c = CorretoraFake(falha=ErroCorretora("saldo insuficiente", duplicada=False))
    r = enviar_ordem(c, "BTCUSDT", COMPRA, Decimal("1"), "c1")
    assert r.status == "erro"


def test_quantidade_zero_nao_vai_pra_corretora():
    c = CorretoraFake()
    r = enviar_ordem(c, "BTCUSDT", COMPRA, Decimal("0"), "c1")
    assert r.status == "recusada" and not c.enviadas


def test_falha_ao_ler_filtros_nao_derruba():
    class SemInfo(CorretoraFake):
        def info_symbol(self, symbol):
            raise ConnectionError("API fora do ar")

    r = comprar(SemInfo(), "BTCUSDT", 100, "c1")
    assert r.status == "erro" and "ConnectionError" in r.motivo


# ---------------------------------------------------------------------------
# ponta a ponta (contra a corretora falsa)
# ---------------------------------------------------------------------------
def test_compra_ponta_a_ponta():
    c = CorretoraFake(preco="50000")
    r = comprar(c, "BTCUSDT", 100, "2026-09-08T12:00")
    assert r.ok
    assert r.qty == Decimal("0.002")                  # 100/50000, no passo 0.00001
    assert r.valor <= Decimal("100")


def test_venda_total_ponta_a_ponta():
    c = CorretoraFake(saldos={"BTC": Decimal("0.12345678")})
    r = vender_tudo(c, "BTCUSDT", "2026-09-08T12:00")
    assert r.ok
    assert r.qty == Decimal("0.12345")                # truncado no passo 0.00001
    assert r.qty <= Decimal("0.12345678")


def test_venda_sem_posicao_e_recusada():
    c = CorretoraFake(saldos={"BTC": Decimal("0")})
    r = vender_tudo(c, "BTCUSDT", "2026-09-08T12:00")
    assert r.status == "recusada" and not c.enviadas
