import csv
from collections import defaultdict
from typing import Dict, Set, Tuple, List, Union

# --- Configurações ---
ARQUIVO_ANTIGO = 'antigo.csv'
ARQUIVO_NOVO = 'novo.csv'
LIMIAR_SIMILARIDADE = 0.7  # THRESHOLD: 70% de similaridade (Jaccard)

# --- Novos Limiars para abordagem mista ---
# Se overlap (coef. sobreposição) >= 0.9 e Jaccard >= 0.7 consideramos evolução estável.
OVERLAP_LIMIAR_CRESCIMENTO = 0.9   # Indica que o cluster antigo está quase totalmente contido no novo.
JACCARD_LIMIAR_CRESCIMENTO = 0.4   # Jaccard mínimo para ainda considerar crescimento (mesmo que tenha inflado muito).
OVERLAP_LIMIAR_SPLIT_MAX = 0.6     # Se nenhum novo cluster tem overlap acima disto, pode indicar divisão.
SPLIT_COBERTURA_MINIMA = 0.8       # Percentual mínimo do cluster antigo coberto pela soma de vários novos para classificar divisão.
OVERLAP_LIMIAR_MUDANCA = 0.5       # Overlap mínimo para considerar mudança drástica (não desapareceu, não split claro).
MERGE_OVERLAP_MIN = 0.5            # Overlap mínimo de antigos contribuindo para potencial fusão.
MERGE_COBERTURA_MIN = 0.8          # Cobertura mínima do novo cluster por vários antigos para marcar fusão.

def carregar_clusters_de_arquivo(caminho_arquivo):
    """
    Lê um arquivo CSV e o estrutura em um dicionário de clusters.

    Args:
        caminho_arquivo (str): O caminho para o arquivo CSV.

    Returns:
        dict: Um dicionário onde as chaves são os IDs dos clusters e os
              valores são conjuntos (sets) com os nomes dos arquivos.
              Ex: {'0': {'file1.bin', 'file2.bin'}}
    """
    clusters = defaultdict(set)
    try:
        with open(caminho_arquivo, 'r', newline='') as f:
            leitor_csv = csv.reader(f)
            for nome_arquivo, cluster_id in leitor_csv:
                clusters[cluster_id.strip()].add(nome_arquivo.strip())
    except FileNotFoundError:
        print(f"Erro: O arquivo '{caminho_arquivo}' não foi encontrado.")
        return None
    return dict(clusters)

def calcular_jaccard(conjunto_a: Set[str], conjunto_b: Set[str]) -> float:
    """
    Calcula o Índice de Jaccard entre dois conjuntos.

    Args:
        conjunto_a (set): O primeiro conjunto de itens.
        conjunto_b (set): O segundo conjunto de itens.

    Returns:
        float: O valor do Índice de Jaccard, entre 0.0 e 1.0.
    """
    intersecao = conjunto_a.intersection(conjunto_b)
    uniao = conjunto_a.union(conjunto_b)
    
    # Previne divisão por zero se ambos os conjuntos forem vazios
    if not uniao:
        return 1.0 if not intersecao else 0.0
        
    return len(intersecao) / len(uniao)

def calcular_coeficiente_sobreposicao(conjunto_a: Set[str], conjunto_b: Set[str]) -> float:
    """
    Calcula o Coeficiente de Sobreposição (Szymkiewicz–Simpson) entre dois conjuntos.

    Fórmula: |A ∩ B| / min(|A|, |B|)

    Interpretação:
        1.0 => Um conjunto está totalmente contido no outro.
        Alto valor mesmo com Jaccard moderado sugere CRESCIMENTO (o cluster velho foi englobado por um maior).
    """
    if not conjunto_a and not conjunto_b:
        return 1.0
    if not conjunto_a or not conjunto_b:
        return 0.0
    intersecao = conjunto_a.intersection(conjunto_b)
    return len(intersecao) / min(len(conjunto_a), len(conjunto_b))

def reconciliar_clusters(clusters_antigos, clusters_novos, threshold):
    """
    Executa o processo de reconciliação para mapear clusters entre duas execuções.

    Args:
        clusters_antigos (dict): Dicionário de clusters da execução antiga.
        clusters_novos (dict): Dicionário de clusters da execução nova.
        threshold (float): Limiar de similaridade Jaccard para considerar um mapeamento.

    Returns:
        tuple: Uma tupla contendo:
               - mapeamento_final (dict): Mapeamentos de clusters antigos para novos.
               - clusters_recem_criados (list): Lista de IDs de clusters novos sem mapeamento.
               - clusters_desaparecidos (list): Lista de IDs de clusters antigos sem mapeamento.
    """
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
    clusters_antigos: Dict[str, Set[str]],
    clusters_novos: Dict[str, Set[str]],
    jaccard_limiar: float = LIMIAR_SIMILARIDADE,
    overlap_limiar_crescimento: float = OVERLAP_LIMIAR_CRESCIMENTO,
    jaccard_limiar_crescimento: float = JACCARD_LIMIAR_CRESCIMENTO,
    overlap_split_max: float = OVERLAP_LIMIAR_SPLIT_MAX,
    split_cobertura_min: float = SPLIT_COBERTURA_MINIMA,
    overlap_mudanca_min: float = OVERLAP_LIMIAR_MUDANCA,
    merge_overlap_min: float = MERGE_OVERLAP_MIN,
    merge_cobertura_min: float = MERGE_COBERTURA_MIN,
) -> Tuple[
    Dict[str, Dict[str, Union[str, List[str], float]]],
    List[str],
    List[str],
    List[Dict[str, Union[str, List[str]]]]
]:
    """
    Reconcilia clusters usando simultaneamente Jaccard e Coef. de Sobreposição,
    classificando o tipo de evolução.

    Tipos possíveis:
        - evolucao: mantém estrutura com alta sobreposição e Jaccard alto.
        - crescimento: overlap alto (antigo contido) mas Jaccard moderado (novo muito maior).
        - mudanca_drastica: similaridade moderada, mas não encaixa em crescimento/evolução.
        - divisao: antigo distribuído em vários novos (split).
        - fundido_em (identificado post-processo se vários antigos formam um novo).
        - desapareceu / novo (listas separadas).
    """
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

def exibir_resultados_misto(mapeamentos, novos, desaparecidos, fusoes):
    print("\n=== Reconciliação Mista (Jaccard + Overlap) ===")
    if not (mapeamentos or novos or desaparecidos):
        print("Nada a exibir.")
        return

    categorias = defaultdict(list)
    for ida, info in mapeamentos.items():
        categorias[info['tipo']].append((ida, info))

    ordem = ['evolucao', 'crescimento', 'mudanca_drastica', 'divisao', 'fundido_em']
    etiquetas = {
        'evolucao': 'Evolução Estável',
        'crescimento': 'Crescimento (superset)',
        'mudanca_drastica': 'Mudança Drástica',
        'divisao': 'Divisão (Split)',
        'fundido_em': 'Fusão (Merge)'
    }
    for cat in ordem:
        if cat in categorias:
            print(f"\n-- {etiquetas[cat]} --")
            for ida, info in categorias[cat]:
                destino = info['novo_id']
                jacc = info.get('jaccard')
                ov = info.get('overlap')
                metricas = []
                if jacc is not None:
                    metricas.append(f"J={jacc:.2f}")
                if ov is not None:
                    metricas.append(f"O={ov:.2f}")
                metricas_str = (' [' + ', '.join(metricas) + ']') if metricas else ''
                if isinstance(destino, list):
                    print(f"Cluster antigo '{ida}' -> dividido em {destino}{metricas_str}")
                else:
                    print(f"Cluster antigo '{ida}' -> novo '{destino}' ({info['tipo']}){metricas_str}")

    print("\n-- Clusters Novos (sem mapeamento direto) --")
    if novos:
        for n in novos:
            print(f"Novo cluster: '{n}'")
    else:
        print("Nenhum.")

    print("\n-- Clusters Desaparecidos --")
    if desaparecidos:
        for d in desaparecidos:
            print(f"Desapareceu: '{d}'")
    else:
        print("Nenhum.")

    print("\n-- Fusões Detectadas (visão agregada) --")
    if fusoes:
        for f in fusoes:
            print(f"Novo cluster '{f['novo_cluster']}' formado por antigos {f['antigos']} (cobertura {f['cobertura']})")
    else:
        print("Nenhuma fusão detectada.")
    print("===============================================")

def exibir_resultados(mapeamento, novos, desaparecidos):
    """Formata e imprime os resultados da reconciliação."""
    print("\n--- Análise de Reconciliação de Clusters ---")
    
    if not mapeamento and not novos and not desaparecidos:
        print("\nNenhuma informação de cluster para analisar.")
        return

    print("\n-- Mapeamentos Encontrados (Evolução) --")
    if mapeamento:
        for id_antigo, info in mapeamento.items():
            similaridade_formatada = f"{info['similaridade']:.2f}"
            print(f"Cluster antigo '{id_antigo}' -> Cluster novo '{info['novo_id']}' (Similaridade Jaccard: {similaridade_formatada})")
    else:
        print("Nenhum mapeamento de evolução encontrado acima do limiar.")
        
    print("\n-- Clusters Novos Detectados --")
    if novos:
        for id_novo in sorted(novos):
            print(f"Cluster novo detectado: '{id_novo}'")
    else:
        print("Nenhum cluster exclusivamente novo foi detectado.")
        
    print("\n-- Clusters Desaparecidos --")
    if desaparecidos:
        for id_antigo in sorted(desaparecidos):
            print(f"Cluster antigo desapareceu: '{id_antigo}'")
    else:
        print("Nenhum cluster antigo desapareceu.")
        
    print("\n---------------------------------------------")

# function to create example CSV files for demonstration
def criar_arquivos_de_exemplo():
    """Cria os arquivos CSV de exemplo para demonstração."""
    dados_antigos = [
        ['coredump_001.bin', '0'],
        ['coredump_002.bin', '0'],
        ['coredump_003.bin', '1'],
        ['coredump_004.bin', '0'],
        ['coredump_005.bin', '1'],
        ['coredump_008.bin', '2'],
    ]
    
    dados_novos = [
        ['coredump_001.bin', '1'],
        ['coredump_002.bin', '1'],
        ['coredump_003.bin', '0'],
        ['coredump_004.bin', '1'],
        ['coredump_005.bin', '0'],
        ['coredump_006.bin', '2'], # Parte de um cluster novo
        ['coredump_007.bin', '1'], # Elemento novo em um cluster existente
        ['coredump_009.bin', '2'], # Parte de um cluster novo
    ]
    
    try:
        with open(ARQUIVO_ANTIGO, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(dados_antigos)
            
        with open(ARQUIVO_NOVO, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerows(dados_novos)
        print("Arquivos de exemplo 'antigo.csv' e 'novo.csv' criados com sucesso.")
    except IOError as e:
        print(f"Erro ao criar arquivos de exemplo: {e}")

# --- Bloco de Execução Principal ---
if __name__ == "__main__":
    # 1. Cria os arquivos de exemplo para que o script possa ser executado imediatamente
    criar_arquivos_de_exemplo()

    # 2. Carrega os dados dos clusters a partir dos arquivos CSV
    clusters_antigos = carregar_clusters_de_arquivo(ARQUIVO_ANTIGO)
    clusters_novos = carregar_clusters_de_arquivo(ARQUIVO_NOVO)
    
    # Prossegue apenas se os arquivos foram carregados com sucesso
    if clusters_antigos is not None and clusters_novos is not None:
        # 3. Executa a lógica de reconciliação
        print("\n>>> Resultado modo original (apenas Jaccard)")
        mapeamento, novos, desaparecidos = reconciliar_clusters(
            clusters_antigos,
            clusters_novos,
            LIMIAR_SIMILARIDADE
        )
        exibir_resultados(mapeamento, novos, desaparecidos)

        print("\n>>> Resultado modo misto (Jaccard + Overlap)")
        mapeamentos_misto, novos_m, desaparecidos_m, fusoes_m = reconciliar_clusters_misto(
            clusters_antigos,
            clusters_novos
        )
        exibir_resultados_misto(mapeamentos_misto, novos_m, desaparecidos_m, fusoes_m)
