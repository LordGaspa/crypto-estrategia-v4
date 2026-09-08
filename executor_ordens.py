# -*- coding: utf-8 -*-
"""Camada de ordens (Fase 1 do PLANO_EXECUCAO_AUTOMATICA.md).

Traduz uma DECISÃO já tomada ("comprar X dólares de BTCUSDT") numa ordem
executada na Binance. Este módulo **não decide nada** — quem decide é
`estrategia_core.estado_posicao_atual()`, exatamente como no `monitor_sinais.py`.
Manter essa separação é o que impede o executor de virar, com o tempo, uma
estratégia paralela diferente da que foi validada.

O que ele resolve, e por quê:

1. **Filtros da Binance** (`LOT_SIZE`, `NOTIONAL`). Cada par tem um passo de
   quantidade e um valor mínimo por ordem. Ignorar isso é o problema descrito na
   seção 3.4 do plano: a ordem é rejeitada e o portfólio real fica em silêncio
   diferente do simulado. Aqui a recusa é **explícita e com motivo**.

2. **Arredondamento sempre PRA BAIXO** (`ROUND_DOWN`), em `Decimal`, nunca
   float. Arredondar pra cima gastaria mais do que o pretendido; float daria
   erro de representação (0.1+0.2) que a Binance rejeita como quantidade
   inválida.

3. **Idempotência.** O `clientOrderId` é derivado de (símbolo, candle, lado).
   Se o cron rodar duas vezes no mesmo candle — reinício, sobreposição de
   execução, retry manual — a segunda tentativa carrega o MESMO id, e antes de
   enviar qualquer coisa o executor **pergunta à corretora se aquele id já
   existe**. É uma defesa que continua valendo mesmo se o nosso arquivo de
   estado for perdido ou corrompido, porque a memória fica na Binance.

   > **Descoberto na testnet (2026-09-08).** A primeira versão confiava na
   > Binance recusar o id repetido. **Ela não recusa**: a unicidade do
   > `clientOrderId` só vale enquanto a ordem está ABERTA, e ordem a mercado
   > preenche na hora, liberando o id imediatamente. O teste de fumaça comprou
   > duas vezes. Por isso a checagem prévia — sem ela não havia proteção
   > nenhuma contra compra em dobro.

Credenciais: **só** por variável de ambiente (`BINANCE_API_KEY`,
`BINANCE_API_SECRET`), como já é feito com o SMTP. Nunca em arquivo versionado,
nunca em log, nunca em mensagem de erro.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Optional, Protocol

# Prefixo do clientOrderId. A Binance aceita [.A-Za-z0-9_:/-]{1,36}; o prefixo
# mais 26 hex dá 32 caracteres, folgado dentro do limite e sempre válido.
PREFIXO_ORDEM = "omega-"
TAMANHO_HASH = 26

COMPRA = "BUY"
VENDA = "SELL"


# ---------------------------------------------------------------------------
# Filtros do par
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FiltrosPar:
    """Restrições que a Binance impõe por par de negociação."""
    symbol: str
    passo_qty: Decimal      # LOT_SIZE.stepSize — quantidade tem que ser múltipla disso
    qty_minima: Decimal     # LOT_SIZE.minQty
    qty_maxima: Decimal     # LOT_SIZE.maxQty
    valor_minimo: Decimal   # NOTIONAL.minNotional — valor mínimo da ordem em USDT


def extrair_filtros(info_symbol: dict) -> FiltrosPar:
    """Lê os filtros do payload de `exchangeInfo` para UM símbolo.

    Trata duas variações da API que convivem hoje:
      - `MIN_NOTIONAL` (nome antigo) e `NOTIONAL` (atual);
      - `MARKET_LOT_SIZE`, que se aplica a ordens a mercado e pode ser mais
        restritivo que o `LOT_SIZE` comum. Ficamos com o mais restritivo dos
        dois, porque é ele que a corretora vai cobrar.
    """
    filtros = {f["filterType"]: f for f in info_symbol.get("filters", [])}

    lote = filtros.get("LOT_SIZE")
    if lote is None:
        raise ValueError(f"{info_symbol.get('symbol')}: exchangeInfo sem filtro LOT_SIZE")

    passo = Decimal(str(lote["stepSize"]))
    qty_min = Decimal(str(lote["minQty"]))
    qty_max = Decimal(str(lote["maxQty"]))

    lote_mercado = filtros.get("MARKET_LOT_SIZE")
    if lote_mercado is not None:
        # stepSize 0 significa "sem restrição adicional" — nesse caso ignorar.
        passo_mkt = Decimal(str(lote_mercado.get("stepSize", "0")))
        if passo_mkt > 0:
            passo = max(passo, passo_mkt)
        qty_min = max(qty_min, Decimal(str(lote_mercado.get("minQty", "0"))))
        qty_max_mkt = Decimal(str(lote_mercado.get("maxQty", "0")))
        if qty_max_mkt > 0:
            qty_max = min(qty_max, qty_max_mkt)

    nocional = filtros.get("NOTIONAL") or filtros.get("MIN_NOTIONAL") or {}
    valor_min = Decimal(str(nocional.get("minNotional", "0")))

    return FiltrosPar(
        symbol=info_symbol["symbol"],
        passo_qty=passo,
        qty_minima=qty_min,
        qty_maxima=qty_max,
        valor_minimo=valor_min,
    )


# ---------------------------------------------------------------------------
# Dimensionamento
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Quantidade:
    qty: Decimal
    aceita: bool
    motivo: str = ""


def calcular_quantidade(valor_usd, preco, filtros: FiltrosPar) -> Quantidade:
    """Converte "quero gastar `valor_usd`" na quantidade que a Binance aceita.

    Devolve `aceita=False` com motivo legível quando a ordem não passa nos
    filtros — o chamador precisa **ver** a recusa, não descobrir depois que a
    posição não existe. Esse silêncio é exatamente o risco da seção 3.4 do plano.
    """
    valor = Decimal(str(valor_usd))
    p = Decimal(str(preco))

    if p <= 0:
        return Quantidade(Decimal(0), False, "preço inválido (<= 0)")
    if valor <= 0:
        return Quantidade(Decimal(0), False, "valor inválido (<= 0)")

    bruta = valor / p

    # PRA BAIXO, sempre: arredondar pra cima gastaria mais do que o pretendido.
    if filtros.passo_qty > 0:
        qty = (bruta / filtros.passo_qty).to_integral_value(rounding=ROUND_DOWN) * filtros.passo_qty
    else:
        qty = bruta

    if qty <= 0:
        return Quantidade(Decimal(0), False,
                          f"valor US$ {valor} não compra nem 1 passo de {filtros.symbol} "
                          f"(passo={filtros.passo_qty}, preço={p})")
    if qty < filtros.qty_minima:
        return Quantidade(Decimal(0), False,
                          f"quantidade {qty} abaixo do mínimo {filtros.qty_minima}")
    if filtros.qty_maxima > 0 and qty > filtros.qty_maxima:
        return Quantidade(Decimal(0), False,
                          f"quantidade {qty} acima do máximo {filtros.qty_maxima}")

    # O nocional tem que ser checado DEPOIS do arredondamento: arredondar pra
    # baixo pode justamente empurrar a ordem pra debaixo do mínimo.
    nocional = qty * p
    if nocional < filtros.valor_minimo:
        return Quantidade(Decimal(0), False,
                          f"valor da ordem US$ {nocional:.2f} abaixo do mínimo "
                          f"US$ {filtros.valor_minimo} da Binance")

    return Quantidade(qty, True)


def quantidade_para_vender(saldo, filtros: FiltrosPar) -> Quantidade:
    """Quantidade vendável a partir do saldo em carteira.

    A saída é sempre <= saldo (arredondando pra baixo): pedir pra vender mais do
    que se tem é rejeitado pela corretora, e a posição ficaria aberta sem que
    ninguém percebesse.
    """
    s = Decimal(str(saldo))
    if s <= 0:
        return Quantidade(Decimal(0), False, "saldo zerado")

    if filtros.passo_qty > 0:
        qty = (s / filtros.passo_qty).to_integral_value(rounding=ROUND_DOWN) * filtros.passo_qty
    else:
        qty = s

    if qty <= 0 or qty < filtros.qty_minima:
        return Quantidade(Decimal(0), False,
                          f"saldo {s} menor que o lote mínimo {filtros.qty_minima} — "
                          f"posição residual (poeira), não vendável")
    return Quantidade(qty, True)


# ---------------------------------------------------------------------------
# Identificador idempotente
# ---------------------------------------------------------------------------
def id_ordem(symbol: str, candle, lado: str) -> str:
    """clientOrderId determinístico para (símbolo, candle, lado).

    Mesma entrada => mesmo id => a Binance recusa a segunda como duplicada.
    O `candle` é o timestamp de abertura do candle que gerou o sinal; usar o
    candle (e não a hora atual) é o que faz duas execuções do cron dentro do
    mesmo candle colidirem de propósito.
    """
    if lado not in (COMPRA, VENDA):
        raise ValueError(f"lado inválido: {lado!r} (esperado {COMPRA} ou {VENDA})")
    chave = f"{symbol}|{candle}|{lado}"
    h = hashlib.sha256(chave.encode("utf-8")).hexdigest()[:TAMANHO_HASH]
    return PREFIXO_ORDEM + h


# ---------------------------------------------------------------------------
# Corretora
# ---------------------------------------------------------------------------
class Corretora(Protocol):
    """Interface mínima que o executor usa. Existe para que os testes rodem
    contra uma corretora falsa, sem rede e sem chave."""

    def info_symbol(self, symbol: str) -> dict: ...
    def preco(self, symbol: str) -> Decimal: ...
    def saldo(self, ativo: str) -> Decimal: ...
    def buscar_ordem(self, symbol: str, client_order_id: str) -> Optional[dict]: ...
    def listar_ordens(self, symbol: str, limite: int) -> list: ...
    def listar_trades(self, symbol: str, limite: int) -> list: ...
    def ordem_mercado(self, symbol: str, lado: str, qty: Decimal, client_order_id: str) -> dict: ...


class ErroCorretora(Exception):
    """Falha vinda da corretora. Nunca carrega credencial na mensagem."""

    def __init__(self, mensagem: str, duplicada: bool = False):
        super().__init__(mensagem)
        self.duplicada = duplicada


class CorretoraBinance:
    """Adaptador do python-binance. `testnet=True` aponta para a Spot Testnet,
    que é dinheiro de brinquedo com a API idêntica — é onde a Fase 1 roda."""

    def __init__(self, testnet: bool = True):
        chave = os.environ.get("BINANCE_API_KEY")
        segredo = os.environ.get("BINANCE_API_SECRET")
        if not chave or not segredo:
            raise ErroCorretora(
                "BINANCE_API_KEY / BINANCE_API_SECRET não estão no ambiente. "
                "Defina no monitor.env do servidor (chmod 600), nunca em arquivo versionado."
            )
        # import tardio: o módulo continua importável (e testável) sem a lib.
        from binance.client import Client

        self._cliente = Client(chave, segredo, testnet=testnet, ping=False)
        self.testnet = testnet

    def info_symbol(self, symbol: str) -> dict:
        info = self._cliente.get_symbol_info(symbol)
        if not info:
            raise ErroCorretora(f"{symbol}: par não encontrado na corretora")
        return info

    def preco(self, symbol: str) -> Decimal:
        return Decimal(self._cliente.get_symbol_ticker(symbol=symbol)["price"])

    def saldo(self, ativo: str) -> Decimal:
        return Decimal(self._cliente.get_asset_balance(asset=ativo)["free"])

    def buscar_ordem(self, symbol: str, client_order_id: str) -> Optional[dict]:
        """A ordem com esse clientOrderId já existe? `None` = não existe.

        É esta consulta — não a recusa da Binance — que garante a idempotência,
        pelo motivo explicado no cabeçalho do módulo. Vale para ordem já
        preenchida, cancelada ou ainda aberta."""
        from binance.exceptions import BinanceAPIException

        try:
            return self._cliente.get_order(symbol=symbol, origClientOrderId=client_order_id)
        except BinanceAPIException as e:
            if e.code == -2013:  # "Order does not exist."
                return None
            raise ErroCorretora(f"{symbol}: {e.message} (código {e.code})") from e

    def listar_ordens(self, symbol: str, limite: int = 1000) -> list:
        """Histórico de ordens do par (todas, não só as nossas — quem filtra
        pelo prefixo é a reconciliação)."""
        return self._cliente.get_all_orders(symbol=symbol, limit=limite)

    def listar_trades(self, symbol: str, limite: int = 1000) -> list:
        """Execuções reais, com `commission` e `commissionAsset`.

        É a única fonte que diz quanto REALMENTE entrou na carteira: a taxa da
        compra é cobrada no ativo comprado, então o `executedQty` da ordem é
        maior do que o recebido. Ver cabeçalho de `reconciliacao.py`."""
        return self._cliente.get_my_trades(symbol=symbol, limit=limite)

    def ordem_mercado(self, symbol: str, lado: str, qty: Decimal, client_order_id: str) -> dict:
        from binance.exceptions import BinanceAPIException

        try:
            return self._cliente.create_order(
                symbol=symbol,
                side=lado,
                type="MARKET",
                quantity=format(qty.normalize(), "f"),  # 'f' evita notação científica
                newClientOrderId=client_order_id,
            )
        except BinanceAPIException as e:
            # Duplicidade não é falha: é a idempotência funcionando.
            duplicada = "duplicate" in str(e.message).lower()
            raise ErroCorretora(f"{symbol}: {e.message} (código {e.code})", duplicada) from e


# ---------------------------------------------------------------------------
# Resultado e envio
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ResultadoOrdem:
    status: str              # executada | duplicada | recusada | erro
    symbol: str
    lado: str
    client_order_id: str
    qty: Decimal = Decimal(0)
    preco_medio: Decimal = Decimal(0)
    motivo: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "executada"

    @property
    def valor(self) -> Decimal:
        return self.qty * self.preco_medio


def _preco_medio(resposta: dict) -> tuple[Decimal, Decimal]:
    """Preço médio ponderado e quantidade, a partir dos `fills` reais.

    Uma ordem a mercado pode ser preenchida em vários pedaços, a preços
    diferentes. É este preço — não o do ticker — que precisa ser comparado com o
    slippage que o backtest assumiu (seção 3.3 do plano)."""
    fills = resposta.get("fills") or []
    if fills:
        total_qty = sum(Decimal(str(f["qty"])) for f in fills)
        total_valor = sum(Decimal(str(f["qty"])) * Decimal(str(f["price"])) for f in fills)
        if total_qty > 0:
            return total_valor / total_qty, total_qty

    # Sem fills detalhados: cai para os campos agregados da resposta.
    qty = Decimal(str(resposta.get("executedQty", "0")))
    valor = Decimal(str(resposta.get("cummulativeQuoteQty", "0")))
    return (valor / qty if qty > 0 else Decimal(0)), qty


def enviar_ordem(corretora: Corretora, symbol: str, lado: str, qty: Decimal,
                 candle, client_order_id: Optional[str] = None) -> ResultadoOrdem:
    """Envia uma ordem a mercado já dimensionada e devolve o resultado.

    Nunca levanta exceção por falha da corretora: devolve `ResultadoOrdem` com
    status. Um executor que morre no meio de um ciclo deixaria os outros ativos
    sem processar — melhor registrar a falha e seguir.
    """
    coid = client_order_id or id_ordem(symbol, candle, lado)

    if qty <= 0:
        return ResultadoOrdem("recusada", symbol, lado, coid, motivo="quantidade zero")

    # Idempotência: essa ordem já foi enviada neste candle? Perguntamos à
    # corretora, não à nossa memória. Falha nesta consulta ABORTA o envio ("fail
    # closed"): sem conseguir verificar, o risco de comprar em dobro é pior do
    # que o de perder uma entrada.
    try:
        if corretora.buscar_ordem(symbol, coid) is not None:
            return ResultadoOrdem("duplicada", symbol, lado, coid,
                                  motivo="ordem com este id já existe na corretora")
    except Exception as e:
        return ResultadoOrdem("erro", symbol, lado, coid,
                              motivo=f"não deu pra verificar duplicidade, ordem NÃO enviada "
                                     f"({type(e).__name__}: {e})")

    try:
        resposta = corretora.ordem_mercado(symbol, lado, qty, coid)
    except ErroCorretora as e:
        if e.duplicada:
            # Já existe ordem com esse id neste candle: a proteção agiu.
            return ResultadoOrdem("duplicada", symbol, lado, coid, motivo=str(e))
        return ResultadoOrdem("erro", symbol, lado, coid, motivo=str(e))
    except Exception as e:  # rede, timeout, resposta malformada
        return ResultadoOrdem("erro", symbol, lado, coid, motivo=f"{type(e).__name__}: {e}")

    preco_medio, qty_exec = _preco_medio(resposta)

    if qty_exec <= 0:
        return ResultadoOrdem("erro", symbol, lado, coid,
                              motivo=f"corretora aceitou mas não preencheu nada "
                                     f"(status={resposta.get('status')})")

    return ResultadoOrdem("executada", symbol, lado, coid, qty_exec, preco_medio)


def comprar(corretora: Corretora, symbol: str, valor_usd, candle) -> ResultadoOrdem:
    """Compra `valor_usd` do par, respeitando os filtros. Ver `calcular_quantidade`."""
    coid = id_ordem(symbol, candle, COMPRA)
    try:
        filtros = extrair_filtros(corretora.info_symbol(symbol))
        preco = corretora.preco(symbol)
    except Exception as e:
        return ResultadoOrdem("erro", symbol, COMPRA, coid, motivo=f"{type(e).__name__}: {e}")

    q = calcular_quantidade(valor_usd, preco, filtros)
    if not q.aceita:
        return ResultadoOrdem("recusada", symbol, COMPRA, coid, motivo=q.motivo)

    return enviar_ordem(corretora, symbol, COMPRA, q.qty, candle, coid)


def vender_tudo(corretora: Corretora, symbol: str, candle) -> ResultadoOrdem:
    """Vende todo o saldo do ativo base do par (fechamento total da posição)."""
    coid = id_ordem(symbol, candle, VENDA)
    try:
        info = corretora.info_symbol(symbol)
        filtros = extrair_filtros(info)
        saldo = corretora.saldo(info["baseAsset"])
    except Exception as e:
        return ResultadoOrdem("erro", symbol, VENDA, coid, motivo=f"{type(e).__name__}: {e}")

    q = quantidade_para_vender(saldo, filtros)
    if not q.aceita:
        return ResultadoOrdem("recusada", symbol, VENDA, coid, motivo=q.motivo)

    return enviar_ordem(corretora, symbol, VENDA, q.qty, candle, coid)
