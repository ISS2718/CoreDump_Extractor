"""Backend for CoreDump Extractor.

Provides components for receiving, interpreting, and clustering coredumps.

Arquitetura de Componentes:
- Receptor: Recebe coredumps via MQTT (ICoreDumpIngestor)
- Interpretador: Gera relatórios de coredumps (ICoreDumpParser)
- Repositório: Gerencia persistência de dados (IDataRepository)
- Clusterizador: Agrupa coredumps similares (IClusterizerControl)
- Dashboard de Análise: Interface para visualização (IAnalysisDashboard)
- Gerenciamento de Firmware: Interface para gerenciamento (IFirmwareManagement)
"""
from __future__ import annotations

from .wiring import create_backend_components

__all__ = ["create_backend_components"]


