"""db_manager.py

Camada mínima de acesso a dados usando SQLite para armazenar firmwares,
devices, clusters e coredumps do backend de extração/classificação.

Fluxo resumido:
    1. `create_database()` garante a existência das tabelas (idempotente).
    2. Funções CRUD chamam `_execute_query()` que centraliza conexão, PRAGMAs
       e logging (row_factory habilita acesso por nome e índice).
    3. Inserções retornam `lastrowid`; buscas retornam `sqlite3.Row` (ou lista deles) permitindo
       acesso por nome (row["firmware_id"]) ou índice (row[0]).
    4. Demonstração isolada em `_demo()` executada apenas via `python db_manager.py`.

Variáveis de ambiente relevantes:
    (Atual) Nenhuma obrigatória. Banco padrão em ./db/project.db.
    (Potencial) FUTURO: CORE_DUMP_DB_DIR / CORE_DUMP_DB_NAME para customizar
    localização/arquivo sem editar código (ver TODO abaixo).

Formato dos registros (rows):
    firmwares: (firmware_id, name, version, elf_path)
    devices: (mac_address, current_firmware_id, chip_type)
    clusters: (cluster_id, name)
    coredumps: (coredump_id, device_mac_address, firmware_id_on_crash,
                cluster_id, raw_dump_path, log_path, received_at)

TODO: - Validar/sanitizar `elf_path` (evitar path traversal / apontar para fora da base).
TODO: - Converter Rows para dataclasses / objetos de domínio.
TODO: - Adicionar índices explícitos (ex.: devices.current_firmware_id, coredumps.cluster_id, received_at) se volume crescer.
TODO: - Parametrizar diretório/arquivo via variáveis de ambiente.
TODO: - Reavaliar estratégia de timeout para cenários de alto write contention.

Notas rápidas:
    - PRAGMAs aplicados: WAL (melhor leitura concorrente) e foreign_keys=ON.
    - `received_at` armazenado como epoch (int) para facilitar ordenação.
    - `SQLITE_TIMEOUT_SEC` (padrão=10) define quanto tempo a conexão espera por locks antes de falhar (ajuste em caso de alta contenção de escrita).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Any, Literal, Optional, Sequence, Union, TypeAlias
import logging

# -----------------------------------------------------------------------------
# Configurações / Constantes
# -----------------------------------------------------------------------------
# Diretório e arquivo do banco. Ajuste aqui se a estrutura mudar.
DB_DIRECTORY: Path = Path("db")
DB_NAME: str = "project.db"
DB_PATH: Path = DB_DIRECTORY / DB_NAME

# Timeout padrão para conexões SQLite (evita travar em locks prolongados)
SQLITE_TIMEOUT_SEC: int = 10

# Nome do logger para este módulo
logger = logging.getLogger("backend.db_manager")

# -----------------------------------------------------------------------------
# Type aliases (tuplas retornadas pelo sqlite3)
# -----------------------------------------------------------------------------
FirmwareRow: TypeAlias = tuple[int, str, str, str]            # (firmware_id, name, version, elf_path)
DeviceRow: TypeAlias = tuple[str, int, Optional[str]]         # (mac_address, current_firmware_id, chip_type)
ClusterRow: TypeAlias = tuple[int, str]                       # (cluster_id, name)
CoredumpRow: TypeAlias = tuple[int, str, int, Optional[int], str, Optional[str], Optional[int]]
# (coredump_id, device_mac_address, firmware_id_on_crash, cluster_id, raw_dump_path, log_path, received_at)

# Resultado genérico de _execute_query dependendo do modo
FetchMode = Optional[Literal["one", "all"]]
QueryResult: TypeAlias = Union[None, int, FirmwareRow, DeviceRow, ClusterRow, CoredumpRow, list[tuple[Any, ...]]]

# -----------------------------------------------------------------------------
# Funções utilitárias internas
# -----------------------------------------------------------------------------
def _get_connection() -> sqlite3.Connection:
    """Cria conexão configurada (WAL, FK)."""
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_TIMEOUT_SEC)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

# --- Função de Criação do Banco ---
def create_database() -> None:
    """Garante a criação das tabelas necessárias caso não existam."""
    DB_DIRECTORY.mkdir(parents=True, exist_ok=True)
    try:
        with _get_connection() as conn:
            # Criação idempotente das tabelas
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS firmwares (
                    firmware_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    elf_path TEXT NOT NULL,
                    UNIQUE(name, version)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    mac_address TEXT PRIMARY KEY,
                    current_firmware_id INTEGER NOT NULL,
                    chip_type TEXT,
                    FOREIGN KEY(current_firmware_id) REFERENCES firmwares(firmware_id)
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS clusters (
                    cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE
                );
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS coredumps (
                    coredump_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_mac_address TEXT NOT NULL,
                    firmware_id_on_crash INTEGER NOT NULL,
                    cluster_id INTEGER,
                    raw_dump_path TEXT NOT NULL,
                    log_path TEXT,
                    received_at INTEGER,
                    FOREIGN KEY(device_mac_address) REFERENCES devices(mac_address),
                    FOREIGN KEY(firmware_id_on_crash) REFERENCES firmwares(firmware_id),
                    FOREIGN KEY(cluster_id) REFERENCES clusters(cluster_id)
                );
                """
            )
            logger.info("Banco de dados e tabelas verificados/criados (path=%s)", DB_PATH)
    except sqlite3.Error:  # pragma: no cover - loga contexto
        logger.exception("Falha ao criar/verificar estrutura do banco")

# -----------------------------------------------------------------------------
# Execução genérica de queries
# -----------------------------------------------------------------------------
def _execute_query(
    query: str,
    params: Sequence[Any] | None = None,
    fetch: FetchMode = None,
) -> QueryResult:
    """Executa query parametrizada.

    fetch="one" -> retorna uma tupla ou None.
    fetch="all" -> retorna lista de tuplas.
    fetch=None -> retorna lastrowid (int) ou None em erro.

    OBS: Com row_factory=sqlite3.Row, os registros retornados suportam acesso
    tanto por índice quanto por nome (ex.: row[0] ou row["firmware_id"]).
    """
    params = params or ()
    try:
        with _get_connection() as conn:
            cursor = conn.cursor()
            logger.debug("Executando SQL: %s | params=%s", query.strip().splitlines()[0], params)
            cursor.execute(query, params)  # segura contra SQL injection por uso de placeholders
            if fetch == "one":
                return cursor.fetchone()  # type: ignore[return-value]
            if fetch == "all":
                return cursor.fetchall()  # type: ignore[return-value]
            return cursor.lastrowid  # Para INSERT principalmente
    except sqlite3.IntegrityError:
        logger.exception("Violação de integridade ao executar query")
        return None
    except sqlite3.Error:
        logger.exception("Erro de banco ao executar query")
        return None
## ---------------------------------------------------------------------------
# CRUD: Firmwares
## ---------------------------------------------------------------------------
def add_firmware(name: str, version: str, elf_path: str) -> Optional[int]:
    """Cria firmware e retorna seu ID ou None em erro."""
    # TODO: validar se elf_path aponta para local permitido / sanitização
    return _execute_query(
        "INSERT INTO firmwares (name, version, elf_path) VALUES (?, ?, ?)",
        (name, version, elf_path),
    )  # type: ignore[return-value]


def get_firmware_by_name_version(name: str, version: str) -> Optional[FirmwareRow]:
    """Obtém firmware específico por (name, version)."""
    return _execute_query(
        "SELECT * FROM firmwares WHERE name = ? AND version = ?",
        (name, version),
        fetch="one",
    )  # type: ignore[return-value]


def get_firmware_by_id(firmware_id: int) -> Optional[FirmwareRow]:
    """Busca firmware por ID."""
    return _execute_query(
        "SELECT * FROM firmwares WHERE firmware_id = ?", (firmware_id,), fetch="one"
    )  # type: ignore[return-value]


def list_firmwares() -> list[tuple[Any, ...]]:
    """Lista todos os firmwares."""
    return _execute_query("SELECT * FROM firmwares", fetch="all") or []  # type: ignore[return-value]


def update_firmware_path(firmware_id: int, new_elf_path: str) -> bool:
    """(NOVO) Atualiza o elf_path de um firmware."""
    _execute_query(
        "UPDATE firmwares SET elf_path = ? WHERE firmware_id = ?",
        (new_elf_path, firmware_id)
    )
    return True


def delete_firmware(firmware_id: int) -> bool:
    """Remove firmware (falha se existir FK dependente)."""
    logger.info("Deletando firmware id=%s", firmware_id)
    _execute_query("DELETE FROM firmwares WHERE firmware_id = ?", (firmware_id,))
    return True  # Mantido para compatibilidade (anterior retornava sempre True)


## ---------------------------------------------------------------------------
# CRUD: Devices
## ---------------------------------------------------------------------------
def add_or_update_device(
    mac_address: str, current_firmware_id: int, chip_type: Optional[str] = None
) -> bool:
    """Cria ou atualiza device (UPSERT por mac)."""
    _execute_query(
        """
        INSERT INTO devices (mac_address, current_firmware_id, chip_type) VALUES (?, ?, ?)
        ON CONFLICT(mac_address) DO UPDATE SET
            current_firmware_id = excluded.current_firmware_id,
            chip_type = excluded.chip_type;
        """,
        (mac_address, current_firmware_id, chip_type),
    )
    return True


def get_device(mac_address: str) -> Optional[DeviceRow]:
    """Obtém device por MAC."""
    return _execute_query(
        "SELECT * FROM devices WHERE mac_address = ?", (mac_address,), fetch="one"
    )  # type: ignore[return-value]


def list_devices() -> list[tuple[Any, ...]]:
    """Lista todos os devices."""
    return _execute_query("SELECT * FROM devices", fetch="all") or []  # type: ignore[return-value]


def delete_device(mac_address: str) -> bool:
    """Remove device (falha se coredump referenciar)."""
    logger.info("Deletando device mac=%s", mac_address)
    _execute_query("DELETE FROM devices WHERE mac_address = ?", (mac_address,))
    return True


## ---------------------------------------------------------------------------
# CRUD: Clusters
## ---------------------------------------------------------------------------
def add_cluster(name: str) -> Optional[int]:
    """Cria cluster e retorna ID."""
    return _execute_query("INSERT INTO clusters (name) VALUES (?)", (name,))  # type: ignore[return-value]


def get_cluster_by_name(name: str) -> Optional[ClusterRow]:
    """Obtém cluster pelo nome."""
    return _execute_query(
        "SELECT * FROM clusters WHERE name = ?", (name,), fetch="one"
    )  # type: ignore[return-value]


def list_clusters() -> list[tuple[Any, ...]]:
    """Lista clusters."""
    return _execute_query("SELECT * FROM clusters", fetch="all") or []  # type: ignore[return-value]


def rename_cluster(cluster_id: int, new_name: str) -> bool:
    """Renomeia cluster mantendo ID."""
    logger.info("Renomeando cluster id=%s para '%s'", cluster_id, new_name)
    _execute_query("UPDATE clusters SET name = ? WHERE cluster_id = ?", (new_name, cluster_id))
    return True


def delete_cluster(cluster_id: int) -> bool:
    """Remove cluster (falha se em uso)."""
    logger.info("Deletando cluster id=%s", cluster_id)
    _execute_query("DELETE FROM clusters WHERE cluster_id = ?", (cluster_id,))
    return True


def get_cluster_name(cluster_id: int) -> Optional[str]:
    """Retorna nome de cluster ou None."""
    result = _execute_query(
        "SELECT name FROM clusters WHERE cluster_id = ?", (cluster_id,), fetch="one"
    )
    return result[0] if result else None  # type: ignore[index]


## ---------------------------------------------------------------------------
# CRUD: Coredumps
## ---------------------------------------------------------------------------
def add_coredump(
    device_mac: str,
    firmware_id: int,
    raw_dump_path: str,
    log_path: Optional[str] = None,
    received_at: Optional[int] = None,
) -> Optional[int]:
    """Adiciona coredump e retorna seu ID.

    raw_dump_path: caminho bruto do arquivo recebido.
    """
    if received_at is None:
        received_at = int(datetime.now().timestamp())
    return _execute_query(
        """
        INSERT INTO coredumps (device_mac_address, firmware_id_on_crash, raw_dump_path, log_path, received_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (device_mac, firmware_id, raw_dump_path, log_path, received_at),
    )  # type: ignore[return-value]


def get_clustered_coredumps() -> list[tuple[Any, ...]]:
    """Coredumps já associados a um cluster (coredump_id, cluster_id)."""
    return _execute_query(
        "SELECT coredump_id, cluster_id FROM coredumps WHERE cluster_id IS NOT NULL",
        fetch="all",
    ) or []  # type: ignore[return-value]


def get_unclustered_coredumps() -> list[tuple[Any, ...]]:
    """Coredumps sem cluster associado."""
    return _execute_query(
        "SELECT * FROM coredumps WHERE cluster_id IS NULL", fetch="all"
    ) or []  # type: ignore[return-value]


def get_coredump_info_by_id(coredump_id: int) -> Optional[tuple[str, Optional[str]]]:
    """Retorna (raw_dump_path, log_path) para o coredump ID."""
    return _execute_query(
        "SELECT raw_dump_path, log_path FROM coredumps WHERE coredump_id = ?",
        (coredump_id,),
        fetch="one",
    )  # type: ignore[return-value]


def assign_cluster_to_coredump(coredump_id: int, cluster_id: Optional[int]) -> bool:
    """(MODIFICADO) Associa cluster (ou desassocia se cluster_id=None)."""
    _execute_query(
        "UPDATE coredumps SET cluster_id = ? WHERE coredump_id = ?",
        (cluster_id, coredump_id),
    )
    return True


def update_coredump(coredump_id: int, cluster_id: Optional[int], log_path: Optional[str]) -> bool:
    """(NOVO) Atualiza o cluster_id e o log_path de um coredump."""
    _execute_query(
        "UPDATE coredumps SET cluster_id = ?, log_path = ? WHERE coredump_id = ?",
        (cluster_id, log_path, coredump_id),
    )
    return True


def unassign_cluster_from_coredumps(cluster_id: int) -> None:
    """Remove associação de todos os coredumps do cluster fornecido."""
    logger.info("Desassociando coredumps do cluster id=%s", cluster_id)
    _execute_query(
        "UPDATE coredumps SET cluster_id = NULL WHERE cluster_id = ?", (cluster_id,)
    )


def list_all_coredumps() -> list[tuple[Any, ...]]:
    """Lista todos os coredumps."""
    return _execute_query("SELECT * FROM coredumps", fetch="all") or []  # type: ignore[return-value]


def delete_coredump(coredump_id: int) -> bool:
    """Remove coredump."""
    logger.info("Deletando coredump id=%s", coredump_id)
    _execute_query("DELETE FROM coredumps WHERE coredump_id = ?", (coredump_id,))
    return True
## ---------------------------------------------------------------------------
# Bloco de demonstração (executado somente se rodar diretamente)
## ---------------------------------------------------------------------------
def _demo() -> None:
    """Demonstra operações CRUD básicas para desenvolvimento/manual QA."""
    create_database()

    logger.info("Limpando dados anteriores (ordem importa por FK)...")
    for table in ("coredumps", "clusters", "devices", "firmwares"):
        _execute_query(f"DELETE FROM {table}")

    fw_id_1 = add_firmware("SensorApp", "1.0.0", "storage/elfs/SensorApp/1.0.0/firmware.elf")
    fw_id_2 = add_firmware("DisplayApp", "2.1.0", "storage/elfs/DisplayApp/2.1.0/firmware.elf")
    logger.info("Firmwares criados: %s / %s", fw_id_1, fw_id_2)
    
    # Teste de update
    update_firmware_path(fw_id_1 or 0, "storage/elfs/SensorApp/1.0.1/firmware.elf")
    logger.info("Firmware 1 atualizado")

    mac_1 = "AA:BB:CC:11:22:33"
    mac_2 = "DD:EE:FF:44:55:66"
    add_or_update_device(mac_1, fw_id_1 or 0)
    add_or_update_device(mac_2, fw_id_2 or 0)

    cluster_id_1 = add_cluster("Stack_Overflow_MQTT_Task")
    cluster_id_2 = add_cluster("I2C_Bus_Failure")
    logger.info("Clusters criados: %s / %s", cluster_id_1, cluster_id_2)

    cd_id_1 = add_coredump(mac_1, fw_id_1 or 0, f"storage/coredumps/{mac_1}/1759089593.cdmp")
    cd_id_2 = add_coredump(mac_1, fw_id_1 or 0, f"storage/coredumps/{mac_1}/1759089688.cdmp")
    cd_id_3 = add_coredump(mac_2, fw_id_2 or 0, f"storage/coredumps/{mac_2}/1759089901.cdmp")
    logger.info("Coredumps criados: %s %s %s", cd_id_1, cd_id_2, cd_id_3)

    assign_cluster_to_coredump(cd_id_1 or 0, cluster_id_1 or 0)
    assign_cluster_to_coredump(cd_id_2 or 0, cluster_id_1 or 0)
    assign_cluster_to_coredump(cd_id_3 or 0, cluster_id_2 or 0)
    rename_cluster(cluster_id_1 or 0, "Stack_Overflow_em_MQTT_Task")
    
    # Teste de desassociar
    assign_cluster_to_coredump(cd_id_2 or 0, None)
    logger.info("Coredump 2 desassociado")

    logger.info("Estado final firmwares=%s", list_firmwares())
    logger.info("Estado final devices=%s", list_devices())
    logger.info("Estado final clusters=%s", list_clusters())
    logger.info("Estado final coredumps=%s", list_all_coredumps())

    # Demonstra deleção de dependências
    delete_coredump(cd_id_1 or 0)
    delete_coredump(cd_id_3 or 0)
    delete_device(mac_2)
    delete_firmware(fw_id_2 or 0)
    logger.info("Após deleções: firmwares=%s", list_firmwares())


if __name__ == "__main__":  # Isola efeito colateral apenas em execução direta
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s :: %(message)s")
    _demo()