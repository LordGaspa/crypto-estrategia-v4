"""
Análise de padrões: top 100 melhores estratégias por ativo
Identifica quais combinações de parâmetros aparecem mais frequentemente nos melhores resultados
"""

import pandas as pd
import numpy as np
from pathlib import Path
from collections import Counter
import warnings
warnings.filterwarnings('ignore')

# Cores para output
class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    END = '\033[0m'
    BOLD = '\033[1m'

def analisar_top100_por_ativo():
    """Carrega todos os 22 ativos e extrai padrões dos top 100 de cada"""

    projeto_dir = Path(".")
    csv_files = sorted(projeto_dir.glob("otimizador_v4_*.csv"))
    # Exclui RESUMO_ATIVOS
    csv_files = [f for f in csv_files if 'RESUMO' not in f.name]

    print(f"{Colors.HEADER}{Colors.BOLD}==== ANÁLISE DE PADRÕES: TOP 100 MELHORES ESTRATÉGIAS POR ATIVO ===={Colors.END}\n")

    # Dados consolidados para análise global
    todos_top100 = []
    padroes_por_ativo = {}

    for csv_file in csv_files:
        ativo = csv_file.stem.replace("otimizador_v4_", "").replace("USDT", "")

        try:
            # Lê e ordena por Calmar (coluna usada na otimização)
            df = pd.read_csv(csv_file, dtype={
                'media_rapida_per': int,
                'media_lenta_per': int,
                'media_filtro_tendencia_per': int,
                'atr_periodo': int,
                'atr_multiplicador': float,
            })

            # Top 100 ordenado por Calmar (ranking principal da otimização)
            top100 = df.nlargest(100, 'Calmar')[
                ['media_rapida_per', 'media_lenta_per', 'media_filtro_tendencia_per',
                 'atr_periodo', 'atr_multiplicador', 'Retorno_Anualizado_%', 'Calmar',
                 'DD_%', 'Num_Trades', 'Sharpe']
            ].reset_index(drop=True)

            top100['Ativo'] = ativo
            todos_top100.append(top100)

            # Análise de frequências para este ativo
            padroes = {
                'media_rapida_per': Counter(top100['media_rapida_per']),
                'media_lenta_per': Counter(top100['media_lenta_per']),
                'media_filtro_tendencia_per': Counter(top100['media_filtro_tendencia_per']),
                'atr_periodo': Counter(top100['atr_periodo']),
                'atr_multiplicador': Counter(top100['atr_multiplicador']),
            }
            padroes_por_ativo[ativo] = {
                'top100_df': top100,
                'frequencias': padroes,
                'calmar_media': top100['Calmar'].mean(),
                'retorno_media': top100['Retorno_Anualizado_%'].mean(),
                'num_trades_media': top100['Num_Trades'].mean(),
            }

            print(f"{Colors.OKBLUE}{ativo:12}{Colors.END} | "
                  f"Calmar médio: {top100['Calmar'].mean():6.2f} | "
                  f"Retorno médio: {top100['Retorno_Anualizado_%'].mean():7.1f}% | "
                  f"Trades médio: {top100['Num_Trades'].mean():5.1f} | "
                  f"Top result: {df.loc[df['Calmar'].idxmax(), 'Calmar']:.2f}")

        except Exception as e:
            print(f"{Colors.FAIL}Erro ao processar {ativo}: {e}{Colors.END}")
            continue

    # Consolidar todos os top100
    df_consolidado = pd.concat(todos_top100, ignore_index=True)

    print(f"\n{Colors.HEADER}{Colors.BOLD}==== ANÁLISE GLOBAL (TODOS OS ATIVOS COMBINADOS) ===={Colors.END}\n")

    print(f"Total de combinações no top100 (22 ativos × 100): {len(df_consolidado):,}")
    print(f"Calmar médio global: {df_consolidado['Calmar'].mean():.2f}")
    print(f"Retorno médio global: {df_consolidado['Retorno_Anualizado_%'].mean():.2f}%")
    print(f"DD médio global: {df_consolidado['DD_%'].mean():.2f}%\n")

    # Análise de frequências globais
    freq_global = {
        'media_rapida_per': Counter(df_consolidado['media_rapida_per']),
        'media_lenta_per': Counter(df_consolidado['media_lenta_per']),
        'media_filtro_tendencia_per': Counter(df_consolidado['media_filtro_tendencia_per']),
        'atr_periodo': Counter(df_consolidado['atr_periodo']),
        'atr_multiplicador': Counter(df_consolidado['atr_multiplicador']),
    }

    # Exibe os parâmetros mais frequentes
    print(f"{Colors.OKGREEN}{Colors.BOLD}PARÂMETROS MAIS FREQUENTES NOS TOP 100:{Colors.END}\n")

    params = ['media_rapida_per', 'media_lenta_per', 'media_filtro_tendencia_per',
              'atr_periodo', 'atr_multiplicador']
    param_names = ['Média Rápida (%)', 'Média Lenta (%)', 'Filtro Tendência (%)',
                   'ATR Período', 'ATR Multiplicador']

    for param, param_name in zip(params, param_names):
        top5 = freq_global[param].most_common(5)
        print(f"{Colors.BOLD}{param_name:30}{Colors.END}")
        for valor, freq in top5:
            percent = (freq / len(df_consolidado)) * 100
            print(f"  {str(valor):15} : {freq:4} vezes ({percent:5.1f}%)")
        print()

    # Padrão de combinação mais comum
    print(f"\n{Colors.OKGREEN}{Colors.BOLD}COMBINAÇÕES MAIS FREQUENTES:{Colors.END}\n")

    df_consolidado['combinacao'] = df_consolidado.apply(
        lambda row: f"R:{int(row['media_rapida_per'])}_L:{int(row['media_lenta_per'])}_"
                   f"F:{int(row['media_filtro_tendencia_per'])}_A:{int(row['atr_periodo'])}_"
                   f"M:{row['atr_multiplicador']}", axis=1
    )

    top_combinacoes = Counter(df_consolidado['combinacao']).most_common(20)
    for i, (combo, freq) in enumerate(top_combinacoes, 1):
        percent = (freq / len(df_consolidado)) * 100
        print(f"{i:2}. {combo:60} : {freq:3} vezes ({percent:5.1f}%)")

    # Salvar top 100 consolidado
    df_consolidado_sorted = df_consolidado.sort_values('Calmar', ascending=False)
    df_consolidado_sorted.to_csv('analise_top100_consolidado.csv', index=False)
    print(f"\n{Colors.OKGREEN}[OK] Resultado salvo em: analise_top100_consolidado.csv{Colors.END}")

    # Análise por grupo de liquidez
    print(f"\n{Colors.HEADER}{Colors.BOLD}==== ANÁLISE POR LIQUIDEZ ===={Colors.END}\n")

    # Mapear ativos para liquidez
    resumo = pd.read_csv('otimizador_v4_RESUMO_ATIVOS.csv')
    liquidez_map = dict(zip(resumo['Ativo'].str.replace('USDT', ''), resumo['Grupo_Liquidez']))

    df_consolidado['Liquidez'] = df_consolidado['Ativo'].map(liquidez_map)

    for liquidez in ['liquido', 'menos_liquido']:
        subset = df_consolidado[df_consolidado['Liquidez'] == liquidez]
        if len(subset) > 0:
            print(f"{Colors.BOLD}Grupo '{liquidez}' ({len(subset)} combinações):{Colors.END}")
            print(f"  Calmar médio: {subset['Calmar'].mean():.2f}")
            print(f"  Retorno médio: {subset['Retorno_Anualizado_%'].mean():.2f}%")
            print(f"  ATR Multiplicador mais comum: {Counter(subset['atr_multiplicador']).most_common(1)[0][0]}")
            print()

    # Retorna dados para análise posterior
    return padroes_por_ativo, freq_global, df_consolidado

if __name__ == "__main__":
    padroes_por_ativo, freq_global, df_consolidado = analisar_top100_por_ativo()

    print(f"\n{Colors.HEADER}{Colors.BOLD}==== CONCLUSÃO: PADRÃO UNIVERSAL DA ESTRATÉGIA ===={Colors.END}\n")

    print("A estratégia mais vencedora nos backtests tende a usar:")
    print(f"  • Média Rápida baixa (período curto) para sensibilidade")
    print(f"  • Média Lenta alta (período longo) para tendência robusta")
    print(f"  • ATR Multiplicador variado (não há consenso)")
    print(f"  • Melhor performance em ativos MAIS LÍQUIDOS (BTC, ETH, BNB, SOL)")
    print(f"\nVer arquivo 'analise_top100_consolidado.csv' para dados completos.")
