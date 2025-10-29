"""Aplicação principal TUI (Textual).

Este módulo contém a classe MainApp (ponto de entrada) e a MainMenuScreen
(tela de navegação inicial).

Ele é responsável por:
- Carregar o CSS.
- Registrar as diferentes telas (MainMenu, DBManager, Dashboard).
- Iniciar a aplicação.
- Inicializar o banco de dados na primeira execução.
"""

import os
import sys
import logging

from textual.app import App, ComposeResult
from textual.widgets import Button, Label, Static
from textual.containers import Vertical, Horizontal
from textual.screen import Screen
from textual import events

# Adiciona diretório raiz ao path primeiro (antes de importar manager e dashboard)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Configurar logging centralizado - um único logger para toda a GUI
# Desabilita console pois a GUI TUI usa o terminal completo
from backend.logging_config import setup_logging
logger = setup_logging("gui", enable_console=False)

# Importar as telas dos outros arquivos
from manager import DBManagerScreen, HelpFooter
from dashboard import DashboardScreen

# Importar repositório (IDataRepository)
try:
    from backend.components.data_repository import create_repository  # type: ignore[import]
except Exception as e:
    logger.error("Erro ao importar create_repository em main.py: %s", e)
    raise


class MainMenuScreen(Screen):
    """Tela do Menu Principal."""

    def compose(self) -> ComposeResult:
        # Wrapper que garante centralização horizontal
        with Horizontal(id="menu-root"):
            with Vertical(id="menu-container"):
                yield Label("Menu Principal", id="menu-title")
                yield Button("Gerenciador de Banco de Dados", id="goto-db-manager", variant="primary")
                yield Button("Dashboard", id="goto-dashboard", variant="default")
                yield Button("Sair", id="quit-app", variant="error")
        # Rodapé de ajuda no mesmo estilo do manager.py
        yield HelpFooter()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "goto-db-manager":
            self.app.push_screen("db_manager")
        elif event.button.id == "goto-dashboard":
            self.app.push_screen("dashboard")
        elif event.button.id == "quit-app":
            self.app.exit()

    def on_mount(self) -> None:
        """Configura foco inicial e lista de botões após montagem."""
        # Coleta os botões do menu e foca o primeiro
        self._buttons = list(self.query(Button))
        self._focus_index = 0
        if self._buttons:
            try:
                self._buttons[0].focus()
            except Exception:
                # fallback silencioso se o focus falhar
                pass

    def on_key(self, event: events.Key) -> None:
        """Trata as teclas de seta para mudança de foco e 'q' para sair.

        - setas up/left: vai para o botão anterior
        - setas down/right: vai para o próximo botão
        - q: fecha a aplicação
        """
        key = event.key
        if key == "q":
            self.app.exit()
            return

        # Se não houver botões registrados, ignora
        if not hasattr(self, "_buttons") or not self._buttons:
            return

        if key in ("up", "left"):
            self._focus_index = (self._focus_index - 1) % len(self._buttons)
            try:
                self._buttons[self._focus_index].focus()
            except Exception:
                pass
        elif key in ("down", "right"):
            self._focus_index = (self._focus_index + 1) % len(self._buttons)
            try:
                self._buttons[self._focus_index].focus()
            except Exception:
                pass


class MainApp(App):
    """App principal que gerencia as telas."""

    # Carrega o CSS original do seu arquivo, agora com um fallback
    try:
        MANAGER_CSS = open(os.path.join(os.path.dirname(__file__), "manager.css"), encoding="utf-8").read()
    except FileNotFoundError:
        logger.warning("manager.css não encontrado. Usando CSS padrão.")
        MANAGER_CSS = """
        #dialog {
            padding: 1 2;
            width: 80;
            height: auto;
            max-height: 80%;
            border: thick $primary;
            background: $panel;
        }
        #buttons {
            padding-top: 1;
            align: right $primary;
        }
        """

    try:
        DASHBOARD_CSS = open(os.path.join(os.path.dirname(__file__), "dashboard.css"), encoding="utf-8").read()
    except FileNotFoundError:
        logger.warning("dashboard.css não encontrado. Usando CSS padrão.")
        DASHBOARD_CSS = ""

    # Define o CSS para a nova tela de menu
    try:
        MENU_CSS = open(os.path.join(os.path.dirname(__file__), "menu.css"), encoding="utf-8").read()
    except FileNotFoundError:
        logger.warning("menu.css não encontrado. Usando CSS mínimo.")
        MENU_CSS = ""

    # Combina os CSS
    CSS = MANAGER_CSS + DASHBOARD_CSS + MENU_CSS
    
    # Define as telas que a aplicação pode usar
    # Elas são instanciadas aqui
    SCREENS = {
        "menu": MainMenuScreen,
        "db_manager": DBManagerScreen,
        "dashboard": DashboardScreen
    }
    
    def on_mount(self) -> None:
        # Iniciar na tela do menu
        self.push_screen("menu")


if __name__ == "__main__":
    # Garante que o DB exista via repositório
    try:
        _repo = create_repository()
        _repo.create_database()
    except Exception as e:
        logger.error("Falha ao inicializar repositório/DB: %s", e)
    app = MainApp()
    app.run()