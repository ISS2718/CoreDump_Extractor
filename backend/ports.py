from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Protocol, Sequence


class IDataRepository(Protocol):
    def get_device(self, mac: str) -> Optional[tuple]:
        ...

    def get_firmware_by_id(self, firmware_id: int) -> Optional[tuple]:
        ...

    def save_coredump_raw(self, mac: str, firmware_id: int, raw_path: Path, received_at: int) -> int:
        ...

    def save_coredump_report(self, coredump_id: int, report_path: Path) -> None:
        ...

    def list_all_coredumps(self) -> Sequence[Sequence[Any]]:
        ...

    def get_unclustered_coredumps(self) -> Sequence[Sequence[Any]]:
        ...

    def apply_cluster_csv(self, csv_path: Path) -> None:
        ...

    # CRUD usados pela GUI
    def create_database(self) -> None:
        ...

    def list_firmwares(self) -> Sequence[Sequence[Any]]:
        ...

    def list_devices(self) -> Sequence[Sequence[Any]]:
        ...

    def list_clusters(self) -> Sequence[Sequence[Any]]:
        ...

    def add_firmware(self, name: str, version: str, elf_path: str) -> Optional[int]:
        ...

    def update_firmware_path(self, firmware_id: int, new_elf_path: str) -> bool:
        ...

    def delete_firmware(self, firmware_id: int) -> bool:
        ...

    def add_or_update_device(self, mac_address: str, current_firmware_id: int, chip_type: Optional[str] = None) -> bool:
        ...

    def delete_device(self, mac_address: str) -> bool:
        ...

    def add_cluster(self, name: str) -> Optional[int]:
        ...

    def rename_cluster(self, cluster_id: int, new_name: str) -> bool:
        ...

    def delete_cluster(self, cluster_id: int) -> bool:
        ...

    def unassign_cluster_from_coredumps(self, cluster_id: int) -> None:
        ...

    def add_coredump(self, device_mac: str, firmware_id: int, raw_dump_path: str) -> Optional[int]:
        ...

    def update_coredump(self, coredump_id: int, cluster_id: Optional[int], log_path: Optional[str]) -> bool:
        ...

    def delete_coredump(self, coredump_id: int) -> bool:
        ...

    def get_db_path(self) -> Path:
        ...


class ICoreDumpParser(Protocol):
    def generate_report(
        self,
        raw_path: Path,
        elf_path: Path,
        out_dir: Path,
        chip_type: Optional[str],
    ) -> Path:
        ...


class ICoreDumpIngestor(Protocol):
    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...


class IClusterizerControl(Protocol):
    def run_once(self) -> None:
        """Executa uma verificação única do clusterizador."""
        ...
    
    def start(self) -> None:
        """Inicia o loop do clusterizador."""
        ...
    
    def stop(self) -> None:
        """Para o loop do clusterizador."""
        ...


class IAnalysisDashboard(Protocol):
    """Interface para Dashboard de Análise de Coredumps."""
    
    def get_coredumps_summary(self) -> dict[str, Any]:
        """Retorna resumo de coredumps (total, por cluster, etc)."""
        ...
    
    def get_cluster_details(self, cluster_id: int) -> Optional[dict[str, Any]]:
        """Retorna detalhes de um cluster específico."""
        ...
    
    def get_coredump_details(self, coredump_id: int) -> Optional[dict[str, Any]]:
        """Retorna detalhes de um coredump específico."""
        ...


class IFirmwareManagement(Protocol):
    """Interface para Gerenciamento de Firmwares e Dispositivos."""
    
    def list_firmwares(self) -> Sequence[Sequence[Any]]:
        """Lista todos os firmwares cadastrados."""
        ...
    
    def list_devices(self) -> Sequence[Sequence[Any]]:
        """Lista todos os dispositivos cadastrados."""
        ...
    
    def list_clusters(self) -> Sequence[Sequence[Any]]:
        """Lista todos os clusters."""
        ...
    
    def list_all_coredumps(self) -> Sequence[Sequence[Any]]:
        """Lista todos os coredumps."""
        ...
    
    def add_firmware(self, name: str, version: str, elf_path: str) -> Optional[int]:
        """Adiciona um novo firmware."""
        ...
    
    def update_firmware_path(self, firmware_id: int, new_elf_path: str) -> bool:
        """Atualiza o caminho do ELF de um firmware."""
        ...
    
    def delete_firmware(self, firmware_id: int) -> bool:
        """Remove um firmware."""
        ...
    
    def add_or_update_device(self, mac_address: str, current_firmware_id: int, chip_type: Optional[str] = None) -> bool:
        """Adiciona ou atualiza um dispositivo."""
        ...
    
    def delete_device(self, mac_address: str) -> bool:
        """Remove um dispositivo."""
        ...
    
    def add_cluster(self, name: str) -> Optional[int]:
        """Adiciona um novo cluster."""
        ...
    
    def rename_cluster(self, cluster_id: int, new_name: str) -> bool:
        """Renomeia um cluster."""
        ...
    
    def delete_cluster(self, cluster_id: int) -> bool:
        """Remove um cluster."""
        ...
    
    def unassign_cluster_from_coredumps(self, cluster_id: int) -> None:
        """Remove a associação de coredumps com um cluster."""
        ...
    
    def add_coredump(self, device_mac: str, firmware_id: int, raw_dump_path: str) -> Optional[int]:
        """Adiciona um novo coredump."""
        ...
    
    def update_coredump(self, coredump_id: int, cluster_id: Optional[int], log_path: Optional[str]) -> bool:
        """Atualiza um coredump."""
        ...
    
    def delete_coredump(self, coredump_id: int) -> bool:
        """Remove um coredump."""
        ...
    
    def get_data_repository(self) -> IDataRepository:
        """Retorna acesso ao repositório de dados para operações avançadas."""
        ...


