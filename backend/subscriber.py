"""subscriber.py

Assinante MQTT para reconstrução e registro de CoreDumps fragmentados.

Topologias de tópico:
    BASE_TOPIC/<MAC> -> mensagem JSON inicial {"parts": N}
    BASE_TOPIC/<MAC>/<INDEX> -> cada parte (binário ou base64)

Fluxo resumido:
    1. Recebe metadados iniciais e inicia sessão.
    2. Recebe partes (índices 0..N-1 ou 1..N) e armazena.
    3. Ao completar, concatena e grava arquivo bruto e agenda processamento.
    4. Limpeza periódica remove sessões não concluídas após timeout.

Variáveis de ambiente relevantes:
    MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS, MQTT_BASE_TOPIC (padrão: 'coredump')
    COREDUMP_TIMEOUT_SECONDS (padrão: 600)
    COREDUMP_RAWS_OUTPUT_DIR (padrão: 'db/coredumps/raws')
    COREDUMP_REPORTS_OUTPUT_DIR (padrão: 'db/coredumps/reports')
    COREDUMP_ACCEPT_BASE64 (padrão: '1')
    COREDUMP_PROCESSING_DOCKER_IMAGE (padrão: 'espressif/idf:v5.5.1')

Formato de dados externo (DB):
    db_manager.get_device(mac) -> (mac: str, current_firmware_id: int | None, chip_type: str)
    db_manager.get_firmware_by_id(id) -> (id: int, name: str, version: str, elf_path: str)

TODO: Remover credenciais hardcoded; usar somente variáveis de ambiente ou secret manager.
"""

from __future__ import annotations

import os
import json
import threading
import time
import logging
import base64
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple, Any
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

import paho.mqtt.client as paho
from paho import mqtt

logger = logging.getLogger("backend.subscriber")

try:
    import db_manager  # Funções de consulta/registro no banco local
except ImportError as exc:
    raise ImportError("db_manager.py não encontrado no PYTHONPATH.") from exc


# ---------------------------- Config / Constantes ----------------------------
MQTT_HOST: str = os.getenv(
    "MQTT_HOST", "d7dc78b4d42d49e8a71a4edfcfb1d6ca.s1.eu.hivemq.cloud"
)
MQTT_PORT: int = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USER: str = os.getenv("MQTT_USER", "BACKEND-TEST")
MQTT_PASS: str = os.getenv("MQTT_PASS", "1qxe)y~P9U+57C.!")
BASE_TOPIC: str = os.getenv("MQTT_BASE_TOPIC", "coredump")
SESSION_TIMEOUT: int = int(os.getenv("COREDUMP_TIMEOUT_SECONDS", "600"))
RAWS_OUTPUT_DIR: Path = Path(os.getenv("COREDUMP_RAWS_OUTPUT_DIR", "db/coredumps/raws"))
REPORTS_OUTPUT_DIR: Path = Path(
    os.getenv("COREDUMP_REPORTS_OUTPUT_DIR", "db/coredumps/reports")
)
ACCEPT_BASE64: bool = os.getenv("COREDUMP_ACCEPT_BASE64", "1") not in (
    "0",
    "false",
    "False",
)
DOCKER_IMAGE: str = os.getenv(
    "COREDUMP_PROCESSING_DOCKER_IMAGE", "espressif/idf:v5.5.1"
)
PROCESSING_DISABLED: bool = os.getenv("COREDUMP_PROCESSING_DISABLED", "0") in ("1", "true", "True")

RAWS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

if (
    not logging.getLogger().handlers
):  # Evita reconfiguração se já houver handler no app principal
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
    )


# ---------------------------- Estruturas de Dados ---------------------------
@dataclass
class CoreDumpSession:
    """Estado de montagem de um coredump.

    Permite índices 0-based ou 1-based, concluindo quando todas as partes chegam.
    """

    mac: str
    expected_parts: int
    start_time: float = field(default_factory=time.time)
    parts: Dict[int, bytes] = field(default_factory=dict)
    completed: bool = False

    def add_part(self, index: int, data: bytes) -> None:
        if index < 0 or index >= self.expected_parts + 1:  # tolera 1-based
            logger.warning("parte.fora_intervalo mac=%s index=%s", self.mac, index)
            return
        if index in self.parts:
            logger.debug("parte.duplicada mac=%s index=%s", self.mac, index)
            return
        self.parts[index] = data

    def is_complete(self) -> bool:
        if self.completed:
            return True
        if len(self.parts) < self.expected_parts:
            return False
        indices = sorted(self.parts.keys())
        # Formatos aceitos: 0..N-1 ou 1..N
        return (
            indices[0] == 0
            and indices[-1] == self.expected_parts - 1
            and len(indices) == self.expected_parts
        ) or (
            indices[0] == 1
            and indices[-1] == self.expected_parts
            and len(indices) == self.expected_parts
        )

    def assemble(self) -> bytes:
        indices = sorted(self.parts.keys())
        base = indices[0]
        ordered = [self.parts[i] for i in range(base, base + self.expected_parts)]
        return b"".join(ordered)


class CoreDumpAssembler:
    """Gerencia sessões de montagem e despacho para processamento."""

    def __init__(self) -> None:
        self._sessions: Dict[str, CoreDumpSession] = {}
        self._lock = threading.Lock()

    def start_session(self, mac: str, expected_parts: int) -> None:
        with self._lock:
            current = self._sessions.get(mac)
            if current and not current.completed:
                logger.info("sessao.reiniciada mac=%s", mac)
            self._sessions[mac] = CoreDumpSession(
                mac=mac, expected_parts=expected_parts
            )
            logger.info("sessao.iniciada mac=%s parts=%d", mac, expected_parts)

    def add_part(self, mac: str, index: int, data: bytes) -> Optional[str]:
        with self._lock:
            sess = self._sessions.get(mac)
            if not sess:
                logger.warning("parte.sem_sessao mac=%s index=%s", mac, index)
                return None
            sess.add_part(index, data)
            if sess.is_complete():
                blob = sess.assemble()
                received_at = int(time.time())
                filepath = self._write_coredump(mac, blob, received_at)
                sess.completed = True
                logger.info(
                    "coredump.completo mac=%s file=%s bytes=%d",
                    mac,
                    filepath,
                    len(blob),
                )
                # Processamento assíncrono para não bloquear loop MQTT
                threading.Thread(
                    target=self.process_and_register_coredump,
                    args=(mac, filepath, received_at),
                    daemon=True,
                ).start()
                return filepath
        return None

    def _write_coredump(self, mac: str, data: bytes, received_at: int) -> str:
        try:
            tz_sp = ZoneInfo("America/Sao_Paulo")
        except Exception:
            tz_sp = timezone(timedelta(hours=-3))
        ts = datetime.fromtimestamp(received_at, tz=tz_sp).strftime("%Y-%m-%d_%H-%M-%S")
        safe_mac = mac.replace(":", "").replace("-", "").upper()
        filename = RAWS_OUTPUT_DIR / f"{ts}_{safe_mac}.cdmp"
        filename.write_bytes(data)
        return str(filename)

    def cleanup(self, older_than: float) -> None:
        # Loop externo aciona periodicamente; remove sessões não concluídas
        with self._lock:
            stale = [
                m
                for m, s in self._sessions.items()
                if (time.time() - s.start_time) > older_than and not s.completed
            ]
            for mac in stale:
                logger.warning("sessao.expirada mac=%s", mac)
                del self._sessions[mac]

    def process_and_register_coredump(
        self, mac: str, coredump_filepath: str, received_at: int
    ) -> None:
        """Executa análise (docker) e registra no banco."""
        logger.info("processamento.inicio mac=%s", mac)
        try:
            device_info = db_manager.get_device(mac)
            if not device_info:
                logger.error("dispositivo.nao_encontrado mac=%s", mac)
                return
            # device_info: (mac, firmware_id, chip_type)
            _, firmware_id, chip_type = device_info
            if not firmware_id:
                logger.error("dispositivo.sem_firmware mac=%s", mac)
                return

            firmware_info = db_manager.get_firmware_by_id(firmware_id)
            if not firmware_info:
                logger.error("firmware.nao_encontrado id=%s mac=%s", firmware_id, mac)
                return
            # firmware_info: (id, name, version, elf_path)
            firmware_elf_path = firmware_info[3]
            log_filepath: Optional[str]
            if not os.path.exists(firmware_elf_path):
                logger.error("elf.inexistente path=%s mac=%s", firmware_elf_path, mac)
                log_filepath = None
            else:
                log_filepath = run_coredump_analysis(
                    coredump_path=coredump_filepath,
                    elf_path=firmware_elf_path,
                    chip_type=chip_type,
                )

            coredump_id = db_manager.add_coredump(
                device_mac=mac,
                firmware_id=firmware_id,
                raw_dump_path=coredump_filepath,
                log_path=log_filepath,
                received_at=received_at,
            )
            if coredump_id:
                logger.info("processamento.registrado mac=%s id=%s", mac, coredump_id)
            else:
                logger.error("processamento.nao_registrado mac=%s", mac)
        except Exception:  # noqa: BLE001
            logger.exception("processamento.excecao_global mac=%s", mac)


_assembler: Optional[CoreDumpAssembler] = None


def get_assembler() -> CoreDumpAssembler:
    """Lazy singleton do assembler para evitar efeitos no import."""
    global _assembler
    if _assembler is None:
        _assembler = CoreDumpAssembler()
    return _assembler


# ------------------------- Interpretação de CoreDumps ---------------------
def run_coredump_analysis(
    coredump_path: str, elf_path: str, chip_type: Optional[str]
) -> Optional[str]:
    """Executa interpretação do coredump se módulo de análise estiver disponível.

    Import dinâmico evita dependência rígida; pode ser desativado via env.
    Retorna caminho do relatório ou None se indisponível/falha.
    """
    if PROCESSING_DISABLED:
        logger.info("processamento.desativado")
        return None
    try:
        from coredump_interpreter import (  # type: ignore
            generate_coredump_report_docker,
            CoreDumpProcessingError,
        )
    except ImportError:
        logger.warning("coredump_interpreter.indisponivel - pulando analise")
        return None
    try:
        report_path = generate_coredump_report_docker(
            coredump_path=coredump_path,
            elf_path=elf_path,
            output_dir=str(REPORTS_OUTPUT_DIR),
            chip_type=chip_type,
            docker_image=DOCKER_IMAGE,
        )
        return str(report_path).replace("\\", "/") if report_path else None
    except CoreDumpProcessingError as e:  # type: ignore[name-defined]
        logger.error("processamento.falha erro=%s", e)
        return None
    except Exception:  # noqa: BLE001
        logger.exception("processamento.excecao_interna")
        return None


def _cleanup_loop() -> None:
    """Loop contínuo (thread daemon) para limpeza de sessões expiradas."""
    while True:  # Mantém processo leve; N pequeno de sessões esperado
        time.sleep(30)
        get_assembler().cleanup(SESSION_TIMEOUT)


# ---------------------------- Utilidades Base64 -----------------------------
BASE64_CHARS = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def maybe_decode_base64(data: bytes) -> Tuple[bytes, bool]:
    """Decodifica Base64 sob heurística simples.

    Retorna (bytes_resultado, True) se decodificado; caso contrário (data, False).
    """
    if not ACCEPT_BASE64 or len(data) < 8:
        return data, False
    if any(c not in BASE64_CHARS for c in data) or len(data) % 4 != 0:
        return data, False
    if b"\x00" in data:  # Evita tratar binário como texto base64
        return data, False
    try:
        decoded = base64.b64decode(data, validate=True)
    except Exception:  # noqa: BLE001
        return data, False
    if not decoded:
        return data, False
    ratio = len(decoded) / len(data)
    if 0.45 <= ratio <= 0.85 or data.endswith(b"="):
        return decoded, True
    return data, False


# ---------------------------- Callbacks MQTT ---------------------------------
def on_connect(
    client: paho.Client,
    userdata: Any,
    flags: Dict[str, Any],
    rc: int,
    properties: Any | None = None,
) -> None:  # Py3.10 compat
    if rc == 0:
        logger.info("mqtt.conectado rc=%s", rc)
        client.subscribe(f"{BASE_TOPIC}/#", qos=2)
        logger.info("mqtt.inscrito topico=%s/#", BASE_TOPIC)
    else:
        logger.error("mqtt.falha_conexao rc=%s", rc)


def on_message(client: paho.Client, userdata: Any, msg: paho.MQTTMessage) -> None:
    topic = msg.topic
    payload = msg.payload
    try:
        segments = topic.split("/")
        if len(segments) < 2 or segments[0] != BASE_TOPIC:
            logger.debug("mqtt.topico_ignorado=%s", topic)
            return
        mac = segments[1]
        # Mensagem inicial
        if len(segments) == 2:
            try:
                meta = json.loads(payload.decode("utf-8"))
                expected = int(meta.get("parts"))
            except Exception as parse_err:  # noqa: BLE001
                logger.error(
                    "json.invalido mac=%s erro=%s payload=%r",
                    mac,
                    parse_err,
                    payload[:100],
                )
                return
            if expected <= 0:
                logger.error("json.parts_invalido mac=%s parts=%s", mac, expected)
                return
            get_assembler().start_session(mac, expected)
            return
        # Fragmento
        if len(segments) == 3:
            try:
                index = int(segments[2])
            except ValueError:
                logger.warning("parte.indice_invalido topic=%s", topic)
                return
            decoded_payload, decoded = maybe_decode_base64(payload)
            if decoded:
                logger.info(
                    "parte.base64_decodificada mac=%s index=%d tam_base64=%d tam_bin=%d",
                    mac,
                    index,
                    len(payload),
                    len(decoded_payload),
                )
            path_final = get_assembler().add_part(mac, index, decoded_payload)
            if path_final:
                logger.info("arquivo.finalizado path=%s", path_final)
            else:
                sess = get_assembler()._sessions.get(
                    mac
                )  # Acesso controlado; somente leitura rápida
                if sess:
                    logger.info(
                        "parte.recebida mac=%s index=%d recebidas=%d total=%d",
                        mac,
                        index,
                        len(sess.parts),
                        sess.expected_parts,
                    )
            return
        logger.debug("mqtt.topico_profundidade_inesperada=%s", topic)
    except Exception:  # noqa: BLE001
        logger.exception("mqtt.erro_processando topico=%s", topic)


# ---------------------------- Inicialização ----------------------------------
def build_client() -> paho.Client:
    """Configura e retorna cliente MQTT."""
    client = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set(tls_version=mqtt.client.ssl.PROTOCOL_TLS)
    return client


def init_runtime() -> None:
    """Inicializa estruturas globais e threads auxiliares."""
    # Thread de limpeza (não bloqueante) mantem sessões enxutas
    threading.Thread(
        target=_cleanup_loop, name="coredump_session_gc", daemon=True
    ).start()


def main() -> None:
    """Ponto de entrada principal."""
    init_runtime()
    client = build_client()
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_forever()


if __name__ == "__main__":  # Execução direta do módulo
    main()
