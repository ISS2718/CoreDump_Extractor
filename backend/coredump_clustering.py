import os
import logging
import shutil
import time
from datetime import datetime

try:
    import db_manager
except ImportError:
    logging.error("Erro: db_manager.py não encontrado. Certifique-se de que ele está no mesmo diretório ou no PYTHONPATH.")
    exit(1)

# Importa a biblioteca de clusterização
try:
    from damicore.damicore import damicore
except ImportError:
    logging.error("Erro: damicore.py não encontrado. Certifique-se de que ele está no mesmo diretório ou no PYTHONPATH.")
    exit(1)

# --- Configurações do Processador ---

# Gatilho Híbrido:
# 1. Quantidade Mínima: Dispara se houver pelo menos X novos coredumps
MIN_NEW_COREDUMPS_TRIGGER = 10
# 2. Tempo Máximo: Dispara se já passou X segundos desde a última execução (e há pelo menos 1 novo)
# 86400 segundos = 24 horas
MAX_TIME_SINCE_LAST_RUN_SECONDS = 86400

# Diretórios e Arquivos
TEMP_PROCESSING_DIR = "db/damicore/processing_temp" # Pasta temporária para o snapshot
REPORTS_DIR = "db/damicore/reports"     # Pasta para salvar os artefatos da DAMICORE
STATE_FILE_PATH = "db/damicore/state.txt"           # Arquivo simples para guardar o timestamp da última execução

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

def check_trigger():
    """Verifica se a clusterização deve ser disparada (gatilho híbrido)."""
    print("--- Verificando gatilho para iniciar a clusterização ---")

    unclustered_coredumps = db_manager.get_unclustered_coredumps()
    unclustered_count = len(unclustered_coredumps)

    if unclustered_count == 0:
        print("Nenhum coredump novo para processar. Aguardando...")
        return False, 0

    # 1. Gatilho por Quantidade
    if unclustered_count >= MIN_NEW_COREDUMPS_TRIGGER:
        print(f"Gatilho ativado: {unclustered_count} novos coredumps (mínimo era {MIN_NEW_COREDUMPS_TRIGGER}).")
        return True, unclustered_count

    # 2. Gatilho por Tempo
    try:
        with open(STATE_FILE_PATH, 'r') as f:
            last_run_timestamp = float(f.read().strip())
    except (FileNotFoundError, ValueError):
        last_run_timestamp = 0 # Se o arquivo não existe ou está inválido, considera que nunca rodou

    time_since_last_run = time.time() - last_run_timestamp
    if time_since_last_run > MAX_TIME_SINCE_LAST_RUN_SECONDS:
        print(f"Gatilho ativado: se passaram {time_since_last_run:.0f}s desde a última execução (máximo era {MAX_TIME_SINCE_LAST_RUN_SECONDS}s).")
        return True, unclustered_count

    print(f"Gatilho não ativado. {unclustered_count} novos coredumps. Aguardando mais coredumps ou o tempo limite.")
    return False, 0

def prepare_snapshot_directory():
    """
    Cria um diretório temporário e copia TODOS os coredumps (clusterizados e não clusterizados)
    para ele, criando um snapshot para a análise.
    Retorna um mapa de 'nome_do_arquivo' -> 'coredump_id' para uso posterior.
    """
    print(f"\n--- Preparando diretório de snapshot em '{TEMP_PROCESSING_DIR}' ---")
    if os.path.exists(TEMP_PROCESSING_DIR):
        shutil.rmtree(TEMP_PROCESSING_DIR)
    os.makedirs(TEMP_PROCESSING_DIR)

    all_coredumps = db_manager.list_all_coredumps()
    if not all_coredumps:
        print("AVISO: Nenhum coredump encontrado no banco de dados.")
        return None, None

    filename_to_id_map = {}
    print(f"Copiando {len(all_coredumps)} coredumps para o snapshot...")

    for dump in all_coredumps:
        # Colunas do DB: coredump_id(0), mac(1), fw_id(2), cluster_id(3), raw_dump_path(4), ...
        coredump_id = dump[0]
        source_path = dump[4]

        if not os.path.exists(source_path):
            print(f"AVISO: Arquivo do coredump ID {coredump_id} não encontrado em '{source_path}'. Pulando.")
            continue

        filename = os.path.basename(source_path)
        destination_path = os.path.join(TEMP_PROCESSING_DIR, filename)

        # Evita colisões de nomes de arquivos, embora seja improvável com timestamps
        if filename in filename_to_id_map:
            print(f"AVISO: Nome de arquivo duplicado '{filename}'. Apenas a última ocorrência será processada.")

        shutil.copy(source_path, destination_path)
        filename_to_id_map[filename] = coredump_id

    print("Snapshot criado com sucesso.")
    return filename_to_id_map

def run_damicore_clustering():
    """Executa a DAMICORE no diretório de snapshot."""
    print("\n--- Executando a clusterização com DAMICORE ---")
    os.makedirs(REPORTS_DIR, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Monta os argumentos para a DAMICORE
    args = [
        "--compressor", "zlib",
        "--level", "9",
        "--ncd-output", os.path.join(REPORTS_DIR, f"ncd_{timestamp}.csv"),
        "--tree-output", os.path.join(REPORTS_DIR, f"tree_{timestamp}.newick"),
        "--graph-image", os.path.join(REPORTS_DIR, f"graph_{timestamp}.png"),
        "--output", "clusters.out", # Arquivo de saída padrão
        TEMP_PROCESSING_DIR # O diretório com os dados do snapshot
    ]

    try:
        damicore.main(args)
        print("DAMICORE executado com sucesso.")
        return "clusters.out"
    except Exception as e:
        print(f"ERRO: A execução da DAMICORE falhou: {e}")
        return None

def process_clustering_results(output_file, filename_to_id_map):
    """Lê o arquivo de saída da DAMICORE e atualiza o banco de dados."""
    print("\n--- Processando resultados e atualizando o banco de dados ---")
    if not os.path.exists(output_file):
        print(f"ERRO: Arquivo de resultado '{output_file}' não encontrado.")
        return

    with open(output_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # Formato da linha: "nome_do_cluster: /caminho/para/o/arquivo.cdmp"
            cluster_name, filepath = line.split(': ')
            filename = os.path.basename(filepath)

            # 1. Encontrar o ID do coredump a partir do nome do arquivo
            coredump_id = filename_to_id_map.get(filename)
            if coredump_id is None:
                print(f"AVISO: Coredump '{filename}' do resultado não foi encontrado no mapa. Pulando.")
                continue

            # 2. Verificar se o cluster já existe no DB ou criá-lo
            cluster_record = db_manager.get_cluster_by_name(cluster_name)
            if cluster_record:
                cluster_id = cluster_record[0] # Pega o ID
            else:
                print(f"Novo cluster encontrado: '{cluster_name}'. Adicionando ao DB.")
                cluster_id = db_manager.add_cluster(cluster_name)
                if cluster_id is None:
                    print(f"ERRO: Falha ao adicionar o cluster '{cluster_name}' ao DB.")
                    continue
            
            # 3. Associar o coredump ao cluster no DB
            db_manager.assign_cluster_to_coredump(coredump_id, cluster_id)
            print(f"Coredump ID {coredump_id} ('{filename}') associado ao cluster '{cluster_name}' (ID: {cluster_id}).")

def cleanup():
    """Remove o diretório temporário e arquivos intermediários."""
    print("\n--- Limpando arquivos temporários ---")
    if os.path.exists(TEMP_PROCESSING_DIR):
        shutil.rmtree(TEMP_PROCESSING_DIR)
    if os.path.exists("clusters.out"):
        os.remove("clusters.out")
    print("Limpeza concluída.")

def main():
    """Função principal que orquestra todo o processo."""
    print("======================================================")
    print(f"Iniciando o processador de coredumps em {datetime.now()}")
    print("======================================================")

    # Garante que a estrutura do DB está ok
    db_manager.create_database()

    should_run, count = check_trigger()
    if not should_run:
        return

    filename_to_id_map = None
    try:
        filename_to_id_map = prepare_snapshot_directory()
        if not filename_to_id_map:
            print("Nenhum arquivo para processar. Abortando a execução.")
            return

        # output_file = run_damicore_clustering()

        # if output_file:
        #     process_clustering_results(output_file, filename_to_id_map)
        #     # Atualiza o timestamp da última execução bem-sucedida
        #     with open(STATE_FILE_PATH, 'w') as f:
        #         f.write(str(time.time()))
        #     print("\nProcesso de clusterização concluído com sucesso!")

    except Exception as e:
        print(f"\nERRO CRÍTICO no processo principal: {e}")
    finally:
        cleanup()


if __name__ == "__main__":
    # Você pode rodar este script periodicamente usando um agendador
    # (como o 'cron' no Linux) ou deixá-lo em um loop com 'time.sleep'.
    # Exemplo de loop simples:
    # while True:
    #    main()
    #    print("\nAguardando 60 segundos para a próxima verificação...\n\n")
    #    time.sleep(60)
    main()