"""Wiring do Backend - Dependency Injection e Inicialização.

Este módulo é responsável por:
1. Criar instâncias dos componentes (Receptor, Interpretador, Repositório, etc)
2. Realizar a injeção de dependências entre componentes
3. Inicializar e coordenar os serviços do backend

Arquitetura:
- Receptor (MqttReceiver): Recebe coredumps via MQTT
- Interpretador (DockerCoredumpParser): Gera relatórios de coredumps
- Repositório (SqliteDataRepository): Gerencia persistência de dados
- Clusterizador (ClusterizerControl): Agrupa coredumps similares
"""
from __future__ import annotations

import logging

try:
    # Prefer relative imports when executed as a package module
    from .components.data_repository import create_repository
    from .components.interpreter import create_parser
    from .components.receiver_mqtt import MqttReceiver
    from .components.clusterizer import create_clusterizer_control
except Exception:
    # Fallback for execution as a script: python backend/wiring.py
    from backend.components.data_repository import create_repository  # type: ignore
    from backend.components.interpreter import create_parser  # type: ignore
    from backend.components.receiver_mqtt import MqttReceiver  # type: ignore
    from backend.components.clusterizer import create_clusterizer_control  # type: ignore


def create_backend_components():
    """Factory para criar componentes do backend para execução standalone.
    
    Cria apenas os componentes necessários para o backend (receptor MQTT e clusterizador).
    A GUI cria suas próprias instâncias de dashboard e firmware_management quando necessário.
    
    Retorna um dicionário com os componentes prontos para uso.
    """
    # 1. Cria o repositório de dados (núcleo do sistema)
    repo = create_repository()
    
    # 2. Cria componentes necessários para o backend standalone
    parser = create_parser()
    clusterizer = create_clusterizer_control(repo)  # Injeta repositório
    
    # 3. Cria o receptor MQTT (depende de repo e parser)
    receiver = MqttReceiver(repo=repo, parser=parser)
    
    return {
        "repository": repo,
        "parser": parser,
        "receiver": receiver,
        "clusterizer": clusterizer,
    }


def main() -> None:
    """Ponto de entrada principal do backend.
    
    Inicializa o sistema de recepção de coredumps via MQTT e o clusterizador.
    """
    import time
    from .logging_config import setup_logging, close_logging
    
    # Configura logging centralizado
    logger = setup_logging("backend")
    
    logger.info("Inicializando componentes do backend...")
    components = create_backend_components()
    
    # Inicia receptor MQTT
    receiver = components["receiver"]
    logger.info("Iniciando receptor MQTT...")
    receiver.start()
    
    # Inicia clusterizador
    clusterizer = components["clusterizer"]
    logger.info("Iniciando clusterizador...")
    clusterizer.start()
    
    logger.info("Backend iniciado com sucesso. Aguardando coredumps...")

    # Mantém processo vivo; os loops rodam em threads
    try:
        while True:
            time.sleep(3600)  # Sleep por 1 hora
    except KeyboardInterrupt:
        logger.info("Recebido sinal de interrupção. Encerrando...")
        receiver.stop()
        clusterizer.stop()
        logger.info("Backend encerrado com sucesso.")
        close_logging(logger)


if __name__ == "__main__":
    main()


