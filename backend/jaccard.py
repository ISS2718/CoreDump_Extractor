import csv
from collections import defaultdict

# --- Configurações ---
ARQUIVO_ANTIGO = 'antigo.csv'
ARQUIVO_NOVO = 'novo.csv'
LIMIAR_SIMILARIDADE = 0.7  # THRESHOLD: 70% de similaridade

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

def calcular_jaccard(conjunto_a, conjunto_b):
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
        mapeamento, novos, desaparecidos = reconciliar_clusters(
            clusters_antigos, 
            clusters_novos, 
            LIMIAR_SIMILARIDADE
        )
        
        # 4. Exibe os resultados formatados
        exibir_resultados(mapeamento, novos, desaparecidos)