"""coredump_clusterizer.py

Orquestra a clusterização de coredumps (snapshot + execução DAMICORE em Docker +
reconciliação no banco). 

Fluxo: 
1. verifica gatilho (quantidade/tempo).
2. gera snapshot.
3. roda DAMICORE.
4. aplica CSV de clusters.
5. atualiza estado.

Formato esperado de cada registro de coredump (tupla retornada pelo repositório):
 (id, mac, firmware_id, cluster_id, <campo4>, raw_dump_path, ...)
 O índice 5 deve conter o caminho bruto do arquivo de coredump.

Variáveis de ambiente relevantes: (nenhuma obrigatória por enquanto)

TODO: Permitir sobreposição da imagem Docker via variável de ambiente.
TODO: Tratar montagem do volume Docker quando o caminho do projeto contém espaços.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence, TypeAlias, Protocol

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("backend.coredump_clusterizer")

# ---------------------------------------------------------------------------
# Imports dependentes do projeto (atrasados para permitir logging estruturado)
# ---------------------------------------------------------------------------

try:  # Interface de acesso ao repositório de dados
    from .ports import IDataRepository
except ImportError as e:
    logging.error("Erro ao importar IDataRepository: %s", e)
    raise SystemExit(1)

try:  # Função que faz a reconciliação / atualização de clusters no banco
    from .cluster_sincronyzer import processar_reconciliacao
except ImportError as e:
    logging.error("Erro ao importar cluster_sincronyzer: %s", e)
    raise SystemExit(1)

# ---------------------------------------------------------------------------
# Constantes de Configuração
# ---------------------------------------------------------------------------

# Imagem Docker da DAMICORE (padrão; pode ser construída localmente).
DAMICORE_DOCKER_IMAGE: str = "damicore-python"

# Gatilho híbrido: quantidade mínima de novos coredumps não clusterizados.
MIN_NEW_COREDUMPS_TRIGGER: int = 5

# Tempo máximo (segundos) desde a última execução antes de forçar nova rodada.
MAX_TIME_SINCE_LAST_RUN_SECONDS: int = 60 * 5  # 5 minutos

# Intervalo do loop principal (quando executado como script).
MAIN_LOOP_INTERVAL_SECONDS: int = 60

# Diretórios / arquivos (Paths para clareza e portabilidade).
PROCESSING_DIR: Path = Path("db/damicore/processing_temp")
STATE_FILE_PATH: Path = Path("db/damicore/state.txt")
CLUSTER_OUTPUT_FILE: Path = Path("db/damicore/clusters.csv")

# Timeout padrão (segundos) para execução do contêiner DAMICORE.
DAMICORE_DOCKER_TIMEOUT_S: int = 600

# Type alias para registros de coredump retornados pelo banco.
CoredumpRecord: TypeAlias = Sequence[Any]


def check_trigger(repo: IDataRepository) -> bool:
    """Decide se a clusterização deve iniciar (quantidade ou tempo).
    
    Args:
        repo: Repositório de dados para acessar coredumps.
    
    Returns:
        True se o gatilho foi ativado, False caso contrário.
    """
    logger.info("Verificando gatilho de clusterização...")

    all_coredumps = repo.list_all_coredumps()
    if len(all_coredumps) < 2:
        logger.debug("Menos de 2 coredumps registrados. Aguardando acumular mais.")
        return False

    unclustered = repo.get_unclustered_coredumps()
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


def prepare_snapshot_directory(repo: IDataRepository) -> int:
    """Gera snapshot local de todos os coredumps (retorna quantidade).
    
    Args:
        repo: Repositório de dados para acessar coredumps.
    
    Returns:
        Número de arquivos copiados para o snapshot.
    """
    logger.info("Preparando snapshot em '%s'", PROCESSING_DIR)

    if PROCESSING_DIR.exists():
        shutil.rmtree(PROCESSING_DIR)
    PROCESSING_DIR.mkdir(parents=True, exist_ok=True)

    all_coredumps: Sequence[CoredumpRecord] = repo.list_all_coredumps()
    if not all_coredumps:
        logger.warning("Nenhum coredump disponível no banco de dados.")
        return 0

    copied = 0
    for record in all_coredumps:
        # Esperado índice 5 = caminho do arquivo bruto
        try:
            source_path_raw = record[5]
        except IndexError:
            logger.warning("Formato inesperado de registro de coredump: %s", record)
            continue

        source_path = Path(str(source_path_raw))
        if not source_path.exists():
            logger.warning(
                "Arquivo ausente para coredump em '%s' (ignorado)", source_path
            )
            continue

        dest = PROCESSING_DIR / source_path.name
        shutil.copy(source_path, dest)
        copied += 1

    logger.info("Snapshot criado (%d arquivos).", copied)
    return copied


def run_damicore_clustering_docker(timeout_s: int = DAMICORE_DOCKER_TIMEOUT_S) -> bool:
    """Executa DAMICORE via Docker retornando sucesso ou falha."""
    logger.info("Executando DAMICORE em contêiner Docker...")

    host_project_root = Path.cwd().resolve()
    container_workdir = "/app"
    container_data_dir = "/data"  # volume montado
    container_processing_dir = f"{container_data_dir}/{PROCESSING_DIR.as_posix()}"
    container_output = f"{container_data_dir}/{CLUSTER_OUTPUT_FILE.as_posix()}"

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


def _write_state_timestamp(ts: float) -> None:
    """Atualiza arquivo de estado de forma atômica."""
    STATE_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_FILE_PATH.with_suffix(STATE_FILE_PATH.suffix + ".tmp")
    tmp_path.write_text(str(ts))
    # Path.replace usa os.replace internamente (atômico na mesma FS)
    tmp_path.replace(STATE_FILE_PATH)


def process_clustering_results(path: Optional[str | Path] = None) -> None:
    """Aplica CSV de clusters no banco e atualiza estado."""
    csv_path = Path(path) if path else CLUSTER_OUTPUT_FILE
    logger.info("Processando resultados em '%s'", csv_path)
    if not csv_path.exists():
        logger.error("Arquivo de resultados não encontrado: %s", csv_path)
        return
    processar_reconciliacao(str(csv_path))
    _write_state_timestamp(time.time())
    logger.info("Clusterização concluída e estado atualizado.")


def cleanup(remove_cluster_file: bool = False) -> None:
    """Remove diretório temporário e opcionalmente o CSV gerado."""
    if PROCESSING_DIR.exists():
        shutil.rmtree(PROCESSING_DIR)
        logger.debug("Removido diretório temporário '%s'", PROCESSING_DIR)
    if remove_cluster_file and CLUSTER_OUTPUT_FILE.exists():
        CLUSTER_OUTPUT_FILE.unlink()
        logger.debug("Removido arquivo de cluster '%s'", CLUSTER_OUTPUT_FILE)


def main(repo: IDataRepository) -> None:
    """Executa uma rodada de clusterização completa se gatilho ativo.
    
    Args:
        repo: Repositório de dados para acessar e atualizar coredumps.
    """
    logger.info(
        "Iniciando rodada de verificação em %s",
        datetime.now().isoformat(timespec="seconds"),
    )

    repo.create_database()  # Garantir estrutura

    if not check_trigger(repo):
        return

    try:
        copied = prepare_snapshot_directory(repo)
        if copied == 0:
            logger.info("Nenhum arquivo disponível para processar.")
            return
        
        if copied < 2:
            logger.warning(
                "DAMICORE requer pelo menos 2 coredumps para clusterização. Encontrados: %d. "
                "Aguardando mais coredumps...", copied
            )
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
    # Loop contínuo: verifica periodicamente se a clusterização deve rodar.
    # Pode ser substituído por scheduler externo (cron/systemd/airflow/etc.).
    from .components.data_repository import create_repository
    
    repository = create_repository()
    logger.info("Repositório criado. Iniciando loop de clusterização...")
    
    while True:  # loop de longa duração
        main(repository)
        logger.info(
            "Aguardando %ds para próxima verificação...", MAIN_LOOP_INTERVAL_SECONDS
        )
        time.sleep(MAIN_LOOP_INTERVAL_SECONDS)
