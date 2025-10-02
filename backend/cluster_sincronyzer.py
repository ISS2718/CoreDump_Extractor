import os
import csv
import logging
import time
from datetime import datetime
from collections import defaultdict

try:
    from db_manager import *
except ImportError:
    logging.error("Erro: db_manager.py não encontrado. Certifique-se de que ele está no mesmo diretório ou no PYTHONPATH.")
    exit(1)

try:
    from jaccard import *
except ImportError:
    logging.error("Erro: jaccard.py não encontrado. Certifique-se de que ele está no mesmo diretório ou no PYTHONPATH.")
    exit(1)

LIMIAR_SIMILARIDADE = 0.7  # THRESHOLD: 70% de similaridade

def extrair_clusters_do_db():
    """Extrai a estrutura de clusters atual do banco de dados."""
    clustered_dumps = get_clustered_coredumps()
    print(f"Clusters extraídos do DB: {clustered_dumps}.")
    clusters_antigos = defaultdict(set)
    for coredump_id, cluster_id in clustered_dumps:
        clusters_antigos[cluster_id].add(
            coredump_id)
    print(f"Clusters extraídos do DB: {clusters_antigos}.")
    return dict(clusters_antigos)

def criar_mapeamento_coredump_para_cluster(clusters_dict):
    """
    Inverte um dicionário de clusters para mapear cada coredump ao seu cluster.

    Args:
        clusters_dict (dict): Dicionário no formato {cluster_id: {set_of_coredump_ids}}.

    Returns:
        dict: Dicionário no formato {coredump_id: cluster_id}.
    """
    coredump_para_cluster_map = {}
    for cluster_id, coredump_ids_set in clusters_dict.items():
        for coredump_id in coredump_ids_set:
            coredump_para_cluster_map[coredump_id] = cluster_id
    return coredump_para_cluster_map

def gerar_nome_cluster_de_arquivo(coredump_id):
    """
    Gera um nome descritivo para um cluster a partir de um coredump membro.

    Args:
        coredump_id (int): O ID de um coredump que pertence ao novo cluster.

    Returns:
        str: Um nome de cluster gerado.
    """
    print(f"  -> Gerando nome de cluster a partir do coredump ID: {coredump_id}")
    info = get_coredump_info_by_id(coredump_id) # Busca o caminho no DB
    
    # info[0] é o raw_dump_path
    if info and info[0]:
        caminho_arquivo = info[0]
        
        # --- AQUI VOCÊ DEVE COLOCAR A SUA LÓGICA CUSTOMIZADA ---
        # Exemplo: Abrir o arquivo, ler uma linha, procurar um padrão, etc.
        # Por enquanto, nosso exemplo de lógica vai apenas usar o nome do arquivo.
        
        nome_base = os.path.basename(caminho_arquivo)
        # Remove a extensão e talvez um timestamp para um nome mais limpo
        nome_descritivo = f"Cluster_{os.path.splitext(nome_base)[0]}"
        return nome_descritivo
        
    # Fallback caso o coredump não seja encontrado
    timestamp = int(time.time())
    return f"Cluster_Inesperado_{timestamp}"

def aplicar_resultados_reconciliacao(mapeamento, novos, desaparecidos, coredumps_no_novo_resultado, clusters_novos):
    """Aplica as mudanças calculadas pela reconciliação no banco de dados."""
    print("\n--- Aplicando Resultados da Reconciliação ao Banco de Dados ---")
    
    # Passo 1: Lidar com clusters desaparecidos (DELETE)
    # Primeiro desassocia os coredumps, depois deleta o cluster para não violar a FK.
    for id_antigo in desaparecidos:
        nome_cluster = get_cluster_name(id_antigo)
        print(f"Ação: Cluster '{nome_cluster}' (ID:{id_antigo}) desapareceu.")
        unassign_cluster_from_coredumps(id_antigo)
        delete_cluster(id_antigo)
        
    # Passo 2: Lidar com clusters novos (INSERT)
    # Cria os novos clusters e guarda o ID do DB para o próximo passo.
    mapa_novo_temp_para_db = {}
    for id_novo_temp in novos:
        coredumps_neste_cluster = clusters_novos.get(id_novo_temp, set())

        if not coredumps_neste_cluster:
            # Caso raro de um cluster novo sem membros
            novo_nome = f"Cluster_Vazio_{id_novo_temp}_{int(time.time())}"
        else:
            # Pega um coredump qualquer do conjunto para servir de representante
            id_representante = next(iter(coredumps_neste_cluster))
            
            # Chama a função que você customizou para gerar o nome
            novo_nome = gerar_nome_cluster_de_arquivo(id_representante)

        novo_db_id = add_cluster(novo_nome)
        mapa_novo_temp_para_db[id_novo_temp] = novo_db_id
        
    # Passo 3: Atualizar a classificação dos coredumps (UPDATE)
    print("Ação: Atualizando classificação dos coredumps...")
    
    # Cria um mapa de ID temporário novo para ID permanente do DB
    mapa_final_temp_para_db = mapa_novo_temp_para_db
    for id_antigo_db, info in mapeamento.items():
        id_novo_temp = info['novo_id']
        mapa_final_temp_para_db[id_novo_temp] = id_antigo_db # Reutiliza o ID antigo do DB!
        nome_antigo = get_cluster_name(id_antigo_db)
        print(f"  - Mapeamento: Cluster '{nome_antigo}' (ID:{id_antigo_db}) evoluiu. Seu ID será mantido.")

    # Itera sobre o resultado da nova clusterização e aplica o mapeamento final
    for coredump_id, novo_cluster_temp_id in coredumps_no_novo_resultado.items():
        db_cluster_id = mapa_final_temp_para_db.get(novo_cluster_temp_id)
        
        if db_cluster_id:
            # Assegura que o coredump_id seja do tipo correto para a função de DB
            try:
                assign_cluster_to_coredump(int(coredump_id), db_cluster_id)
            except ValueError:
                print(f"AVISO: Coredump ID '{coredump_id}' não é um inteiro válido e será ignorado.")
        else:
            print(f"AVISO: Coredump ID {coredump_id} pertence a um cluster temporário '{novo_cluster_temp_id}' que não foi mapeado.")

def carregar_e_traduzir_clusters_novos(caminho_csv):
    """
    Lê o CSV da nova clusterização e traduz os caminhos de arquivo para IDs de coredump do DB.

    Args:
        caminho_csv (str): Caminho para o arquivo CSV no formato (raw_dump_path, temp_cluster_id).

    Returns:
        tuple: Contendo as duas estruturas de dados necessárias com IDs numéricos:
               - clusters_novos (dict): {temp_cluster_id: {set_of_integer_coredump_ids}}
               - coredumps_para_cluster (dict): {integer_coredump_id: temp_cluster_id}
    """
    # 1. Buscar todos os coredumps do DB para criar um mapa de tradução
    print("\nBuscando mapa de tradução (caminho -> ID) do banco de dados...")
    todos_os_coredumps = list_all_coredumps() 
    if not todos_os_coredumps:
        print("AVISO: Não há coredumps no banco de dados para mapear.")
        return {}, {}

    # O list_all_coredumps retorna (id, mac, fw_id, cluster_id, path, ...)
    path_para_id_map = {os.path.basename(row[5]): row[0] for row in todos_os_coredumps}
    print(f"Mapa de tradução: {path_para_id_map}")

    # 2. Ler o CSV e construir as estruturas de dados usando os IDs numéricos
    clusters_novos = defaultdict(set)
    coredumps_para_cluster = {}

    print(f"Lendo e traduzindo o arquivo de novos clusters: '{caminho_csv}'...")
    try:
        with open(caminho_csv, 'r', newline='', encoding='utf-8') as f:
            leitor_csv = csv.reader(f)
            for coredump_name, novo_cluster_temp_id in leitor_csv:
                coredump_name = coredump_name.strip()
                novo_cluster_temp_id = novo_cluster_temp_id.strip()

                # A etapa de tradução crucial acontece aqui!
                coredump_id_numerico = path_para_id_map.get(coredump_name)
                
                if coredump_id_numerico is not None:
                    # Usa o ID numérico em ambas as estruturas
                    clusters_novos[novo_cluster_temp_id].add(coredump_id_numerico)
                    coredumps_para_cluster[coredump_id_numerico] = novo_cluster_temp_id
                else:
                    print(f"  - Aviso: Coredump com caminho '{coredump_name}' do CSV não foi encontrado no DB e será ignorado.")

    except FileNotFoundError:
        print(f"ERRO: Arquivo de novos clusters '{caminho_csv}' não encontrado.")
        return {}, {}
    
    return dict(clusters_novos), coredumps_para_cluster

def processar_reconciliacao(caminho_csv_novo, similaridade_threshold=LIMIAR_SIMILARIDADE):
    """
    Executa o ciclo completo de reconciliação de clusters.

    Args:
        caminho_csv_novo (str): O caminho para o arquivo CSV com a nova clusterização.

    Returns:
        dict: Um dicionário com o sumário do que foi feito.
    """
    print("--- INICIANDO PROCESSO DE RECONCILIAÇÃO DE CLUSTERS ---")
    create_database()

    # 1. Extrai o estado "antigo" do banco de dados
    clusters_antigos_db = extrair_clusters_do_db()
    
    print("\n--- Estado Inicial dos Clusters no DB ---")
    if not clusters_antigos_db:
        print("Nenhum cluster classificado no banco de dados.")
    else:
        for cluster_id, coredumps in clusters_antigos_db.items():
            print(f"Cluster ID {cluster_id} ('{get_cluster_name(cluster_id)}'): {len(coredumps)} coredumps")

    # 2. Carrega os "novos" clusters do CSV, traduzindo para IDs numéricos
    clusters_novos, coredumps_no_novo_resultado = carregar_e_traduzir_clusters_novos(caminho_csv_novo)

    if not clusters_novos:
        print("\nNenhum novo cluster válido foi carregado. Encerrando o processo.")
        return {"status": "finalizado", "mensagem": "Nenhum dado novo para processar."}

    # 3. Executa a reconciliação
    mapeamento, novos, desaparecidos = reconciliar_clusters(
        clusters_antigos_db, 
        clusters_novos, 
        similaridade_threshold
    )

    # 4. Aplica os resultados
    aplicar_resultados_reconciliacao(mapeamento, novos, desaparecidos, coredumps_no_novo_resultado, clusters_novos)

    # 5. Verifica o estado final
    clusters_finais_db = extrair_clusters_do_db()
    print("\n--- Estado Final dos Clusters no DB ---")
    if not clusters_finais_db:
        print("Nenhum cluster classificado no banco de dados.")
    else:
        for cluster_id, coredumps in clusters_finais_db.items():
            print(f"Cluster ID {cluster_id} ('{get_cluster_name(cluster_id)}'): {len(coredumps)} coredumps")
            
    sumario = {
        "status": "sucesso",
        "clusters_mapeados": len(mapeamento),
        "clusters_novos_criados": len(novos),
        "clusters_removidos": len(desaparecidos)
    }
    return sumario

if __name__ == "__main__":
    print("Executando orquestrador_clusters.py em modo de teste...")
    
    # Você precisa de um arquivo de teste para que isso funcione
    arquivo_teste_csv = "db/damicore/clusters.csv"
    
    if os.path.exists(arquivo_teste_csv):
        resultado = processar_reconciliacao(arquivo_teste_csv)
        print("\n--- TESTE CONCLUÍDO ---")
        print(f"Resultado do processo: {resultado}")
    else:
        print(f"AVISO: Arquivo de teste '{arquivo_teste_csv}' não encontrado. Teste não executado.")


