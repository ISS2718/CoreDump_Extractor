"""Configuração centralizada de logging para toda a aplicação.

Este módulo fornece uma função para configurar logging consistente
em todos os componentes (Backend, GUI, etc).
"""

import logging
import os
from pathlib import Path
from logging.handlers import RotatingFileHandler


def setup_logging(log_name: str, level: int = logging.INFO, enable_console: bool = True) -> logging.Logger:
    """Configura logging com saída para console e arquivo rotativo.
    
    Args:
        log_name: Nome do arquivo de log (ex: "backend", "manager", "dashboard")
        level: Nível de logging para CONSOLE (padrão: INFO, pode ser sobrescrito por LOG_LEVEL env var)
        enable_console: Se True, envia logs para console; False apenas para arquivo (padrão: True)
    
    Returns:
        Logger configurado
    
    Variáveis de ambiente:
        LOG_LEVEL: Define o nível de log para o CONSOLE (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    
    Nota: O arquivo SEMPRE salva todos os níveis (DEBUG+) independentemente da configuração.
          A variável LOG_LEVEL controla apenas o que aparece no console.
    """
    # Permite sobrescrever nível do console via variável de ambiente
    console_level = level
    env_level = os.getenv("LOG_LEVEL", "").upper()
    if env_level:
        level_mapping = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL,
        }
        console_level = level_mapping.get(env_level, level)
    
    # Cria diretório de logs
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{log_name}.log"
    
    # Formato detalhado para os logs
    log_format = "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d %(message)s"
    formatter = logging.Formatter(log_format)
    
    # Cria logger específico para este módulo
    # Logger raiz aceita TUDO (DEBUG é o nível mais baixo)
    logger = logging.getLogger(log_name)
    logger.setLevel(logging.DEBUG)  # Logger aceita todos os níveis
    
    # Desabilita propagação para evitar duplicação
    logger.propagate = False
    
    # Verifica se já tem handlers (evita duplicação em múltiplas chamadas)
    if logger.handlers:
        return logger
    
    # Handler para arquivo (sempre presente)
    # ARQUIVO SALVA TUDO (DEBUG+)
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)  # Arquivo recebe todos os níveis
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Handler para console (opcional - desabilitado para GUIs TUI)
    # CONSOLE respeita o nível configurado para não poluir a saída
    if enable_console:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(console_level)  # Console respeita configuração do usuário
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
    
    # Log de inicialização
    logger.info("=" * 80)
    logger.info("Iniciando %s - Logs salvos em: %s", log_name, log_file.absolute())
    logger.debug("Configuração de log: Arquivo=DEBUG (tudo), Console=%s", logging.getLevelName(console_level))
    
    return logger


def close_logging(logger: logging.Logger) -> None:
    """Fecha e limpa handlers de logging.
    
    Args:
        logger: Logger a ser fechado
    """
    logger.info("Encerrando %s", logger.name)
    logger.info("=" * 80)
    
    # Fecha handlers específicos deste logger
    for handler in logger.handlers[:]:
        handler.close()
        logger.removeHandler(handler)

