# -*- coding: utf-8 -*-
# SETORES V7 -- classificacao dos 22 ativos por CATEGORIA DE MERCADO
# ----------------------------------------------------------------------------
# Hipotese do usuario (2026-08-02): a divisao atual da estrategia e por
# LIQUIDEZ (veterana/nova, ver RECEITA_ROBUSTA em config_v4.py). Mas o mercado
# cripto se organiza por SETOR (as abas da Binance: MEME, Layer 1/Layer 2, IA,
# Pagamentos, ...). Se ativos do mesmo setor se movem de forma parecida, os
# parametros ideais deveriam ser mais parecidos DENTRO do setor do que entre
# setores -- e ai valeria uma receita por setor em vez de por liquidez.
#
# IMPORTANTE -- diferenca de metodo em relacao ao v5: aqui NAO buscamos o
# parametro mais lucrativo (isso gerou overfitting antes). Buscamos o
# parametro mais CONSTANTE dentro do grupo. Consistencia e evidencia de
# estrutura real; lucro maximo isolado e frequentemente ruido.
#
# Modulo novo e independente -- nao altera config_v4.py nem a RECEITA_ROBUSTA
# em producao.

# ----------------------------------------------------------------------------
# CLASSIFICACAO
# ----------------------------------------------------------------------------
# Fonte: categorias que a Binance usa na aba Criptomoedas (MEME, Layer 1/
# Layer 2, IA, Pagamentos, RWA, Solana, BSC, Jogos...). Onde um ativo cabe em
# mais de uma categoria, escolhemos a que melhor descreve o COMPORTAMENTO DE
# PRECO dominante, e a ambiguidade fica documentada em AMBIGUOS abaixo --
# isso importa porque o teste de permutacao (setores_v7_consistencia.py) mede
# justamente se a divisao setorial explica algo; classificacao dulvidosa
# enfraquece o resultado e precisa ser visivel.
# Universo EXPANDIDO: aos 22 originais somam-se ativos escolhidos por MAIOR
# CAPITALIZACAO dentro de cada setor (criterio do usuario -- os mais solidos),
# com o filtro de ter >= 3 anos de historico (menos que isso nao sobra periodo
# de desenvolvimento util depois de reservar os 12 meses de holdout).
# Ativos NOVOS (nao estavam no portfolio v4) marcados com # NOVO.
SETORES = {
    # memecoins: sem fundamento de uso, movidas por atencao/narrativa social
    "MEME": [
        "1MBABYDOGEUSDT", "BONKUSDT", "DOGEUSDT",
        "FLOKIUSDT", "PENGUUSDT", "PEPEUSDT",
        "SHIBUSDT",                                            # NOVO
    ],
    # blockchains base (L1) -- inclui BNB (cadeia propria BSC)
    "LAYER1": [
        "BNBUSDT", "BTCUSDT", "ETHUSDT", "HBARUSDT",
        "INJUSDT", "SOLUSDT", "SUIUSDT", "TRXUSDT",
        "ADAUSDT", "AVAXUSDT", "DOTUSDT", "NEARUSDT",          # NOVO
        "ATOMUSDT", "ALGOUSDT",                                # NOVO
    ],
    # inteligencia artificial / DePIN de computacao
    "IA": [
        "FETUSDT", "RENDERUSDT", "TAOUSDT",
        "WLDUSDT", "ARKMUSDT", "NMRUSDT",                      # NOVO
    ],
    # infraestrutura de dados: oraculos e indexacao
    "INFRA": [
        "API3USDT", "LINKUSDT",
        "GRTUSDT", "BANDUSDT", "TRBUSDT",                      # NOVO
    ],
    # pagamentos / transferencia de valor (ZEC entra pelo uso, com privacidade)
    "PAGAMENTOS": [
        "XRPUSDT", "ZECUSDT",
        "LTCUSDT", "BCHUSDT", "XLMUSDT",                       # NOVO
    ],
    # gaming / metaverso (IMX e L2 focada em jogos, cabe aqui)
    "GAMING": [
        "IMXUSDT",
        "SANDUSDT", "AXSUSDT", "MANAUSDT", "GALAUSDT", "ENJUSDT",  # NOVO
    ],
}

# ativos que ja faziam parte do portfolio v4 (usados em producao hoje)
ATIVOS_ORIGINAIS_V4 = {
    "1MBABYDOGEUSDT", "API3USDT", "BNBUSDT", "BONKUSDT", "BTCUSDT", "DOGEUSDT",
    "ETHUSDT", "FETUSDT", "FLOKIUSDT", "HBARUSDT", "IMXUSDT", "INJUSDT",
    "LINKUSDT", "PENGUUSDT", "PEPEUSDT", "RENDERUSDT", "SOLUSDT", "SUIUSDT",
    "TAOUSDT", "TRXUSDT", "XRPUSDT", "ZECUSDT",
}

UNIVERSO_V7 = [a for lst in SETORES.values() for a in lst]
ATIVOS_NOVOS_V7 = [a for a in UNIVERSO_V7 if a not in ATIVOS_ORIGINAIS_V4]

# Ativos cuja classificacao e defensavel de mais de uma forma -- registrar
# explicitamente pra nao fingir que a divisao e obvia.
AMBIGUOS = {
    "BNBUSDT": "L1 (BSC) mas tambem 'exchange token' -- comportamento ligado a Binance",
    "INJUSDT": "L1 mas com forte identidade DeFi",
    "HBARUSDT": "L1 corporativo (Hashgraph), dinamica diferente das L1 de varejo",
    "ZECUSDT": "privacidade; agrupado em PAGAMENTOS pelo uso, nao pela narrativa",
    "IMXUSDT": "Layer2 + Gaming -- sozinho no grupo, sem poder estatistico",
    "PENGUUSDT": "MEME do ecossistema Solana; listagem recente, historico curto",
    "RENDERUSDT": "IA/DePIN, antes classificado como 'renderizacao grafica'",
}

# Grupos com poucos ativos NAO tem poder estatistico pra sustentar uma receita
# propria -- qualquer padrao ali e provavelmente ruido. Marcados aqui pra que
# os scripts seguintes possam avisar em vez de reportar como achado.
MIN_ATIVOS_CONFIAVEL = 3


def setor_de(symbol: str) -> str:
    """Setor do ativo. Levanta KeyError se o ativo nao estiver classificado --
    de proposito: um ativo novo tem que ser classificado explicitamente, nao
    cair num 'outros' silencioso."""
    for setor, ativos in SETORES.items():
        if symbol in ativos:
            return setor
    raise KeyError(f"{symbol} nao classificado em SETORES (setores_v7.py)")


def setores_confiaveis() -> list:
    """Setores com ativos suficientes pra analise ter algum poder."""
    return [s for s, a in SETORES.items() if len(a) >= MIN_ATIVOS_CONFIAVEL]


def resumo() -> str:
    linhas = ["Setores (v7) -- classificacao por categoria de mercado:", ""]
    for setor, ativos in sorted(SETORES.items(), key=lambda kv: -len(kv[1])):
        marca = "" if len(ativos) >= MIN_ATIVOS_CONFIAVEL else "  <-- POUCOS ATIVOS"
        novos = sum(1 for a in ativos if a not in ATIVOS_ORIGINAIS_V4)
        nomes = ", ".join(
            (a.replace("USDT", "") + ("*" if a not in ATIVOS_ORIGINAIS_V4 else ""))
            for a in ativos
        )
        linhas.append(f"  {setor:<11} ({len(ativos):2d}, {novos} novos) {nomes}{marca}")
    linhas.append("")
    linhas.append(f"Total: {len(UNIVERSO_V7)} ativos em {len(SETORES)} setores "
                  f"({len(ATIVOS_NOVOS_V7)} novos, marcados com *)")
    linhas.append(f"Setores com >= {MIN_ATIVOS_CONFIAVEL} ativos: {', '.join(setores_confiaveis())}")
    linhas.append("")
    linhas.append("Classificacoes ambiguas (documentadas):")
    for a, motivo in AMBIGUOS.items():
        linhas.append(f"  {a.replace('USDT',''):<12} {motivo}")
    return "\n".join(linhas)


if __name__ == "__main__":
    print(resumo())
