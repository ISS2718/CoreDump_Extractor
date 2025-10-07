import os
import logging
from pathlib import Path
import shutil
import subprocess
import time
from datetime import datetime

try:
    import db_manager
except ImportError:
    logging.error("Erro: db_manager.py não encontrado. Certifique-se de que ele está no mesmo diretório ou no PYTHONPATH.")
    exit(1)

try:
    from cluster_sincronyzer import processar_reconciliacao
except ImportError:
    logging.error("Erro: cluster_sincronyzer.py não encontrado. Certifique-se de que ele está no mesmo diretório ou no PYTHONPATH.")
    exit(1)

# --- Configurações do Processador ---

# Imagem Docker da DAMICORE (você pode construir sua própria imagem se necessário) 
DAMICORE_DOCKER_IMAGE = "damicore-python"

# Gatilho Híbrido:
# 1. Quantidade Mínima: Dispara se houver pelo menos X novos coredumps
MIN_NEW_COREDUMPS_TRIGGER = 5
# 2. Tempo Máximo: Dispara se já passou X segundos desde a última execução (e há pelo menos 1 novo)
# 86400 segundos = 24 horas
MAX_TIME_SINCE_LAST_RUN_SECONDS = 60 * 5

# Diretórios e Arquivos
TEMP_PROCESSING_DIR = "db/damicore/processing_temp" # Pasta temporária para o snapshot
STATE_FILE_PATH = "db/damicore/state.txt"           # Arquivo simples para guardar o timestamp da última execução
CLUSTER_OUTPUT_FILE = "db/damicore/clusters.csv"  # Arquivo de saída padrão da DAMICORE (se não especificado)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

def check_trigger():
    """Verifica se a clusterização deve ser disparada (gatilho híbrido)."""
    print("--- Verificando gatilho para iniciar a clusterização ---")

    all_coredumps = db_manager.list_all_coredumps()
    if len(all_coredumps) <  2:
        print("Número insuficiente de coredumps no banco de dados para clusterização (mínimo 2).")
        return False, 0

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
        source_path = dump[5]

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

def run_damicore_clustering_docker():
    """Executa a DAMICORE dentro de um contêiner Docker."""
    print("\n--- Executando a clusterização com DAMICORE via Docker ---")
    
    # Caminho absoluto no HOST (Windows/Linux)
    host_project_root = Path.cwd().resolve()

    # Caminhos dentro do contêiner
    container_workdir = "/app"       # onde o código da DAMICORE está
    container_data_dir = "/data"     # volume mapeado do host
    container_processing_dir = f"{container_data_dir}/db/damicore/processing_temp"  # entrada
    container_cluster_output = f"{container_data_dir}/db/damicore/clusters.csv"  # saída

    # Argumentos para o main.py da DAMICORE
    damicore_args = [
        "--compressor", "zlib",
        "--level", "9",
        "--output", container_cluster_output,  # saída no host
        container_processing_dir  # diretório de entrada
    ]

    # Comando 'docker run'
    command = [
        "docker", "run",
        "--rm",
        "-v", f"{host_project_root}:{container_data_dir}",  # mapeia host → container
        "-w", container_workdir,
        DAMICORE_DOCKER_IMAGE,
        "python3", "main.py",
        *damicore_args
    ]

    print(f"  > Executando: {' '.join(command)}")
    
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=True, encoding='utf-8', timeout=600
        )
        print("Clusterização Docker concluída. Saída:")
        print(result.stdout)
        return container_cluster_output.replace(container_data_dir + "/", "")
    except subprocess.CalledProcessError as e:
        print(f"ERRO: A execução da DAMICORE via Docker falhou (código {e.returncode}).")
        print(f"Stderr: {e.stderr}")
        return None
    except FileNotFoundError:
        print("ERRO: Comando 'docker' não encontrado. O Docker está instalado e no PATH do sistema?")
        return None
    except Exception as e:
        print(f"ERRO inesperado ao executar o Docker: {e}")
        return None

def process_clustering_results(cluster_csv_path):
    """Lê o arquivo de saída da DAMICORE e atualiza o banco de dados."""
    print("\n--- Processando resultados e atualizando o banco de dados ---")
    if not os.path.exists(cluster_csv_path):
        print(f"ERRO: Arquivo de resultado '{cluster_csv_path}' não encontrado.")
        return

    processar_reconciliacao(cluster_csv_path)

    with open(STATE_FILE_PATH, 'w') as f:
        f.write(str(time.time()))
    
    print("\nProcesso de clusterização concluído com sucesso!")

def cleanup():
    """Remove o diretório temporário e arquivos intermediários."""
    print("\n--- Limpando arquivos temporários ---")
    if os.path.exists(TEMP_PROCESSING_DIR):
        shutil.rmtree(TEMP_PROCESSING_DIR)
    if os.path.exists(CLUSTER_OUTPUT_FILE):
        os.remove(CLUSTER_OUTPUT_FILE)
    print("Limpeza concluída.")

def main():
    """Função principal que orquestra todo o processo."""
    print("======================================================")
    print(f"Iniciando o processador de coredumps em {datetime.now()}")
    print("======================================================")

    # Garante que a estrutura do DB está ok
    db_manager.create_database()

    should_run, _ = check_trigger()
    if not should_run:
        return

    filename_to_id_map = None
    try:
        filename_to_id_map = prepare_snapshot_directory()
        if not filename_to_id_map:
            print("Nenhum arquivo para processar. Abortando a execução.")
            return

        output_file = run_damicore_clustering_docker()

        print(output_file)
        if output_file:
            process_clustering_results(output_file,)

    except Exception as e:
        print(f"\nERRO CRÍTICO no processo principal: {e}")
    finally:
        cleanup()


if __name__ == "__main__":
    # Você pode rodar este script periodicamente usando um agendador
    # (como o 'cron' no Linux) ou deixá-lo em um loop com 'time.sleep'.
    # Exemplo de loop simples:
    while True:
       main()
       print("\nAguardando 60 segundos para a próxima verificação...\n\n")
       time.sleep(60)
    main()