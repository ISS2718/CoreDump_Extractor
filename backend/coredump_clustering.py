import os
import logging
from pathlib import Path
import shutil
import subprocess
import time
from datetime import datetime
from typing import Optional

try:  # Acesso ao banco de dados de coredumps
    import db_manager
except ImportError as e:
    logging.error("Erro ao importar db_manager: %s", e)
    raise SystemExit(1)

try:  # Função que faz a reconciliação / atualização de clusters no banco
    from cluster_sincronyzer import processar_reconciliacao
except ImportError as e:
    logging.error("Erro ao importar cluster_sincronyzer: %s", e)
    raise SystemExit(1)

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
TEMP_PROCESSING_DIR = "db/damicore/processing_temp"  # Pasta temporária para o snapshot
STATE_FILE_PATH = "db/damicore/state.txt"  # Arquivo simples para guardar o timestamp da última execução
CLUSTER_OUTPUT_FILE = "db/damicore/clusters.csv"  # Arquivo de saída padrão da DAMICORE (se não especificado)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("coredump_clustering")


def check_trigger() -> bool:
    """Avalia se devemos disparar a clusterização.

    Regras:
      1. Só roda se houver pelo menos 2 coredumps totais (senão cluster não faz sentido).
      2. Roda imediatamente se houver >= MIN_NEW_COREDUMPS_TRIGGER ainda não clusterizados.
      3. Caso contrário, roda se o tempo desde a última execução exceder MAX_TIME_SINCE_LAST_RUN_SECONDS
         e existe pelo menos 1 novo coredump.
    """
    logger.info("Verificando gatilho de clusterização...")

    all_coredumps = db_manager.list_all_coredumps()
    if len(all_coredumps) < 2:
        logger.debug("Menos de 2 coredumps registrados. Aguardando acumular mais.")
        return False

    unclustered = db_manager.get_unclustered_coredumps()
    unclustered_count = len(unclustered)
    if unclustered_count == 0:
        logger.debug("Nenhum coredump novo desde a última execução.")
        return False

    if unclustered_count >= MIN_NEW_COREDUMPS_TRIGGER:
        logger.info(
            "Gatilho por quantidade: %d novos coredumps (limite=%d)",
            unclustered_count,
            MIN_NEW_COREDUMPS_TRIGGER,
        )
        return True

    # Checagem por tempo decorrido desde última execução
    try:
        with open(STATE_FILE_PATH, "r") as f:
            last_run_timestamp = float(f.read().strip())
    except (FileNotFoundError, ValueError):
        last_run_timestamp = 0.0  # nunca executou

    elapsed = time.time() - last_run_timestamp
    if elapsed > MAX_TIME_SINCE_LAST_RUN_SECONDS:
        logger.info(
            "Gatilho por tempo: %.0fs desde última execução (limite=%ds)",
            elapsed,
            MAX_TIME_SINCE_LAST_RUN_SECONDS,
        )
        return True

    logger.debug(
        "Gatilho não ativado. %d novos coredumps; faltam mais dumps ou tempo.",
        unclustered_count,
    )
    return False


def prepare_snapshot_directory() -> int:
    """Gera um snapshot local contendo TODOS os coredumps atuais para a DAMICORE.

    Retorna o número de arquivos copiados. (O mapeamento ID->arquivo não é mais usado aqui.)
    """
    logger.info("Preparando snapshot em '%s'", TEMP_PROCESSING_DIR)
    if os.path.exists(TEMP_PROCESSING_DIR):
        shutil.rmtree(TEMP_PROCESSING_DIR)
    os.makedirs(TEMP_PROCESSING_DIR, exist_ok=True)

    all_coredumps = db_manager.list_all_coredumps()
    if not all_coredumps:
        logger.warning("Nenhum coredump disponível no banco de dados.")
        return 0

    copied = 0
    for dump in all_coredumps:
        # Layout esperado de colunas: (id, mac, fw_id, cluster_id, ..., raw_dump_path,...)
        try:
            source_path = dump[5]
        except IndexError:
            logger.warning("Formato inesperado de registro de coredump: %s", dump)
            continue
        if not os.path.exists(source_path):
            logger.warning(
                "Arquivo ausente para coredump em '%s' (ignorado)", source_path
            )
            continue
        filename = os.path.basename(source_path)
        dest = os.path.join(TEMP_PROCESSING_DIR, filename)
        shutil.copy(source_path, dest)
        copied += 1

    logger.info("Snapshot criado (%d arquivos).", copied)
    return copied


def run_damicore_clustering_docker(timeout_s: int = 600) -> bool:
    """Executa a DAMICORE (via Docker) gerando `CLUSTER_OUTPUT_FILE`.

    Retorna True em caso de execução bem-sucedida.
    """
    logger.info("Executando DAMICORE em contêiner Docker...")

    host_project_root = Path.cwd().resolve()
    container_workdir = "/app"
    container_data_dir = "/data"  # volume principal (project root)
    container_processing_dir = (
        f"{container_data_dir}/{TEMP_PROCESSING_DIR.replace('\\', '/')}"
    )
    container_output = f"{container_data_dir}/{CLUSTER_OUTPUT_FILE.replace('\\', '/')}"

    damicore_args = [
        "--compressor",
        "zlib",
        "--level",
        "9",
        "--output",
        container_output,
        container_processing_dir,
    ]

    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{host_project_root}:{container_data_dir}",
        "-w",
        container_workdir,
        DAMICORE_DOCKER_IMAGE,
        "python3",
        "main.py",
        *damicore_args,
    ]

    logger.debug("Comando Docker: %s", " ".join(command))

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
            timeout=timeout_s,
        )
        if result.stdout:
            logger.info("DAMICORE stdout:\n%s", result.stdout.strip())
        if result.stderr:
            logger.debug("DAMICORE stderr:\n%s", result.stderr.strip())
        return True
    except subprocess.CalledProcessError as e:
        logger.error(
            "Falha na execução da DAMICORE (exit=%s): %s", e.returncode, e.stderr
        )
    except FileNotFoundError:
        logger.error(
            "Comando 'docker' não encontrado. Docker está instalado e no PATH?"
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Erro inesperado executando Docker: %s", e)
    return False


def process_clustering_results(path: Optional[str] = None) -> None:
    """Lê o arquivo CSV de clusters e aplica no banco (reconciliação)."""
    csv_path = path or CLUSTER_OUTPUT_FILE
    logger.info("Processando resultados em '%s'", csv_path)
    if not os.path.exists(csv_path):
        logger.error("Arquivo de resultados não encontrado: %s", csv_path)
        return
    processar_reconciliacao(csv_path)
    with open(STATE_FILE_PATH, "w") as f:
        f.write(str(time.time()))
    logger.info("Clusterização concluída e estado atualizado.")


def cleanup(remove_cluster_file: bool = False) -> None:
    """Remove diretório temporário. (Opcionalmente remove o CSV de clusters gerado.)"""
    if os.path.exists(TEMP_PROCESSING_DIR):
        shutil.rmtree(TEMP_PROCESSING_DIR)
        logger.debug("Removido diretório temporário '%s'", TEMP_PROCESSING_DIR)
    if remove_cluster_file and os.path.exists(CLUSTER_OUTPUT_FILE):
        os.remove(CLUSTER_OUTPUT_FILE)
        logger.debug("Removido arquivo de cluster '%s'", CLUSTER_OUTPUT_FILE)


def main() -> None:
    """Fluxo principal de orquestração de uma rodada de clusterização.

    Chamado de forma periódica (loop inferior) ou manualmente. Seguro repetir.
    """
    logger.info(
        "Iniciando rodada de verificação em %s",
        datetime.now().isoformat(timespec="seconds"),
    )

    db_manager.create_database()  # Garantir estrutura

    if not check_trigger():
        return

    try:
        copied = prepare_snapshot_directory()
        if copied == 0:
            logger.info("Nenhum arquivo disponível para processar.")
            return

        if run_damicore_clustering_docker():
            process_clustering_results()
        else:
            logger.error("Execução da DAMICORE falhou; resultados não aplicados.")
    except Exception as e:  # noqa: BLE001
        logger.exception("Erro crítico durante a rodada: %s", e)
    finally:
        cleanup(remove_cluster_file=False)


if __name__ == "__main__":
    # Loop simples: verifica a cada 60s se condições de clusterização foram satisfeitas.
    # Pode ser substituído por um scheduler externo (cron, systemd timer, etc.).
    INTERVALO_SEGUNDOS = 60
    while True:
        main()
        logger.info("Aguardando %ds para próxima verificação...", INTERVALO_SEGUNDOS)
        time.sleep(INTERVALO_SEGUNDOS)
