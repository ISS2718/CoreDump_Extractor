"""cluster_reconciler.py

Reconciliação e classificação de evolução de clusters (coredumps) entre execuções.

Fluxo resumido: 
1. lê CSV do clusterizador (linhas: <arquivo>,<cluster_id>).
2. constrói mapas cluster.
4. calcula métricas (Jaccard/Overlap).
5. classifica (evolução, crescimento, divisão, fusão, mudança drástica).
6. registra resultados.

Variáveis de ambiente relevantes: (nenhuma atualmente). Valores de limiares são constantes internas.

TODO: permitir sobreposição de configuração via variáveis de ambiente.
TODO: adicionar validação de integridade dos CSV (linhas inválidas / duplicadas).
"""

from __future__ import annotations


import csv
import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, Set, Tuple, List, Union, Optional, Iterable, Any

# -----------------------------------------------------------------------------
# Constantes configuráveis (limiares e paths de exemplo)
# -----------------------------------------------------------------------------
ARQUIVO_ANTIGO: str = "antigo.csv"  # Arquivo CSV origem (ex: execução anterior)
ARQUIVO_NOVO: str = "novo.csv"      # Arquivo CSV destino (execução atual)
LIMIAR_SIMILARIDADE: float = 0.7     # Similaridade mínima (Jaccard) para evolução direta

# Limiars abordagem mista (Jaccard + Overlap)
OVERLAP_LIMIAR_CRESCIMENTO: float = 0.9    # Overlap para evolução/crescimento (antigo contido)
JACCARD_LIMIAR_CRESCIMENTO: float = 0.4    # Jaccard mínimo ainda aceito em crescimento
OVERLAP_LIMIAR_SPLIT_MAX: float = 0.6      # Nenhum novo acima => pode ser divisão
SPLIT_COBERTURA_MINIMA: float = 0.8        # Cobertura mínima do antigo por vários novos para split
OVERLAP_LIMIAR_MUDANCA: float = 0.5        # Overlap mínimo para considerar mudança drástica
MERGE_OVERLAP_MIN: float = 0.5             # Overlap mínimo de antigos contribuindo para fusão
MERGE_COBERTURA_MIN: float = 0.8           # Cobertura mínima do novo por vários antigos para fusão

# -----------------------------------------------------------------------------
# Logger
# -----------------------------------------------------------------------------
logger = logging.getLogger("backend.cluster_reconciler")

# -----------------------------------------------------------------------------
# Type aliases
# -----------------------------------------------------------------------------
ClusterMap = Dict[str, Set[str]]
ReconMap = Dict[str, Dict[str, Union[str, List[str], float, None]]]


def carregar_clusters_de_arquivo(caminho_arquivo: Union[str, Path]) -> Optional[ClusterMap]:
    """Lê CSV (linhas: <arquivo>,<cluster_id>) e retorna mapa cluster->set(arquivos)."""
    clusters: Dict[str, Set[str]] = defaultdict(set)
    path = Path(caminho_arquivo)
    try:
        with path.open("r", newline="") as f:
            leitor_csv = csv.reader(f)
            for row in leitor_csv:
                if len(row) != 2:
                    # Linha inválida poderia ser descartada; manter log para ajustarmos parser futuramente
                    logger.warning("Linha ignorada em %s: %s", path, row)
                    continue
                nome_arquivo, cluster_id = row
                clusters[cluster_id.strip()].add(nome_arquivo.strip())
    except FileNotFoundError:
        logger.error("Arquivo não encontrado: %s", path)
        return None
    except Exception:  # Caso inesperado
        logger.exception("Falha ao carregar clusters do arquivo: %s", path)
        return None
    return dict(clusters)

def calcular_jaccard(conjunto_a: Set[str], conjunto_b: Set[str]) -> float:
    """Retorna índice de Jaccard entre dois conjuntos."""
    intersecao = conjunto_a.intersection(conjunto_b)
    uniao = conjunto_a.union(conjunto_b)
    
    # Previne divisão por zero se ambos os conjuntos forem vazios
    if not uniao:
        return 1.0 if not intersecao else 0.0
        
    return len(intersecao) / len(uniao)

def calcular_coeficiente_sobreposicao(conjunto_a: Set[str], conjunto_b: Set[str]) -> float:
    """Coef. Sobreposição (|A∩B| / min(|A|,|B|))."""
    if not conjunto_a and not conjunto_b:
        return 1.0
    if not conjunto_a or not conjunto_b:
        return 0.0
    intersecao = conjunto_a.intersection(conjunto_b)
    return len(intersecao) / min(len(conjunto_a), len(conjunto_b))

def reconciliar_clusters(
    clusters_antigos: Optional[ClusterMap],
    clusters_novos: ClusterMap,
    threshold: float,
) -> Tuple[Dict[str, Dict[str, Union[str, float]]], List[str], List[str]]:
    """Mapeia clusters (Jaccard) entre execuções para evolução simples."""
    # Se clusters_antigos for vazio ou None, todos os clusters_novos são novos
    if not clusters_antigos:
        return {}, list(clusters_novos.keys()), []

    mapeamento_final = {}
    clusters_antigos_mapeados = set()
    
    # Itera sobre cada cluster antigo para encontrar sua melhor correspondência no novo conjunto
    for id_antigo, arquivos_antigos in clusters_antigos.items():
        melhor_correspondencia = None
        maior_similaridade = -1.0
        
        for id_novo, arquivos_novos in clusters_novos.items():
            similaridade = calcular_jaccard(arquivos_antigos, arquivos_novos)
            if similaridade > maior_similaridade:
                maior_similaridade = similaridade
                melhor_correspondencia = id_novo
        
        # Regra 1: Mapeamento por Evolução
        if maior_similaridade >= threshold:
            mapeamento_final[id_antigo] = {
                'novo_id': melhor_correspondencia,
                'similaridade': maior_similaridade
            }
            clusters_antigos_mapeados.add(id_antigo)

    # Identifica os clusters que não foram mapeados
    ids_novos_mapeados = {v['novo_id'] for v in mapeamento_final.values()}
    todos_ids_novos = set(clusters_novos.keys())
    todos_ids_antigos = set(clusters_antigos.keys())

    # Regra 2: Detecção de Cluster Novo
    clusters_recem_criados = list(todos_ids_novos - ids_novos_mapeados)
    
    # Regra 3: Identificar clusters que desapareceram
    clusters_desaparecidos = list(todos_ids_antigos - clusters_antigos_mapeados)
    
    return mapeamento_final, clusters_recem_criados, clusters_desaparecidos

# ----------------- Abordagem Mista (Jaccard + Coef. Sobreposição) -----------------
def reconciliar_clusters_misto(
    clusters_antigos: ClusterMap,
    clusters_novos: ClusterMap,
    jaccard_limiar: float = LIMIAR_SIMILARIDADE,
    overlap_limiar_crescimento: float = OVERLAP_LIMIAR_CRESCIMENTO,
    jaccard_limiar_crescimento: float = JACCARD_LIMIAR_CRESCIMENTO,
    overlap_split_max: float = OVERLAP_LIMIAR_SPLIT_MAX,
    split_cobertura_min: float = SPLIT_COBERTURA_MINIMA,
    overlap_mudanca_min: float = OVERLAP_LIMIAR_MUDANCA,
    merge_overlap_min: float = MERGE_OVERLAP_MIN,
    merge_cobertura_min: float = MERGE_COBERTURA_MIN,
) -> Tuple[ReconMap, List[str], List[str], List[Dict[str, Union[str, List[str], float]]]]:
    """Classifica evolução de clusters (misto Jaccard+Overlap)."""
    if not clusters_antigos:
        # Tudo é novo
        return {}, list(clusters_novos.keys()), [], []

    mapeamentos: Dict[str, Dict[str, Union[str, List[str], float]]] = {}
    clusters_antigos_classificados = set()

    # Pré-computa interseções e métricas
    intersec_matrix = {}
    for ida, set_a in clusters_antigos.items():
        intersec_matrix[ida] = {}
        for idn, set_b in clusters_novos.items():
            inter = set_a.intersection(set_b)
            if inter:
                jacc = calcular_jaccard(set_a, set_b)
                ov = calcular_coeficiente_sobreposicao(set_a, set_b)
                intersec_matrix[ida][idn] = {
                    'inter_size': len(inter),
                    'jaccard': jacc,
                    'overlap': ov,
                    'old_size': len(set_a),
                    'new_size': len(set_b)
                }

    # Classificação por cluster antigo
    for ida, candidatos in intersec_matrix.items():
        if not candidatos:
            # Sem interseção com nenhum novo -> potencial desaparecido (decidiremos depois)
            continue

        # Ordena candidatos por overlap depois jaccard e tamanho de interseção
        ordenados = sorted(
            candidatos.items(),
            key=lambda kv: (kv[1]['overlap'], kv[1]['jaccard'], kv[1]['inter_size']),
            reverse=True
        )
        melhor_id, melhor_info = ordenados[0]
        ov = melhor_info['overlap']
        jacc = melhor_info['jaccard']

        tipo = None
        destino: Union[str, List[str]] = melhor_id

        # 1. Evolução estável
        if ov >= overlap_limiar_crescimento and jacc >= jaccard_limiar:
            tipo = 'evolucao'
        # 2. Crescimento (antigo contido mas Jaccard menor pois novo cresceu muito)
        elif ov >= overlap_limiar_crescimento and jacc >= jaccard_limiar_crescimento:
            tipo = 'crescimento'
        else:
            # 3. Verifica divisão: nenhum overlap alto mas soma cobre grande parte do antigo
            if ov < overlap_split_max:
                cobertura = 0
                usados = []
                cobertos = set()
                for idn, info in sorted(candidatos.items(), key=lambda kv: kv[1]['inter_size'], reverse=True):
                    novos_itens = clusters_novos[idn].intersection(clusters_antigos[ida]) - cobertos
                    if not novos_itens:
                        continue
                    cobertos.update(novos_itens)
                    cobertura = len(cobertos) / len(clusters_antigos[ida])
                    usados.append(idn)
                    if cobertura >= split_cobertura_min:
                        break
                if cobertura >= split_cobertura_min and len(usados) > 1:
                    tipo = 'divisao'
                    destino = usados
            # 4. Mudança Drástica (não split claro, ainda alguma sobreposição)
            if not tipo and ov >= overlap_mudanca_min and jacc >= jaccard_limiar_crescimento:
                tipo = 'mudanca_drastica'

        if tipo:
            mapeamentos[ida] = {
                'novo_id': destino,
                'jaccard': jacc if isinstance(destino, str) else None,
                'overlap': ov if isinstance(destino, str) else None,
                'tipo': tipo
            }
            clusters_antigos_classificados.add(ida)

    # Identifica desaparecidos (não classificados)
    todos_antigos = set(clusters_antigos.keys())
    desaparecidos = sorted(list(todos_antigos - clusters_antigos_classificados))

    # Novos: IDs não apontados por nenhum mapeamento simples ou listados em divisão
    ids_destinos = set()
    for info in mapeamentos.values():
        if isinstance(info['novo_id'], list):
            ids_destinos.update(info['novo_id'])
        else:
            ids_destinos.add(info['novo_id'])
    todos_novos = set(clusters_novos.keys())
    novos = sorted(list(todos_novos - ids_destinos))

    # Detecção de fusões: novo cluster coberto por múltiplos antigos sem que já tenham sido classificados como divisão
    fusoes = []
    # Índice inverso overlap antigo->novo já está em intersec_matrix; criamos novo->antigos
    novo_para_antigos = defaultdict(list)
    for ida, cand in intersec_matrix.items():
        for idn, info in cand.items():
            novo_para_antigos[idn].append((ida, info))

    for idn, lista in novo_para_antigos.items():
        # Ordena por overlap decrescente
        contribs = sorted(lista, key=lambda kv: kv[1]['overlap'], reverse=True)
        candidatos_merge = [c for c in contribs if c[1]['overlap'] >= merge_overlap_min]
        if len(candidatos_merge) < 2:
            continue
        # Cobertura do novo cluster pela união dos antigos considerados
        cobertura_set = set()
        for ida, _info in candidatos_merge:
            cobertura_set.update(clusters_antigos[ida].intersection(clusters_novos[idn]))
        cobertura_rel = len(cobertura_set) / len(clusters_novos[idn]) if clusters_novos[idn] else 0
        if cobertura_rel >= merge_cobertura_min:
            antigos_ids = [ida for ida, _ in candidatos_merge]
            fusoes.append({'novo_cluster': idn, 'antigos': antigos_ids, 'cobertura': round(cobertura_rel, 3)})
            # Marca cada antigo que ainda não tem tipo (ou tem mudança/crescimento) como fundido
            for ida in antigos_ids:
                if ida in mapeamentos and mapeamentos[ida]['tipo'] in {'mudanca_drastica', 'crescimento'}:
                    # Atualiza tipo para fusão
                    mapeamentos[ida]['tipo'] = 'fundido_em'
                    mapeamentos[ida]['novo_id'] = idn
                elif ida not in mapeamentos:
                    mapeamentos[ida] = {
                        'novo_id': idn,
                        'jaccard': None,
                        'overlap': None,
                        'tipo': 'fundido_em'
                    }

    return mapeamentos, novos, desaparecidos, fusoes

def exibir_resultados_misto(
    mapeamentos: ReconMap,
    novos: Iterable[str],
    desaparecidos: Iterable[str],
    fusoes: Iterable[Dict[str, Any]],
) -> None:
    """Loga resumo da reconciliação mista."""
    logger.info("=== Reconciliação Mista (Jaccard + Overlap) ===")
    if not (mapeamentos or list(novos) or list(desaparecidos)):
        logger.info("Sem resultados para exibir.")
        return

    categorias: Dict[str, List[Tuple[str, Dict[str, Union[str, List[str], float, None]]]]] = defaultdict(list)
    for ida, info in mapeamentos.items():
        categorias[info['tipo']].append((ida, info))  # type: ignore[index]

    ordem = ["evolucao", "crescimento", "mudanca_drastica", "divisao", "fundido_em"]
    etiquetas = {
        "evolucao": "Evolução Estável",
        "crescimento": "Crescimento (superset)",
        "mudanca_drastica": "Mudança Drástica",
        "divisao": "Divisão (Split)",
        "fundido_em": "Fusão (Merge)",
    }
    for cat in ordem:
        if cat in categorias:
            logger.info("-- %s --", etiquetas[cat])
            for ida, info in categorias[cat]:
                destino = info["novo_id"]
                jacc = info.get("jaccard")
                ov = info.get("overlap")
                metricas = []
                if isinstance(jacc, float):
                    metricas.append(f"J={jacc:.2f}")
                if isinstance(ov, float):
                    metricas.append(f"O={ov:.2f}")
                metricas_str = f" [{' ,'.join(metricas)}]" if metricas else ""
                if isinstance(destino, list):
                    logger.info("Cluster antigo '%s' -> dividido em %s%s", ida, destino, metricas_str)
                else:
                    logger.info("Cluster antigo '%s' -> novo '%s' (%s)%s", ida, destino, info['tipo'], metricas_str)

    logger.info("-- Clusters Novos (sem mapeamento direto) --")
    novos_list = list(novos)
    if novos_list:
        for n in novos_list:
            logger.info("Novo cluster: '%s'", n)
    else:
        logger.info("Nenhum.")

    logger.info("-- Clusters Desaparecidos --")
    desap_list = list(desaparecidos)
    if desap_list:
        for d in desap_list:
            logger.info("Desapareceu: '%s'", d)
    else:
        logger.info("Nenhum.")

    logger.info("-- Fusões Detectadas (visão agregada) --")
    fusoes_list = list(fusoes)
    if fusoes_list:
        for f in fusoes_list:
            logger.info(
                "Novo cluster '%s' formado por antigos %s (cobertura %.3f)",
                f["novo_cluster"],
                f["antigos"],
                f["cobertura"],
            )
    else:
        logger.info("Nenhuma fusão detectada.")
    logger.info("===============================================")

def exibir_resultados(
    mapeamento: Dict[str, Dict[str, Union[str, float]]],
    novos: Iterable[str],
    desaparecidos: Iterable[str],
) -> None:
    """Loga resultados de reconciliação simples."""
    logger.info("--- Análise de Reconciliação de Clusters ---")
    if not mapeamento and not list(novos) and not list(desaparecidos):
        logger.info("Nenhuma informação de cluster para analisar.")
        return

    logger.info("-- Mapeamentos Encontrados (Evolução) --")
    if mapeamento:
        for id_antigo, info in mapeamento.items():
            logger.info(
                "Cluster antigo '%s' -> Cluster novo '%s' (Jaccard=%.2f)",
                id_antigo,
                info['novo_id'],
                info['similaridade'],
            )
    else:
        logger.info("Nenhum mapeamento de evolução encontrado acima do limiar.")

    novos_list = sorted(list(novos))
    logger.info("-- Clusters Novos Detectados --")
    if novos_list:
        for id_novo in novos_list:
            logger.info("Cluster novo detectado: '%s'", id_novo)
    else:
        logger.info("Nenhum cluster exclusivamente novo foi detectado.")

    desaparecidos_list = sorted(list(desaparecidos))
    logger.info("-- Clusters Desaparecidos --")
    if desaparecidos_list:
        for id_antigo in desaparecidos_list:
            logger.info("Cluster antigo desapareceu: '%s'", id_antigo)
    else:
        logger.info("Nenhum cluster antigo desapareceu.")
    logger.info("---------------------------------------------")

# function to create example CSV files for demonstration
def criar_arquivos_de_exemplo() -> None:
    """Gera CSVs de exemplo para execução isolada local."""
    dados_antigos: List[List[str]] = [
        ["coredump_001.bin", "0"],
        ["coredump_002.bin", "0"],
        ["coredump_003.bin", "1"],
        ["coredump_004.bin", "0"],
        ["coredump_005.bin", "1"],
        ["coredump_008.bin", "2"],
    ]
    dados_novos: List[List[str]] = [
        ["coredump_001.bin", "1"],
        ["coredump_002.bin", "1"],
        ["coredump_003.bin", "0"],
        ["coredump_004.bin", "1"],
        ["coredump_005.bin", "0"],
        ["coredump_006.bin", "2"],  # Novo cluster
        ["coredump_007.bin", "1"],  # Novo elemento em cluster existente
        ["coredump_009.bin", "2"],  # Novo cluster
    ]
    try:
        with Path(ARQUIVO_ANTIGO).open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(dados_antigos)
        with Path(ARQUIVO_NOVO).open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(dados_novos)
        logger.info("Arquivos de exemplo criados: %s, %s", ARQUIVO_ANTIGO, ARQUIVO_NOVO)
    except IOError:
        logger.exception("Erro ao criar arquivos de exemplo.")


def main() -> None:
    """Ponto de entrada isolado (evita efeitos colaterais em import)."""
    # Configuração básica de logging somente se root ainda não configurado
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    criar_arquivos_de_exemplo()
    clusters_antigos = carregar_clusters_de_arquivo(ARQUIVO_ANTIGO)
    clusters_novos = carregar_clusters_de_arquivo(ARQUIVO_NOVO)
    if clusters_antigos is None or clusters_novos is None:
        logger.error("Falha ao carregar dados de clusters. Abortando.")
        return

    logger.info(
        "+ Execução modo original (apenas Jaccard) | limiar=%.2f | antigos=%d | novos=%d",
        LIMIAR_SIMILARIDADE,
        len(clusters_antigos),
        len(clusters_novos),
    )
    mapeamento, novos, desaparecidos = reconciliar_clusters(
        clusters_antigos, clusters_novos, LIMIAR_SIMILARIDADE
    )
    exibir_resultados(mapeamento, novos, desaparecidos)

    logger.info("+ Execução modo misto (Jaccard + Overlap)")
    mapeamentos_misto, novos_m, desaparecidos_m, fusoes_m = reconciliar_clusters_misto(
        clusters_antigos, clusters_novos
    )
    exibir_resultados_misto(mapeamentos_misto, novos_m, desaparecidos_m, fusoes_m)

if __name__ == "__main__":  # Execução direta
    main()
