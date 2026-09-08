# -*- coding: utf-8 -*-
"""Teste de fumaça da camada de ordens contra a Binance Spot **Testnet**.

Dinheiro de brinquedo: a testnet é um ambiente isolado, com saldo fictício, sem
qualquer ligação com a conta real. É o critério de aprovação da Fase 1 do
PLANO_EXECUCAO_AUTOMATICA.md.

O que ele prova, na ordem:
  1. conecta e lê o saldo;
  2. lê os filtros reais do par e dimensiona a ordem;
  3. COMPRA;
  4. repete a MESMA ordem (mesmo candle) — tem que ser recusada como duplicada,
     que é a prova da idempotência;
  5. VENDE tudo de volta, deixando a carteira como estava.

Uso:
    set -a && . ./testnet.env && set +a
    .venv/Scripts/python.exe testnet_smoke.py

Só toca a testnet: `CorretoraBinance(testnet=True)`. Se as variáveis apontarem
para a conta real por engano, o `--confirmar-real` inexistente garante que não
há caminho para isso aqui.
"""
import sys
from datetime import datetime, timezone
from decimal import Decimal

from executor_ordens import (
    CorretoraBinance, ErroCorretora, extrair_filtros, calcular_quantidade,
    comprar, vender_tudo, id_ordem, COMPRA,
)

PAR = "BTCUSDT"
VALOR_TESTE = Decimal("200")   # USDT de brinquedo


def linha(txt=""):
    print(txt, flush=True)


def main():
    linha("=" * 66)
    linha("  TESTE DE FUMACA — Binance Spot TESTNET (dinheiro ficticio)")
    linha("=" * 66)

    # 1. conexão -----------------------------------------------------------
    try:
        corretora = CorretoraBinance(testnet=True)
    except ErroCorretora as e:
        linha(f"\n[X] {e}")
        linha("\n    Crie o arquivo testnet.env (ja ignorado pelo git) com:")
        linha('        export BINANCE_API_KEY="sua_api_key"')
        linha('        export BINANCE_API_SECRET="seu_secret"')
        linha("    e carregue com:  set -a && . ./testnet.env && set +a")
        return 1

    info = corretora.info_symbol(PAR)
    base = info["baseAsset"]

    usdt = corretora.saldo("USDT")
    btc = corretora.saldo(base)
    linha(f"\n[1] Conectado. Saldo inicial: {usdt:.2f} USDT | {btc:.8f} {base}")

    if usdt < VALOR_TESTE:
        linha(f"\n[X] Saldo de brinquedo insuficiente ({usdt:.2f} < {VALOR_TESTE}).")
        linha("    A testnet zera as contas periodicamente — gere uma chave nova")
        linha("    em testnet.binance.vision para receber saldo ficticio de novo.")
        return 1

    # 2. filtros e dimensionamento ----------------------------------------
    filtros = extrair_filtros(info)
    preco = corretora.preco(PAR)
    q = calcular_quantidade(VALOR_TESTE, preco, filtros)
    linha(f"\n[2] Filtros de {PAR}: passo={filtros.passo_qty} | "
          f"minimo=US$ {filtros.valor_minimo}")
    linha(f"    Preco {preco:.2f} -> US$ {VALOR_TESTE} viram {q.qty} {base}"
          f"  ({'aceita' if q.aceita else 'RECUSADA: ' + q.motivo})")
    if not q.aceita:
        return 1

    # candle fixo: é o que faz a repetição do passo 4 colidir de propósito
    candle = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
    linha(f"    clientOrderId desta compra: {id_ordem(PAR, candle, COMPRA)}")

    # 3. compra ------------------------------------------------------------
    r1 = comprar(corretora, PAR, VALOR_TESTE, candle)
    linha(f"\n[3] COMPRA: {r1.status}")
    if not r1.ok:
        linha(f"    motivo: {r1.motivo}")
        return 1
    linha(f"    {r1.qty} {base} a {r1.preco_medio:.2f} = US$ {r1.valor:.2f}")
    deslize = (r1.preco_medio - preco) / preco * 100
    linha(f"    deslize vs. preco do ticker: {deslize:+.4f}%")

    # 4. idempotência ------------------------------------------------------
    r2 = comprar(corretora, PAR, VALOR_TESTE, candle)
    linha(f"\n[4] MESMA ordem de novo (simula cron rodando 2x): {r2.status}")
    if r2.status != "duplicada":
        linha(f"    [X] FALHOU — deveria ser 'duplicada', veio '{r2.status}'.")
        linha(f"        {r2.motivo}")
        linha("        Sem isso, o cron rodando duas vezes compraria em dobro.")
        return 1
    linha("    OK — a Binance recusou a segunda. Idempotencia funcionando.")

    # 5. desfaz ------------------------------------------------------------
    r3 = vender_tudo(corretora, PAR, candle + "-desfazer")
    linha(f"\n[5] VENDA de volta: {r3.status}")
    if r3.ok:
        linha(f"    {r3.qty} {base} a {r3.preco_medio:.2f} = US$ {r3.valor:.2f}")
    else:
        linha(f"    motivo: {r3.motivo}")

    linha(f"\n    Saldo final: {corretora.saldo('USDT'):.2f} USDT | "
          f"{corretora.saldo(base):.8f} {base}")
    linha("\n" + "=" * 66)
    linha("  FASE 1 APROVADA: ordem enviada, duplicata recusada, posicao desfeita.")
    linha("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
