"""Dashboard TUI com seletores e geração de gráficos locais (matplotlib).

Funcionalidades adicionadas:
 - Lista de firmwares com checkboxes (multi-seleção)
 - Botões para escolher tipo de gráfico
 - Geração de gráficos usando matplotlib em threads (não-bloqueante)

Notas:
 - Usa o banco definido em `backend/db_manager.py` (importado como `db`).
 - Para evitar travamentos do TUI, cada plot é criado em uma thread daemon
   que chama `plt.show(block=False)` para abrir janelas independentes.
"""
import os
import sys
import logging
import sqlite3
import multiprocessing
from pathlib import Path
from typing import Any, Optional, Sequence
import re

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Label, Button, Checkbox
from textual.containers import Vertical, Horizontal, VerticalScroll
from textual.screen import Screen
from rich.text import Text

# Adiciona diretório raiz ao path primeiro
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Logger para este módulo (será configurado por main.py)
logger = logging.getLogger("gui")

# Import repositório em vez de db_manager
try:
    from backend.components.data_repository import create_repository  # type: ignore[import]
    _repo = create_repository()
except ImportError as e:
    logger.error("Erro ao importar create_repository em dashboard.py: %s", e)
    raise

is_dashboard_standalone = False


# Tipos de gráficos suportados
CHART_TYPES = [
    ("coredumps_per_firmware", "Coredumps por Firmware"),
    ("distinct_clusters_per_firmware", "Tipos de Falha por Firmware"),
    ("distribution_by_cluster", "Distribuição por Cluster"),
    ("time_evolution", "Evolução Temporal"),
    ("coredumps_per_device", "Coredumps por Dispositivo (top 50)"),
    ("normalized_failure_rate", "Taxa de Falhas Normalizada"),
    ("heatmap_failures", "Mapa de Calor (Hora/Dia)"),
    ("health_overview", "Visão Consolidada de Saúde"),
]


# Define qual tipo de filtro cada gráfico usa (firmware ou device)
CHART_FILTER_KIND = {
    "coredumps_per_firmware": "firmware",
    "distinct_clusters_per_firmware": "firmware",
    "distribution_by_cluster": "firmware",
    "time_evolution": "firmware",
    "coredumps_per_device": "device",
    "normalized_failure_rate": "firmware",
    "heatmap_failures": "firmware",
    "health_overview": "firmware",
}


def _fetch_rows(sql: str, params: Sequence[Any] | None = None) -> list[sqlite3.Row]:
    """Executa uma query simples no DB e retorna as linhas.

    Cada chamada abre uma nova conexão (seguro para threads).
    """
    params = params or ()
    conn = sqlite3.connect(str(_repo.get_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        return rows
    finally:
        conn.close()


def _plot_worker(chart_key: str, filter_values: list, db_path: str) -> None:
    """Worker que roda em processo separado e executa a plotagem com matplotlib.

    Usar um processo isola o backend gráfico (tk/qt) do loop do Textual e evita
    problemas de thread-safe com o tkinter backend no Windows.
    """
    try:
        import sqlite3 as _sqlite3
        import matplotlib
        import matplotlib.pyplot as plt
        from matplotlib import dates as mdates

        matplotlib.use(matplotlib.get_backend())

        import logging as _logging
        _logging.getLogger().setLevel(_logging.DEBUG)

        def _fetch(sql: str, params=None):
            params = params or ()
            conn = _sqlite3.connect(db_path)
            conn.row_factory = _sqlite3.Row
            try:
                cur = conn.execute(sql, params)
                return cur.fetchall()
            finally:
                conn.close()

        if chart_key == "coredumps_per_firmware":
            # LEFT JOIN from firmwares to coredumps so firmwares without coredumps
            # appear with COUNT = 0
            base = (
                "SELECT f.firmware_id AS firmware_id, f.name || ' ' || f.version AS fw, "
                "COUNT(c.coredump_id) AS total "
                "FROM firmwares f LEFT JOIN coredumps c ON c.firmware_id_on_crash = f.firmware_id "
            )
            if filter_values:
                placeholders = ",".join(["?"] * len(filter_values))
                sql = base + f"WHERE f.firmware_id IN ({placeholders}) GROUP BY f.firmware_id, fw"
                rows = _fetch(sql, tuple(filter_values))
            else:
                sql = base + "GROUP BY f.firmware_id, fw"
                rows = _fetch(sql, ())
            labels = [r["fw"] for r in rows]
            vals = [r["total"] for r in rows]

            fig, ax = plt.subplots()
            # Usar barras verticais para 'Coredumps por Firmware'
            ax.bar(labels, vals, color="tab:blue")
            ax.set_ylabel("Coredumps")
            ax.set_title("Coredumps por Firmware")
            try:
                # Forçar ticks inteiros no eixo Y (contagens discretas)
                import matplotlib.ticker as mticker
                ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
                plt.xticks(rotation=45, ha="right")
                # Anotar cada barra com o valor inteiro
                for rect, v in zip(ax.patches, vals):
                    try:
                        ax.annotate(str(int(v)), xy=(rect.get_x() + rect.get_width() / 2, v),
                                    xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=8)
                    except Exception:
                        pass
            except Exception:
                pass

        elif chart_key == "distinct_clusters_per_firmware":
            # LEFT JOIN to include firmwares with 0 distinct clusters
            base = (
                "SELECT f.firmware_id AS firmware_id, f.name || ' ' || f.version AS fw, "
                "COUNT(DISTINCT c.cluster_id) AS total "
                "FROM firmwares f LEFT JOIN coredumps c ON c.firmware_id_on_crash = f.firmware_id "
            )
            if filter_values:
                placeholders = ",".join(["?"] * len(filter_values))
                sql = base + f"WHERE f.firmware_id IN ({placeholders}) GROUP BY f.firmware_id, fw"
                rows = _fetch(sql, tuple(filter_values))
            else:
                sql = base + "GROUP BY f.firmware_id, fw"
                rows = _fetch(sql, ())
            labels = [r["fw"] for r in rows]
            vals = [r["total"] for r in rows]

            fig, ax = plt.subplots()
            ax.bar(labels, vals, color="tab:orange")
            ax.set_ylabel("Tipos de Falha Distintos")
            ax.set_title("Tipos de Falha por Firmware")
            try:
                import matplotlib.ticker as mticker
                ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
                plt.xticks(rotation=45, ha="right")
                for rect, v in zip(ax.patches, vals):
                    try:
                        ax.annotate(str(int(v)), xy=(rect.get_x() + rect.get_width() / 2, v),
                                    xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=8)
                    except Exception:
                        pass
            except Exception:
                pass

        elif chart_key == "distribution_by_cluster":
            base = (
                "SELECT cl.name AS cluster_name, COUNT(*) AS total "
                "FROM coredumps c JOIN clusters cl ON cl.cluster_id = c.cluster_id "
            )
            if filter_values:
                placeholders = ",".join(["?"] * len(filter_values))
                sql = base + f"WHERE c.firmware_id_on_crash IN ({placeholders}) GROUP BY cl.cluster_id, cl.name ORDER BY total DESC"
                rows = _fetch(sql, tuple(filter_values))
            else:
                sql = base + "GROUP BY cl.cluster_id, cl.name ORDER BY total DESC"
                rows = _fetch(sql, ())
            labels = [r["cluster_name"] or "(unassigned)" for r in rows]
            vals = [r["total"] for r in rows]

            fig, ax = plt.subplots()
            ax.pie(vals, labels=labels, autopct="%1.1f%%")
            ax.set_title("Distribuição de Falhas por Cluster")

        elif chart_key == "time_evolution":
            base = (
                "SELECT DATE(received_at, 'unixepoch') AS data, COUNT(*) AS total "
                "FROM coredumps "
            )
            if filter_values:
                placeholders = ",".join(["?"] * len(filter_values))
                sql = base + f"WHERE firmware_id_on_crash IN ({placeholders}) GROUP BY DATE(received_at, 'unixepoch') ORDER BY DATE(received_at, 'unixepoch')"
                rows = _fetch(sql, tuple(filter_values))
            else:
                sql = base + "GROUP BY DATE(received_at, 'unixepoch') ORDER BY DATE(received_at, 'unixepoch')"
                rows = _fetch(sql, ())
            dates = [r["data"] for r in rows]
            vals = [r["total"] for r in rows]

            import datetime as _dt
            xdates = [_dt.datetime.strptime(d, "%Y-%m-%d") for d in dates]
            fig, ax = plt.subplots()
            ax.plot(xdates, vals, marker="o")
            ax.set_title("Evolução Temporal de Coredumps")
            ax.set_ylabel("Quantidade")
            try:
                import matplotlib.ticker as mticker
                ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
            except Exception:
                pass
            ax.xaxis.set_major_locator(mdates.AutoDateLocator())
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
            try:
                plt.xticks(rotation=45, ha="right")
            except Exception:
                pass
            # Anotar pontos com valores inteiros
            try:
                for x, y in zip(xdates, vals):
                    ax.annotate(str(int(y)), xy=(x, y), xytext=(0, 5), textcoords='offset points', ha='center', fontsize=8)
            except Exception:
                pass

        elif chart_key == "coredumps_per_device":
            base = (
                "SELECT device_mac_address AS mac, COUNT(*) AS total "
                "FROM coredumps "
            )
            # Here filter_values expected to be device MAC addresses
            if filter_values:
                placeholders = ",".join(["?"] * len(filter_values))
                sql = base + f"WHERE device_mac_address IN ({placeholders}) GROUP BY device_mac_address ORDER BY total DESC LIMIT 50"
                rows = _fetch(sql, tuple(filter_values))
            else:
                sql = base + "GROUP BY device_mac_address ORDER BY total DESC LIMIT 50"
                rows = _fetch(sql, ())
            labels = [r["mac"] for r in rows]
            vals = [r["total"] for r in rows]

            fig, ax = plt.subplots(figsize=(10, max(4, len(labels) * 0.25)))
            ax.barh(labels, vals, color="tab:green")
            ax.set_xlabel("Coredumps")
            ax.set_title("Coredumps por Dispositivo (top 50)")

        else:
            return

        try:
            plt.tight_layout()
        except Exception:
            pass

        try:
            # Exibir de forma bloqueante no processo filho (não afeta TUI)
            plt.show()
        except Exception:
            pass

    except Exception:
        logging.exception("Erro no worker de plotagem")


class DashboardScreen(Screen):
    """Tela de Dashboard com seletores e geração de gráficos."""

    TITLE = "Dashboard de Coredumps"
    if is_dashboard_standalone:
        BINDINGS = [("q", "app.pop_screen", "Voltar ao Menu")]
    else:
        BINDINGS = [("q", "app.exit", "Sair")]

    CSS = """
    Screen {
        background: $panel; /* use textual theme variable if available */
        color: $text;
    }
    #dashboard-container { padding: 1; }
    #left-panel, #right-panel { background: $panel; color: $text; padding: 1; }
    #fw-scroll, #dev-scroll, #chart-buttons { background: $background; color: $text; }
    #chart-buttons Button, #fw-actions Button, #dev-actions Button {
        margin: 0 0 1 0; width: auto; background: #0b66ff; color: #ffffff; border: none; padding: 0 1;
    }
    #chart-buttons Button:hover, #fw-actions Button:hover, #dev-actions Button:hover {
        background: #0955d9; color: #fff; border: none;
    }
    /* Garantir contraste quando o botão está em foco: manter fundo azul e texto branco */
    #chart-buttons Button:focus, #fw-actions Button:focus, #dev-actions Button:focus {
        background: #0b66ff;
        color: #ffffff;
        border: none;
        /* usar box-shadow-like effect via border-top/bottom para destaque (Textual limita propriedades) */
        padding: 0 1;
    }
    #status { color: $text; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="dashboard-container"):
            # Painel esquerdo: seleção de firmwares
            with Vertical(id="left-panel"):
                yield Label("Firmwares", id="fw-label")
                with VerticalScroll(id="fw-scroll"):
                    # Checkboxes serão inseridos dinamicamente no on_mount
                    yield Static("Carregando firmwares...", id="fw-loading")
                with Horizontal(id="fw-actions"):
                    yield Button("Selecionar Todos", id="select_all_fw", variant="primary")
                    yield Button("Limpar", id="clear_fw", variant="default")
                # Lista de devices abaixo dos firmwares
                yield Label("Devices", id="dev-label")
                with VerticalScroll(id="dev-scroll"):
                    yield Static("Carregando devices...", id="dev-loading")
                with Horizontal(id="dev-actions"):
                    yield Button("Selecionar Todos", id="select_all_dev", variant="primary")
                    yield Button("Limpar", id="clear_dev", variant="default")

            # Painel direito: botões de gráfico e área de status
            with Vertical(id="right-panel"):
                yield Label("Gráficos", id="chart-label")
                # Use VerticalScroll para empilhar botões verticalmente e permitir rolagem
                with VerticalScroll(id="chart-buttons"):
                    # Botões criados dinamicamente abaixo (empilhados verticalmente)
                    for key, label in CHART_TYPES:
                        yield Button(label, id=f"chart_{key}")

                yield Static("Selecione firmwares e clique em um gráfico.", id="status")

        yield Footer()

    def on_mount(self) -> None:
        """Carrega firmwares do DB e popula checkboxes."""
        try:
            _repo.create_database()
        except Exception:
            logging.exception("Falha ao criar/verificar DB")
        # Remover o texto de carregando e popular checkboxes
        try:
            container = self.query_one("#fw-scroll")
            container.remove_children()
        except Exception:
            pass

        try:
            rows = _repo.list_firmwares()
            # db.list_firmwares retorna lista de sqlite3.Row
            if not rows:
                container.mount(Static("Nenhum firmware encontrado."))
                return

            for r in rows:
                fw_id = r[0]
                # sqlite3.Row suporta indexação por nome (r['name']) mas não .get()
                try:
                    name = r[1]
                except Exception:
                    # fallback para índice posicional
                    name = str(r[1]) if len(r) > 1 else str(r[0])
                try:
                    version = r[2]
                except Exception:
                    version = str(r[2]) if len(r) > 2 else ""

                label = f"{name} {version} (id={fw_id})"
                cb = Checkbox(label, id=f"fw_{fw_id}")
                container.mount(cb)

        except Exception as e:
            logging.exception("Falha ao carregar firmwares: %s", e)
            try:
                container.mount(Static(f"Erro ao carregar firmwares: {e}"))
            except Exception:
                pass

        # Popula lista de devices
        try:
            dcontainer = self.query_one("#dev-scroll")
            dcontainer.remove_children()
        except Exception:
            dcontainer = None

        try:
            dev_rows = _repo.list_devices()
            if not dev_rows:
                if dcontainer:
                    dcontainer.mount(Static("Nenhum device encontrado."))
            else:
                for r in dev_rows:
                    mac = r[0]
                    label = f"{mac}"
                    # sanitize id: replace invalid chars with underscore and prefix to avoid starting with number
                    sanitized = re.sub(r"[^A-Za-z0-9_-]", "_", mac)
                    cb = Checkbox(label, id=f"dev_{sanitized}")
                    # store original MAC on widget for reliable retrieval
                    try:
                        setattr(cb, "mac", mac)
                    except Exception:
                        pass
                    if dcontainer:
                        dcontainer.mount(cb)
        except Exception as e:
            logging.exception("Falha ao carregar devices: %s", e)
            try:
                if dcontainer:
                    dcontainer.mount(Static(f"Erro ao carregar devices: {e}"))
            except Exception:
                pass

    # ------------ Helpers de seleção de firmwares ----------------
    def _get_selected_firmware_ids(self) -> list[int]:
        """Retorna lista de firmware_id selecionados (checkboxes checados).

        Se nenhum selecionado, retorna lista vazia.
        """
        ids: list[int] = []
        try:
            for cb in self.query(Checkbox):
                try:
                    if cb.value:
                        # id do checkbox tem formato fw_<id>
                        _, fid = cb.id.split("_")
                        ids.append(int(fid))
                except Exception:
                    continue
        except Exception:
            logging.exception("Erro ao coletar firmwares selecionados")
        return ids

    def _set_all_fw_checked(self, checked: bool) -> None:
        for cb in self.query(Checkbox):
            try:
                if cb.id and cb.id.startswith("fw_"):
                    cb.value = checked
                    try:
                        cb.refresh()
                    except Exception:
                        pass
            except Exception:
                pass

    def _get_selected_device_ids(self) -> list[str]:
        ids: list[str] = []
        try:
            for cb in self.query(Checkbox):
                try:
                    if cb.id and cb.id.startswith("dev_") and cb.value:
                        mac = getattr(cb, "mac", None)
                        if mac:
                            ids.append(mac)
                        else:
                            # fallback: try to reconstruct (replace underscores with colon is unreliable)
                            _, rest = cb.id.split("_", 1)
                            ids.append(rest)
                except Exception:
                    continue
        except Exception:
            logging.exception("Erro ao coletar devices selecionados")
        return ids

    def _set_all_dev_checked(self, checked: bool) -> None:
        try:
            for cb in self.query(Checkbox):
                try:
                    if cb.id and cb.id.startswith("dev_"):
                        cb.value = checked
                        try:
                            cb.refresh()
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass

    # ------------ Handlers de botões ----------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "select_all_fw":
            self._set_all_fw_checked(True)
            return
        if bid == "clear_fw":
            self._set_all_fw_checked(False)
            return
        if bid == "select_all_dev":
            self._set_all_dev_checked(True)
            return
        if bid == "clear_dev":
            self._set_all_dev_checked(False)
            return

        if bid.startswith("chart_"):
            chart_key = bid.replace("chart_", "")
            filter_kind = CHART_FILTER_KIND.get(chart_key, "firmware")

            if filter_kind == "firmware":
                values = self._get_selected_firmware_ids()
                if not values:
                    fw_rows = _repo.list_firmwares()
                    values = [r[0] for r in fw_rows]
            else:  # device
                values = self._get_selected_device_ids()
                if not values:
                    dev_rows = _repo.list_devices()
                    values = [r[0] for r in dev_rows]

            # Atualiza status
            status = self.query_one("#status", Static)
            status.update(f"Gerando gráfico '{chart_key}' para {filter_kind}s: {values}...")

            # debug log
            logging.getLogger().info("Request to plot %s for %s: %s", chart_key, filter_kind, values)

            # Executa plot em processo separado para isolar o backend gráfico
            db_path = str(_repo.get_db_path())
            p = multiprocessing.Process(target=_plot_worker, args=(chart_key, tuple(values), db_path), daemon=True)
            p.start()
    # ------------ Plotagem (thread-safe) ----------------
    def _generate_and_show_chart(self, chart_key: str, firmware_ids: list[int]) -> None:
        """Gera o gráfico correspondente e abre a janela matplotlib (não-bloqueante).

        Cada plot abre sua própria figura. Chamamos `plt.show(block=False)`
        e um pequeno `plt.pause()` para forçar o loop do backend a processar a
        criação da janela.
        """
        try:
            import matplotlib
            import matplotlib.pyplot as plt
            from matplotlib import dates as mdates

            matplotlib.use(matplotlib.get_backend())
            plt.ion()

            if chart_key == "coredumps_per_firmware":
                base = (
                    "SELECT f.firmware_id AS firmware_id, f.name || ' ' || f.version AS fw, "
                    "COUNT(c.coredump_id) AS total "
                    "FROM firmwares f LEFT JOIN coredumps c ON c.firmware_id_on_crash = f.firmware_id "
                )
                if firmware_ids:
                    placeholders = ",".join(["?"] * len(firmware_ids))
                    sql = base + f"WHERE f.firmware_id IN ({placeholders}) GROUP BY f.firmware_id, fw"
                    rows = _fetch_rows(sql, tuple(firmware_ids))
                else:
                    sql = base + "GROUP BY f.firmware_id, fw"
                    rows = _fetch_rows(sql, ())
                labels = [r["fw"] for r in rows]
                vals = [r["total"] for r in rows]

                fig, ax = plt.subplots()
                # Usar barras verticais para 'Coredumps por Firmware'
                ax.bar(labels, vals, color="tab:blue")
                ax.set_ylabel("Coredumps")
                ax.set_title("Coredumps por Firmware")
                try:
                    import matplotlib.ticker as mticker
                    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
                    plt.xticks(rotation=45, ha="right")
                    for rect, v in zip(ax.patches, vals):
                        try:
                            ax.annotate(str(int(v)), xy=(rect.get_x() + rect.get_width() / 2, v),
                                        xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=8)
                        except Exception:
                            pass
                except Exception:
                    pass

            elif chart_key == "distinct_clusters_per_firmware":
                base = (
                    "SELECT f.firmware_id AS firmware_id, f.name || ' ' || f.version AS fw, "
                    "COUNT(DISTINCT c.cluster_id) AS total "
                    "FROM firmwares f LEFT JOIN coredumps c ON c.firmware_id_on_crash = f.firmware_id "
                )
                if firmware_ids:
                    placeholders = ",".join(["?"] * len(firmware_ids))
                    sql = base + f"WHERE f.firmware_id IN ({placeholders}) GROUP BY f.firmware_id, fw"
                    rows = _fetch_rows(sql, tuple(firmware_ids))
                else:
                    sql = base + "GROUP BY f.firmware_id, fw"
                    rows = _fetch_rows(sql, ())
                labels = [r["fw"] for r in rows]
                vals = [r["total"] for r in rows]

                fig, ax = plt.subplots()
                ax.bar(labels, vals, color="tab:orange")
                ax.set_ylabel("Tipos de Falha Distintos")
                ax.set_title("Tipos de Falha por Firmware")
                try:
                    import matplotlib.ticker as mticker
                    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
                    plt.xticks(rotation=45, ha="right")
                    for rect, v in zip(ax.patches, vals):
                        try:
                            ax.annotate(str(int(v)), xy=(rect.get_x() + rect.get_width() / 2, v),
                                        xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=8)
                        except Exception:
                            pass
                except Exception:
                    pass

            elif chart_key == "distribution_by_cluster":
                base = (
                    "SELECT cl.name AS cluster_name, COUNT(*) AS total "
                    "FROM coredumps c JOIN clusters cl ON cl.cluster_id = c.cluster_id "
                )
                if firmware_ids:
                    placeholders = ",".join(["?"] * len(firmware_ids))
                    sql = base + f"WHERE c.firmware_id_on_crash IN ({placeholders}) GROUP BY cl.name ORDER BY total DESC"
                    rows = _fetch_rows(sql, tuple(firmware_ids))
                else:
                    sql = base + "GROUP BY cl.name ORDER BY total DESC"
                    rows = _fetch_rows(sql, ())
                labels = [r["cluster_name"] or "(unassigned)" for r in rows]
                vals = [r["total"] for r in rows]

                fig, ax = plt.subplots()
                ax.pie(vals, labels=labels, autopct="%1.1f%%")
                ax.set_title("Distribuição de Falhas por Cluster")

            elif chart_key == "time_evolution":
                base = (
                    "SELECT DATE(received_at, 'unixepoch') AS data, COUNT(*) AS total "
                    "FROM coredumps "
                )
                if firmware_ids:
                    placeholders = ",".join(["?"] * len(firmware_ids))
                    sql = base + f"WHERE firmware_id_on_crash IN ({placeholders}) GROUP BY data ORDER BY data"
                    rows = _fetch_rows(sql, tuple(firmware_ids))
                else:
                    sql = base + "GROUP BY data ORDER BY data"
                    rows = _fetch_rows(sql, ())
                dates = [r["data"] for r in rows]
                vals = [r["total"] for r in rows]

                # Converter strings para datetimes
                import datetime
                xdates = [datetime.datetime.strptime(d, "%Y-%m-%d") for d in dates]
                fig, ax = plt.subplots()
                ax.plot(xdates, vals, marker="o")
                ax.set_title("Evolução Temporal de Coredumps")
                ax.set_ylabel("Quantidade")
                try:
                    import matplotlib.ticker as mticker
                    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
                except Exception:
                    pass
                ax.xaxis.set_major_locator(mdates.AutoDateLocator())
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
                try:
                    plt.xticks(rotation=45, ha="right")
                except Exception:
                    pass
                try:
                    for x, y in zip(xdates, vals):
                        ax.annotate(str(int(y)), xy=(x, y), xytext=(0, 5), textcoords='offset points', ha='center', fontsize=8)
                except Exception:
                    pass

            elif chart_key == "coredumps_per_device":
                base = (
                    "SELECT device_mac_address AS mac, COUNT(*) AS total "
                    "FROM coredumps "
                )
                if firmware_ids:
                    placeholders = ",".join(["?"] * len(firmware_ids))
                    sql = base + f"WHERE firmware_id_on_crash IN ({placeholders}) GROUP BY device_mac_address ORDER BY total DESC LIMIT 50"
                    rows = _fetch_rows(sql, tuple(firmware_ids))
                else:
                    sql = base + "GROUP BY device_mac_address ORDER BY total DESC LIMIT 50"
                    rows = _fetch_rows(sql, ())
                labels = [r["mac"] for r in rows]
                vals = [r["total"] for r in rows]

                fig, ax = plt.subplots(figsize=(10, max(4, len(labels) * 0.25)))
                ax.barh(labels, vals, color="tab:green")
                ax.set_xlabel("Coredumps")
                ax.set_title("Coredumps por Dispositivo (top 50)")
                try:
                    import matplotlib.ticker as mticker
                    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
                    # Anotar barras horizontais com valores inteiros
                    for rect, v in zip(ax.patches, vals):
                        try:
                            ax.annotate(str(int(v)), xy=(v, rect.get_y() + rect.get_height() / 2),
                                        xytext=(3, 0), textcoords='offset points', va='center', fontsize=8)
                        except Exception:
                            pass
                except Exception:
                    pass
            
            elif chart_key == "normalized_failure_rate":
                sql = """
                SELECT 
                    f.name || ' ' || f.version AS fw,
                    COUNT(c.coredump_id) * 1.0 / COUNT(DISTINCT d.mac_address) AS taxa
                FROM firmwares f
                LEFT JOIN coredumps c ON c.firmware_id_on_crash = f.firmware_id
                LEFT JOIN devices d ON d.current_firmware_id = f.firmware_id
                GROUP BY f.firmware_id
                ORDER BY taxa DESC;
                """
                rows = _fetch_rows(sql)
                labels = [r["fw"] for r in rows]
                vals = [r["taxa"] for r in rows]

                fig, ax = plt.subplots()
                ax.bar(labels, vals, color="tab:purple")
                ax.set_ylabel("Falhas por Dispositivo")
                ax.set_title("Taxa Normalizada de Falhas")
                plt.xticks(rotation=45, ha="right")
                for rect, v in zip(ax.patches, vals):
                    ax.annotate(f"{v:.2f}", xy=(rect.get_x() + rect.get_width() / 2, v),
                                xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', fontsize=8)
                    
            elif chart_key == "heatmap_failures":
                import numpy as np
                import seaborn as sns  # requer instalação
                sql = """
                SELECT 
                    STRFTIME('%w', received_at, 'unixepoch') AS dia,
                    STRFTIME('%H', received_at, 'unixepoch') AS hora,
                    COUNT(*) AS total
                FROM coredumps
                GROUP BY dia, hora;
                """
                rows = _fetch_rows(sql)
                data = np.zeros((7, 24))
                for r in rows:
                    dia = int(r["dia"])
                    hora = int(r["hora"])
                    data[dia, hora] = r["total"]

                fig, ax = plt.subplots(figsize=(10, 5))
                sns.heatmap(data, cmap="YlOrRd", ax=ax)
                ax.set_title("Mapa de Calor de Falhas por Hora/Dia")
                ax.set_xlabel("Hora do dia")
                ax.set_ylabel("Dia da semana (0=Dom, 6=Sáb)")

            elif chart_key == "health_overview":
                sql = """
                SELECT 
                    f.name || ' ' || f.version AS fw,
                    COUNT(c.coredump_id) AS total_falhas,
                    COUNT(DISTINCT c.device_mac_address) AS dispositivos_afetados,
                    COUNT(DISTINCT c.cluster_id) AS tipos_falha
                FROM firmwares f
                LEFT JOIN coredumps c ON f.firmware_id = c.firmware_id_on_crash
                GROUP BY f.firmware_id
                ORDER BY total_falhas DESC;
                """
                rows = _fetch_rows(sql)
                labels = [r["fw"] for r in rows]
                total_falhas = [r["total_falhas"] for r in rows]
                dispositivos = [r["dispositivos_afetados"] for r in rows]
                tipos = [r["tipos_falha"] for r in rows]

                fig, axs = plt.subplots(1, 3, figsize=(12, 4))
                axs[0].bar(labels, total_falhas, color="tab:red")
                axs[1].bar(labels, dispositivos, color="tab:blue")
                axs[2].bar(labels, tipos, color="tab:orange")
                titles = ["Total de Falhas", "Dispositivos Afetados", "Tipos de Falha"]
                for ax, title in zip(axs, titles):
                    ax.set_title(title)
                    ax.tick_params(axis='x', rotation=45)
                fig.suptitle("Visão Consolidada de Saúde por Firmware")


            else:
                logger.error("Chart key desconhecido: %s", chart_key)
                return

            # Exibe figura de forma não-bloqueante
            try:
                plt.tight_layout()
            except Exception:
                pass

            try:
                plt.show(block=False)
                # Pequena pausa para garantir que o backend processe a criação da janela
                plt.pause(0.001)
            except Exception as e:
                logging.exception("Falha ao mostrar plot: %s", e)

        except Exception as e:
            logging.exception("Erro ao gerar gráfico '%s': %s", chart_key, e)

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


if __name__ == "__main__":
    try:
        _repo.create_database()  # Garante que o DB exista antes de rodar
    except Exception:
        logging.exception("Falha ao criar/verificar DB")

    class _RunnerApp(App):
        CSS = DashboardScreen.CSS
        SCREENS = {"dashboard": DashboardScreen}
        # Flag para indicar execução standalone (usada por DashboardScreen.on_key)
        global is_dashboard_standalone
        is_dashboard_standalone = True

        def on_mount(self) -> None:
            self.push_screen("dashboard")

    _RunnerApp().run()