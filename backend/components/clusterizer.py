from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from ..ports import IClusterizerControl, IDataRepository
from .. import coredump_clusterizer as cluster


logger = logging.getLogger("backend.components.clusterizer")


class ClusterizerControl(IClusterizerControl):
    def __init__(self, repo: IDataRepository) -> None:
        """Inicializa o controle do clusterizador.
        
        Args:
            repo: Repositório de dados para acessar coredumps.
        """
        self.repo = repo
        self._thread: Optional[threading.Thread] = None
        self._running: bool = False
        self._check_interval: int = 60  # segundos
    
    def run_once(self) -> None:
        """Executa uma verificação do clusterizador (verifica triggers internos)."""
        cluster.main(self.repo)
    
    def start(self) -> None:
        """Inicia o loop do clusterizador em thread daemon."""
        if self._thread is not None:
            logger.warning("Clusterizador já está rodando")
            return
        
        logger.info("Iniciando clusterizador em thread daemon...")
        self._running = True
        self._thread = threading.Thread(
            target=self._clusterizer_loop,
            name="clusterizer-loop",
            daemon=True
        )
        self._thread.start()
    
    def stop(self) -> None:
        """Para o loop do clusterizador."""
        if self._thread is None:
            return
        
        logger.info("Parando clusterizador...")
        self._running = False
        self._thread = None
    
    def _clusterizer_loop(self) -> None:
        """Loop interno que verifica periodicamente se deve rodar.
        
        Nota: run_once() chama a lógica interna que verifica os triggers automáticos
        (quantidade mínima de coredumps ou tempo decorrido). A clusterização só
        ocorre se os critérios forem atendidos.
        """
        while self._running:
            try:
                # Chama verificação - só executa se triggers internos forem atendidos
                self.run_once()
                time.sleep(self._check_interval)
            except Exception:
                logger.exception("Erro no loop do clusterizador")
                time.sleep(self._check_interval)


def create_clusterizer_control(repo: IDataRepository) -> ClusterizerControl:
    """Factory function para criar controle do clusterizador.
    
    Args:
        repo: Repositório de dados para acessar coredumps.
    
    Returns:
        Instância configurada de ClusterizerControl.
    """
    return ClusterizerControl(repo)

__all__ = ["ClusterizerControl", "create_clusterizer_control"]


