"""Dashboard de Análise de Coredumps.

Fornece interface para visualização e análise de coredumps e clusters.
"""
from __future__ import annotations

from typing import Any, Optional

from ..ports import IAnalysisDashboard, IDataRepository


class AnalysisDashboard(IAnalysisDashboard):
    """Implementação do dashboard de análise usando repositório de dados."""
    
    def __init__(self, repo: IDataRepository) -> None:
        self.repo = repo
    
    def get_coredumps_summary(self) -> dict[str, Any]:
        """Retorna resumo estatístico dos coredumps."""
        all_coredumps = self.repo.list_all_coredumps()
        unclustered = self.repo.get_unclustered_coredumps()
        clusters = self.repo.list_clusters()
        
        # Contagem por cluster
        cluster_counts: dict[int, int] = {}
        for cd in all_coredumps:
            cluster_id = cd[3] if len(cd) > 3 else None
            if cluster_id is not None:
                cluster_counts[cluster_id] = cluster_counts.get(cluster_id, 0) + 1
        
        return {
            "total_coredumps": len(all_coredumps),
            "unclustered_count": len(unclustered),
            "clustered_count": len(all_coredumps) - len(unclustered),
            "total_clusters": len(clusters),
            "coredumps_per_cluster": cluster_counts,
        }
    
    def get_cluster_details(self, cluster_id: int) -> Optional[dict[str, Any]]:
        """Retorna detalhes de um cluster específico."""
        clusters = self.repo.list_clusters()
        cluster_info = None
        
        for c in clusters:
            if len(c) > 0 and c[0] == cluster_id:
                cluster_info = c
                break
        
        if not cluster_info:
            return None
        
        # Busca coredumps do cluster
        all_coredumps = self.repo.list_all_coredumps()
        cluster_coredumps = [
            cd for cd in all_coredumps 
            if len(cd) > 3 and cd[3] == cluster_id
        ]
        
        return {
            "cluster_id": cluster_id,
            "cluster_name": cluster_info[1] if len(cluster_info) > 1 else "Unknown",
            "coredump_count": len(cluster_coredumps),
            "coredumps": cluster_coredumps,
        }
    
    def get_coredump_details(self, coredump_id: int) -> Optional[dict[str, Any]]:
        """Retorna detalhes de um coredump específico."""
        all_coredumps = self.repo.list_all_coredumps()
        
        for cd in all_coredumps:
            if len(cd) > 0 and cd[0] == coredump_id:
                return {
                    "id": cd[0],
                    "device_mac": cd[1] if len(cd) > 1 else None,
                    "firmware_id": cd[2] if len(cd) > 2 else None,
                    "cluster_id": cd[3] if len(cd) > 3 else None,
                    "received_at": cd[4] if len(cd) > 4 else None,
                    "raw_path": cd[5] if len(cd) > 5 else None,
                    "report_path": cd[6] if len(cd) > 6 else None,
                }
        
        return None


def create_analysis_dashboard(repo: IDataRepository) -> AnalysisDashboard:
    """Factory function para criar dashboard de análise."""
    return AnalysisDashboard(repo)


__all__ = ["AnalysisDashboard", "create_analysis_dashboard"]

