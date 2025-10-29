"""Gerenciamento de Firmwares e Dispositivos.

Fornece interface simplificada para gerenciamento de firmwares, dispositivos e clusters.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from ..ports import IFirmwareManagement, IDataRepository


class FirmwareManagement(IFirmwareManagement):
    """Implementação da interface de gerenciamento usando repositório de dados."""
    
    def __init__(self, repo: IDataRepository) -> None:
        self.repo = repo
    
    def list_firmwares(self) -> Sequence[Sequence[Any]]:
        """Lista todos os firmwares cadastrados."""
        return self.repo.list_firmwares()
    
    def list_devices(self) -> Sequence[Sequence[Any]]:
        """Lista todos os dispositivos cadastrados."""
        return self.repo.list_devices()
    
    def list_clusters(self) -> Sequence[Sequence[Any]]:
        """Lista todos os clusters."""
        return self.repo.list_clusters()
    
    def list_all_coredumps(self) -> Sequence[Sequence[Any]]:
        """Lista todos os coredumps."""
        return self.repo.list_all_coredumps()
    
    def add_firmware(self, name: str, version: str, elf_path: str) -> Optional[int]:
        """Adiciona um novo firmware."""
        return self.repo.add_firmware(name, version, elf_path)
    
    def update_firmware_path(self, firmware_id: int, new_elf_path: str) -> bool:
        """Atualiza o caminho do ELF de um firmware."""
        return self.repo.update_firmware_path(firmware_id, new_elf_path)
    
    def delete_firmware(self, firmware_id: int) -> bool:
        """Remove um firmware."""
        return self.repo.delete_firmware(firmware_id)
    
    def add_or_update_device(
        self, 
        mac_address: str, 
        current_firmware_id: int, 
        chip_type: Optional[str] = None
    ) -> bool:
        """Adiciona ou atualiza um dispositivo."""
        return self.repo.add_or_update_device(mac_address, current_firmware_id, chip_type)
    
    def delete_device(self, mac_address: str) -> bool:
        """Remove um dispositivo."""
        return self.repo.delete_device(mac_address)
    
    def add_cluster(self, name: str) -> Optional[int]:
        """Adiciona um novo cluster."""
        return self.repo.add_cluster(name)
    
    def rename_cluster(self, cluster_id: int, new_name: str) -> bool:
        """Renomeia um cluster."""
        return self.repo.rename_cluster(cluster_id, new_name)
    
    def delete_cluster(self, cluster_id: int) -> bool:
        """Remove um cluster."""
        return self.repo.delete_cluster(cluster_id)
    
    def unassign_cluster_from_coredumps(self, cluster_id: int) -> None:
        """Remove a associação de coredumps com um cluster."""
        self.repo.unassign_cluster_from_coredumps(cluster_id)
    
    def add_coredump(self, device_mac: str, firmware_id: int, raw_dump_path: str) -> Optional[int]:
        """Adiciona um novo coredump."""
        return self.repo.add_coredump(device_mac, firmware_id, raw_dump_path)
    
    def update_coredump(self, coredump_id: int, cluster_id: Optional[int], log_path: Optional[str]) -> bool:
        """Atualiza um coredump."""
        return self.repo.update_coredump(coredump_id, cluster_id, log_path)
    
    def delete_coredump(self, coredump_id: int) -> bool:
        """Remove um coredump."""
        return self.repo.delete_coredump(coredump_id)
    
    def get_data_repository(self) -> IDataRepository:
        """Retorna acesso ao repositório de dados para operações avançadas."""
        return self.repo


def create_firmware_management(repo: IDataRepository) -> FirmwareManagement:
    """Factory function para criar interface de gerenciamento."""
    return FirmwareManagement(repo)


__all__ = ["FirmwareManagement", "create_firmware_management"]

