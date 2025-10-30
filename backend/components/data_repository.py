from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from .. import db_manager
from ..ports import IDataRepository


class SqliteDataRepository(IDataRepository):
    def get_device(self, mac: str) -> Optional[tuple]:
        return db_manager.get_device(mac)

    def get_firmware_by_id(self, firmware_id: int) -> Optional[tuple]:
        return db_manager.get_firmware_by_id(firmware_id)

    def save_coredump_raw(self, mac: str, firmware_id: int, raw_path: Path, received_at: int) -> int:
        new_id = db_manager.add_coredump(
            device_mac=mac,
            firmware_id=firmware_id,
            raw_dump_path=str(raw_path),
            log_path=None,
            received_at=received_at,
        )
        return int(new_id or 0)

    def save_coredump_report(self, coredump_id: int, report_path: Path) -> None:
        db_manager.update_coredump(coredump_id=coredump_id, cluster_id=None, log_path=str(report_path))

    def list_all_coredumps(self) -> Sequence[Sequence[Any]]:
        return db_manager.list_all_coredumps()

    def get_unclustered_coredumps(self) -> Sequence[Sequence[Any]]:
        return db_manager.get_unclustered_coredumps()

    def get_coredump_info(self, coredump_id: int) -> Optional[tuple[str, Optional[str]]]:
        """Retorna (raw_dump_path, log_path) para o coredump ID especificado."""
        return db_manager.get_coredump_info_by_id(coredump_id)

    def get_clustered_coredumps(self) -> Sequence[Sequence[Any]]:
        """Retorna coredumps já associados a clusters (coredump_id, cluster_id)."""
        return db_manager.get_clustered_coredumps()

    def get_cluster_name(self, cluster_id: int) -> Optional[str]:
        """Retorna o nome de um cluster pelo ID."""
        return db_manager.get_cluster_name(cluster_id)

    def assign_cluster_to_coredump(self, coredump_id: int, cluster_id: Optional[int]) -> bool:
        """Associa um coredump a um cluster (ou desassocia se cluster_id=None)."""
        return db_manager.assign_cluster_to_coredump(coredump_id, cluster_id)

    # ---- CRUD usados pela GUI ----
    def create_database(self) -> None:
        db_manager.create_database()

    def list_firmwares(self) -> Sequence[Sequence[Any]]:
        return db_manager.list_firmwares()

    def list_devices(self) -> Sequence[Sequence[Any]]:
        return db_manager.list_devices()

    def list_clusters(self) -> Sequence[Sequence[Any]]:
        return db_manager.list_clusters()

    def add_firmware(self, name: str, version: str, elf_path: str) -> Optional[int]:
        return db_manager.add_firmware(name, version, elf_path)

    def update_firmware_path(self, firmware_id: int, new_elf_path: str) -> bool:
        return db_manager.update_firmware_path(firmware_id, new_elf_path)

    def delete_firmware(self, firmware_id: int) -> bool:
        return db_manager.delete_firmware(firmware_id)

    def add_or_update_device(self, mac_address: str, current_firmware_id: int, chip_type: Optional[str] = None) -> bool:
        return db_manager.add_or_update_device(mac_address, current_firmware_id, chip_type)

    def delete_device(self, mac_address: str) -> bool:
        return db_manager.delete_device(mac_address)

    def add_cluster(self, name: str) -> Optional[int]:
        return db_manager.add_cluster(name)

    def rename_cluster(self, cluster_id: int, new_name: str) -> bool:
        return db_manager.rename_cluster(cluster_id, new_name)

    def delete_cluster(self, cluster_id: int) -> bool:
        return db_manager.delete_cluster(cluster_id)

    def unassign_cluster_from_coredumps(self, cluster_id: int) -> None:
        db_manager.unassign_cluster_from_coredumps(cluster_id)

    def add_coredump(self, device_mac: str, firmware_id: int, raw_dump_path: str) -> Optional[int]:
        return db_manager.add_coredump(device_mac, firmware_id, raw_dump_path)

    def update_coredump(self, coredump_id: int, cluster_id: Optional[int], log_path: Optional[str]) -> bool:
        return db_manager.update_coredump(coredump_id, cluster_id, log_path)

    def delete_coredump(self, coredump_id: int) -> bool:
        return db_manager.delete_coredump(coredump_id)

    def get_db_path(self) -> Path:
        return db_manager.DB_PATH


def create_repository() -> SqliteDataRepository:
    db_manager.create_database()
    return SqliteDataRepository()

__all__ = ["SqliteDataRepository", "create_repository"]


