"""Interface textual para gerenciar o banco de dados local usado pelo projeto.

Este módulo implementa uma aplicação TUI (Textual) que permite visualizar e
manipular tabelas do banco (Firmwares, Devices, Clusters e Coredumps).

Objetivos principais:
- Fornecer visualização tabular dos dados.
- Permitir adicionar, editar, deletar e visualizar arquivos de log relacionados
    a coredumps.

O módulo foi escrito com ênfase em docstrings e comentários para facilitar a
manutenção e compreensão por outros desenvolvedores.
"""

import os
import sys
import logging
import webbrowser
from pathlib import Path
from typing import Any, Optional

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, DataTable, Static, Label, Input, Button
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.screen import ModalScreen, Screen
from rich.text import Text

# Adiciona diretório raiz ao path primeiro
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Logger para este módulo (será configurado por main.py)
logger = logging.getLogger("gui")

# Importar FirmwareManagement (interface completa de gerenciamento)
try:
    from backend.components.firmware_management import create_firmware_management  # type: ignore[import]
    from backend.components.data_repository import create_repository  # type: ignore[import]
    _repo = create_repository()
    _mgmt = create_firmware_management(_repo)
except Exception as e:
    logger.error("Erro ao importar/instanciar componentes em manager.py: %s", e)
    raise


class AddEditModal(ModalScreen[dict]):
    """Modal genérico para adicionar ou editar um registro.

    Uso:
        modal = AddEditModal("Título", {"field": "Rótulo"}, {"field": "valor"})
        app.push_screen(modal, callback)

    Quando o usuário pressionar "Salvar", o modal será descartado com um
    dicionário contendo os valores dos inputs. Se o usuário cancelar, o modal
    retorna um dicionário vazio.
    """

    def __init__(self, title: str, fields: dict[str, str], data: Optional[dict] = None):
        """
        Args:
            title (str): Título do modal.
            fields (dict[str, str]): Dicionário de {field_name: label_text}.
            data (Optional[dict]): Dicionário de {field_name: value} para pré-popular (edição).
        """
        super().__init__()
        self.modal_title = title
        self.fields = fields
        self.data = data or {}

    def compose(self) -> ComposeResult:
        """Constrói a árvore de widgets do modal (inputs e botões)."""
        with Vertical(id="dialog"):
            yield Label(self.modal_title)
            with VerticalScroll():
                # Criar inputs para cada campo
                for name, label in self.fields.items():
                    # Garante que o valor padrão seja sempre uma string
                    default_value = str(self.data.get(name, "") or "")
                    yield Label(label)
                    yield Input(default_value, id=f"input_{name}")
            with Horizontal(id="buttons"):
                yield Button("Salvar", variant="primary", id="save")
                yield Button("Cancelar", variant="default", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handler de clique dos botões do modal.

        Fecha o modal retornando um dicionário com os valores dos inputs quando
        o botão 'Salvar' for pressionado; retorna dicionário vazio se cancelar.
        """
        if event.button.id == "save":
            # Coletar dados dos inputs
            result = {}
            for name in self.fields:
                result[name] = self.query_one(f"#input_{name}", Input).value
            # Retorna o dicionário de dados preenchido
            self.dismiss(result)
        else:
            # Retorna dict vazio para cancelar
            self.dismiss({})


class ErrorModal(ModalScreen[None]):
    """Modal simples para exibir uma mensagem de erro.

    Esta tela modal exibe um título e uma mensagem estática. Pode ser fechada
    pelo botão OK ou pelas teclas Enter/Escape.
    """

    def __init__(self, title: str, message: str):
        super().__init__()
        self.modal_title = title
        self.message = message

    def compose(self) -> ComposeResult:
        """Cria os elementos do modal de erro (título, mensagem, botão OK)."""
        with Vertical(id="dialog"):
            yield Label(self.modal_title)
            yield Static(self.message, id="error_message") # Usar Static para texto
            with Horizontal(id="buttons"):
                yield Button("OK", variant="primary", id="ok")

    def on_mount(self) -> None:
        """Executado quando o modal é montado; posiciona o foco no botão OK."""
        # Focar no botão OK
        self.query_one("#ok", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Fecha o modal quando o botão OK for pressionado."""
        self.dismiss()

    def on_key(self, event) -> None:
        """Fecha o modal ao pressionar Enter ou Escape."""
        # Permitir fechar com Enter ou Esc
        if event.key in ("enter", "escape"):
            self.dismiss()


class MenuBar(Static):
    """Barra de menu horizontal que permite navegar entre as tabelas.

    A `MenuBar` renderiza os nomes das abas e mantém um índice selecionado.
    O callback `on_select(index)` é chamado sempre que a seleção muda.
    """
    def __init__(self, options, on_select):
        """Inicializa a barra de menu com opções e callback de seleção."""
        super().__init__()
        self.options = options
        self.selected_index = 0
        self.on_select = on_select

    def render(self) -> Text:
        """Renderiza o texto da barra de menu, destacando a opção selecionada."""
        text = Text()
        for i, name in enumerate(self.options):
            if i == self.selected_index:
                text.append(f"[{i+1}] {name}", style="bold yellow")
            else:
                text.append(f"[{i+1}] {name}", style="dim")
            text.append("   ")
        return text

    def next(self):
        """Seleciona a próxima opção (com wrap-around) e chama o callback."""
        self.selected_index = (self.selected_index + 1) % len(self.options)
        self.refresh()
        self.on_select(self.selected_index)

    def prev(self):
        """Seleciona a opção anterior (com wrap-around) e chama o callback."""
        self.selected_index = (self.selected_index - 1) % len(self.options)
        self.refresh()
        self.on_select(self.selected_index)

    def select(self, index: int):
        """Seleciona explicitamente a opção `index` (se válida) e atualiza UI."""
        if 0 <= index < len(self.options):
            self.selected_index = index
            self.refresh()
            self.on_select(index)


class HelpFooter(Static):
    """Rodapé que exibe atalhos úteis para o usuário.

    O rodapé atualiza dinamicamente para mostrar o atalho 'v' quando a aba
    'Coredumps' está selecionada.
    """
    def render(self) -> Text:
        """Renderiza o texto do rodapé com atalhos dinâmicos conforme aba."""
        help_text = Text()
        help_text.append("q - sair", style="bold white")
        help_text.append("  |  ")
        help_text.append("a - adicionar", style="white")
        help_text.append("  |  ")
        help_text.append("e - editar", style="white")
        help_text.append("  |  ")
        help_text.append("d - deletar", style="white")
        help_text.append("  |  ")
        help_text.append("r - recarregar", style="white")
        help_text.append("  |  ")
        help_text.append("←/→ navegar", style="white")

        # Adiciona atalho 'v' apenas se a app/tela estiver montada e na aba Coredumps
        menu = None
        try:
            # Tenta encontrar a MenuBar em qualquer lugar da aplicação
            menu = self.app.query_one(MenuBar)
        except Exception:
            # Fallback: verifica se a tela ativa tem o atributo `menu`
            active = getattr(self.app, "screen", None)
            if active and hasattr(active, "menu"):
                menu = getattr(active, "menu")

        if menu and getattr(menu, "selected_index", None) == 3:
            help_text.append("  |  ")
            help_text.append("v - visualizar log", style="yellow")
             
        return help_text


class DBManagerScreen(Screen):
    """Tela (Screen) do gerenciador SQLite com menu de navegação.

    Esta classe integra a camada de UI (Textual) com as funções de banco
    definidas em `backend/db_manager.py`. Ela provê ações ao serem acionadas
    por teclas (ex.: 'a' para adicionar, 'e' para editar, 'd' para deletar).

    Principais responsabilidades:
    - Inicializar o banco (chamada a `db.create_database`).
    - Carregar e renderizar a tabela selecionada.
    - Mapear atalhos/ações para operações do banco.
    """

    TITLE = "Gerenciador de Banco de Dados"
    # Mapeamento de chaves (nomes de colunas do DB) para rótulos amigáveis
    # Use este dicionário para personalizar o texto mostrado no cabeçalho
    # da `DataTable` sem alterar as chaves internas (que o código usa).
    COLUMN_LABELS = {
        "firmware_id": "ID",
        "name": "Nome",
        "version": "Versão",
        "elf_path": "ELF",
        "mac_address": "MAC",
        "current_firmware_id": "Firmware ID",
        "chip_type": "Chip",
        "cluster_id": "Cluster ID",
        "log_path": "Log Path",
        "device_mac": "Device MAC",
        "raw_dump_path": "Dump Path",
        "coredump_id": "ID",
        "device_mac_address": "Device MAC",
        "firmware_id_on_crash": "Firmware ID",
        "received_at": "Recebido Em",
    }

    CSS = open(os.path.join(os.path.dirname(__file__), "manager.css"), encoding="utf-8").read()

    def compose(self) -> ComposeResult:
        """Composição inicial dos widgets da aplicação (header, menu, tabela, footer)."""
        yield Header()
        with Vertical():
            self.menu = MenuBar(
                ["Firmwares", "Devices", "Clusters", "Coredumps"],
                on_select=self.show_table
            )
            yield self.menu
            self.table = DataTable()
            yield self.table
        yield HelpFooter()

    def on_mount(self):
        """Executado quando a aplicação é montada; inicializa o DB e a tabela."""
        try:
            _mgmt.get_data_repository().create_database()
        except Exception:
            logging.exception("Falha ao criar/verificar DB")
        self.table.cursor_type = "row"
        self.table.zebra_stripes = True
        self.table.show_header = True
        self.show_table(0)

    def _get_selected_row_data(self) -> tuple[Optional[dict], Any]:
        """Retorna (cells_dict, pk_value) para a linha atualmente selecionada.

        O método tenta acessar estruturas internas do `DataTable` quando
        necessário. Isso pode ser frágil para mudanças na API do Textual, por
        isso tratamentos de fallback são incluídos.

        Retornos:
            cells_dict: dicionário {col_key: valor} ou None em caso de falha.
            pk_value: valor da chave primária (primeira coluna) ou None.
        """
        try:
            coord = getattr(self.table, "cursor_coordinate", None)
            if not coord:
                logger.warning("Nenhuma coordenada de cursor encontrada.")
                return None, None
            row_index, _ = coord

            row_key = None
            if hasattr(self.table, "get_row_key"):
                row_key = self.table.get_row_key(row_index)
            elif hasattr(self.table, "_row_locations"):
                row_key = self.table._row_locations.get_key(row_index)
            
            if not row_key:
                logger.warning("Nenhuma row_key encontrada para o índice %s", row_index)
                return None, None

            cells_dict = {}
            columns = getattr(self.table, "ordered_columns", [])
            
            if hasattr(self.table, "_data"):
                data_row = self.table._data.get(row_key)
                if data_row:
                    cells_dict = {str(col.key): data_row.get(col.key) for col in columns}
            
            if not cells_dict:
                logger.warning("Não foi possível extrair dados da linha (método 1).")
                if hasattr(self.table, "get_row_at"):
                    row_list = self.table.get_row_at(row_index)
                    col_keys = [str(col.key) for col in columns]
                    cells_dict = dict(zip(col_keys, row_list))

            if not cells_dict:
                logger.error("Não foi possível extrair dados da linha para edição/deleção.")
                return None, None
            
            pk_value = None
            if columns:
                first_col_key = str(columns[0].key)
                pk_value = cells_dict.get(first_col_key)
            
            if pk_value is None:
                 logger.warning("Não foi possível determinar o valor da PK.")

            return cells_dict, pk_value

        except Exception as e:
            logging.exception("Erro ao obter dados da linha selecionada: %s", e)
            return None, None

    def action_delete_selected(self):
        """Deleta o registro correspondente à linha atualmente selecionada.

        O comportamento depende da aba atual (firmwares/devices/clusters/coredumps).
        """
        try:
            cells_data, pk_value = self._get_selected_row_data()
            if not cells_data or pk_value is None:
                logger.warning("Delete: Nenhuma linha selecionada ou PK não encontrada.")
                return

            idx = self.menu.selected_index
            if idx == 0:  # Firmwares -> firmware_id
                _mgmt.delete_firmware(int(pk_value))
            elif idx == 1:  # Devices -> mac_address
                _mgmt.delete_device(str(pk_value))
            elif idx == 2:  # Clusters -> cluster_id
                _mgmt.unassign_cluster_from_coredumps(int(pk_value))
                _mgmt.delete_cluster(int(pk_value))
            elif idx == 3:  # Coredumps -> coredump_id
                _mgmt.delete_coredump(int(pk_value))

            # Recarrega tabela
            self.show_table(self.menu.selected_index)
        except Exception as e:
            logging.exception("Falha ao deletar registro: %s", e)


    def action_add_item(self):
        """Abre o modal apropriado para adicionar um novo registro na aba ativa."""
        idx = self.menu.selected_index
        
        try:
            if idx == 0: # Firmwares
                fields = {"name": "Nome", "version": "Versão", "elf_path": "Caminho do ELF"}
                title = "Adicionar Firmware"
            elif idx == 1: # Devices
                fields = {"mac_address": "MAC Address", "current_firmware_id": "Firmware ID", "chip_type": "Chip (Opcional)"}
                title = "Adicionar/Atualizar Device"
            elif idx == 2: # Clusters
                fields = {"name": "Nome do Cluster"}
                title = "Adicionar Cluster"
            elif idx == 3: # Coredumps
                fields = {
                    "device_mac": "MAC do Device",
                    "firmware_id": "Firmware ID (no crash)",
                    "raw_dump_path": "Caminho do Dump"
                }
                title = "Adicionar Coredump"
            else:
                return

            def on_modal_dismiss(data: dict):
                """Callback executado quando o modal de adição é fechado.

                Recebe `data` com os valores inseridos ou dicionário vazio se cancelado.
                """
                if not data:
                    return
                try:
                    if idx == 0:
                        _mgmt.add_firmware(data["name"], data["version"], data["elf_path"])
                    elif idx == 1:
                        _mgmt.add_or_update_device(data["mac_address"], int(data["current_firmware_id"]), data.get("chip_type"))
                    elif idx == 2:
                        _mgmt.add_cluster(data["name"])
                    elif idx == 3:
                        _mgmt.add_coredump(data["device_mac"], int(data["firmware_id"]), data["raw_dump_path"])
                    
                    self.show_table(idx)
                except Exception as e:
                    logging.exception("Falha ao adicionar item: %s | Data: %s", e, data)

            self.app.push_screen(AddEditModal(title, fields), on_modal_dismiss)
        
        except Exception as e:
            logging.exception("Falha ao preparar modal de adição: %s", e)

    def action_edit_item(self):
        """Abre o modal de edição para o item selecionado na aba ativa."""
        idx = self.menu.selected_index
        data, pk_value = self._get_selected_row_data()

        if not data or pk_value is None:
            logger.warning("Edit: Nenhuma linha selecionada ou PK não encontrada.")
            return

        try:
            if idx == 0: # Firmwares (Editar elf_path)
                fields = {"elf_path": "Novo Caminho do ELF"}
                title = f"Editar Firmware ID: {pk_value}"
                modal_data = {"elf_path": data.get("elf_path")}
            
            elif idx == 1: # Devices (Editar firmware_id e chip_type)
                fields = {"current_firmware_id": "Firmware ID", "chip_type": "Chip (Opcional)"}
                title = f"Editar Device MAC: {pk_value}"
                modal_data = {"current_firmware_id": data.get("current_firmware_id"), "chip_type": data.get("chip_type")}

            elif idx == 2: # Clusters (Renomear)
                fields = {"name": "Novo Nome do Cluster"}
                title = f"Editar Cluster ID: {pk_value}"
                modal_data = {"name": data.get("name")}

            elif idx == 3: # Coredumps (Atribuir/Mudar cluster E log_path)
                fields = {
                    "cluster_id": "Novo Cluster ID (vazio para remover)",
                    "log_path": "Caminho do Log (Opcional)"
                }
                title = f"Editar Coredump ID: {pk_value}"
                modal_data = {
                    "cluster_id": data.get("cluster_id"),
                    "log_path": data.get("log_path")
                }
            else:
                return

            def on_modal_dismiss(new_data: dict):
                """Callback executado quando o modal de edição é fechado.

                Recebe `new_data` com os valores atualizados ou dicionário vazio se cancelado.
                """
                if not new_data:
                    return
                try:
                    if idx == 0:
                        _mgmt.update_firmware_path(int(pk_value), new_data["elf_path"])
                    elif idx == 1:
                        _mgmt.add_or_update_device(str(pk_value), int(new_data["current_firmware_id"]), new_data.get("chip_type"))
                    elif idx == 2:
                        _mgmt.rename_cluster(int(pk_value), new_data["name"])
                    elif idx == 3:
                        cluster_id_str = new_data.get("cluster_id", "").strip()
                        new_cluster_id = int(cluster_id_str) if cluster_id_str.isdigit() else None
                        log_path_str = new_data.get("log_path", "").strip()
                        new_log_path = log_path_str if log_path_str else None
                        _mgmt.update_coredump(int(pk_value), new_cluster_id, new_log_path)

                    self.show_table(idx)
                except Exception as e:
                    logging.exception("Falha ao salvar edição: %s | Data: %s", e, new_data)

            self.app.push_screen(AddEditModal(title, fields, modal_data), on_modal_dismiss)
        
        except Exception as e:
            logging.exception("Falha ao preparar modal de edição: %s", e)

    def action_view_log(self):
        """Tenta abrir o arquivo de log associado ao coredump selecionado.

        Mostra mensagens de erro via modal se não houver log_path ou se o arquivo
        estiver ausente.
        """
        if self.menu.selected_index != 3:
            return
            
        data, pk_value = self._get_selected_row_data()
        if not data:
            logger.warning("View: Nenhuma linha selecionada.")
            self.app.push_screen(ErrorModal("Erro", "Nenhuma linha selecionada."))
            return

        log_path_str = data.get("log_path")
        if not log_path_str:
            log_msg = f"Coredump ID {pk_value} não possui um log_path."
            logger.warning("View: %s", log_msg)
            self.app.push_screen(ErrorModal("Erro ao Visualizar", log_msg))
            return

        log_file = Path(log_path_str)
        if not log_file.exists():
            log_msg = f"Arquivo de log não encontrado:\n{log_file}"
            logger.error("View: %s", log_msg)
            self.app.push_screen(ErrorModal("Erro ao Visualizar", log_msg))
            return

        try:
            webbrowser.open(log_file.as_uri())
            logger.info("Abrindo log: %s", log_file)
        except Exception as e:
            log_msg = f"Falha ao abrir o arquivo de log:\n{e}"
            logging.exception(log_msg)
            self.app.push_screen(ErrorModal("Erro ao Visualvert", log_msg))

    def show_table(self, index: int):
        """Carrega e exibe os dados da tabela correspondente ao índice `index`."""
        loaders = [
            _mgmt.list_firmwares,
            _mgmt.list_devices,
            _mgmt.list_clusters,
            _mgmt.list_all_coredumps,
        ]
        titles = ["Firmwares", "Devices", "Clusters", "Coredumps"]

        try:
            loader = loaders[index]
            title = titles[index]

            rows = loader()
            self.table.clear(columns=True)
            self.table.border_title = f"[bold yellow]{title}[/]"

            if not rows:
                self.table.add_column("Vazio")
                self.table.add_row("Sem registros")
            else:
                # rows[0].keys() deve fornecer as chaves na ordem das colunas
                keys = list(rows[0].keys())

                # Adiciona colunas preservando as chaves internas, mas usa rótulos
                # amigáveis definidos em COLUMN_LABELS quando disponíveis.
                for k in keys:
                    # Rótulo padrão a partir do dicionário
                    label = self.COLUMN_LABELS.get(str(k), str(k))

                    # Exceção: 'cluster_id' deve mostrar 'ID' na aba Clusters
                    # (index == 2) e 'Cluster ID' na aba Coredumps (index == 3).
                    if str(k) == "cluster_id":
                        if index == 2:
                            label = "ID"
                        elif index == 3:
                            label = "Cluster ID"

                    # DataTable aceita (key, label) para definir colunas com rótulos
                    try:
                        # Alguns backends do DataTable podem aceitar kwargs diferentes;
                        # usamos key= para garantir que a chave interna seja mantida.
                        self.table.add_column(label, key=str(k))
                    except TypeError:
                        # Fallback para versões que aceitam (label) e não key=
                        self.table.add_column(label)

                for r in rows:
                    self.table.add_row(*[str(v) if v is not None else "" for v in r])
            
            self.query_one(HelpFooter).refresh()

        except Exception as e:
            logging.exception("Falha ao carregar tabela: %s", e)
            self.table.clear(columns=True)
            self.table.add_column("Erro")
            self.table.add_row(f"Não foi possível carregar dados: {e}")
            self.query_one(HelpFooter).refresh()

    def on_key(self, event):
        """Handler de teclado global da aplicação, mapeia teclas para ações."""
        key = event.key.lower()

        if key == "q":
            if hasattr(self, "app") and getattr(self.app, "is_manager_standalone", False):
                try:
                    self.app.exit()
                except Exception:
                    pass
            else:
                try:
                    self.app.pop_screen()
                except Exception:
                    try:
                        self.app.exit()
                    except Exception:
                        pass

        elif key == "a":
            self.action_add_item()

        elif key == "e":
            self.action_edit_item()

        elif key == "v":
            if self.menu.selected_index == 3:
                self.action_view_log()

        elif key == "r":
            self.show_table(self.menu.selected_index)

        elif key == "d":
            self.action_delete_selected()

        elif key in ["1", "2", "3", "4"]:
            self.menu.select(int(key) - 1)

        elif key == "right":
            self.menu.next()

        elif key == "left":
            self.menu.prev()


if __name__ == "__main__":
    # Permite executar o manager.py de forma isolada: cria um App simples
    # que usa `DBManagerScreen` como tela inicial.
    try:
        _mgmt.get_data_repository().create_database()
    except Exception:
        logging.exception("Falha ao criar/verificar DB")

    class _RunnerApp(App):
        CSS = DBManagerScreen.CSS
        SCREENS = {"db_manager": DBManagerScreen}
        # Flag para indicar execução standalone (usada por DBManagerScreen.on_key)
        is_manager_standalone = True

        def on_mount(self) -> None:
            self.push_screen("db_manager")

    _RunnerApp().run()