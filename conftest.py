# Garante que a raiz do projeto está no sys.path para os testes importarem os
# módulos de topo (estrategia_core, otimizador_v4, config_v4, ...).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
