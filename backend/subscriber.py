"""Subscriber MQTT para reconstrução de CoreDumps particionados.

Estrutura de tópicos esperada:
  coredump/<MAC> -> mensagem JSON inicial: {"parts": N}
  coredump/<MAC>/<INDEX> -> cada parte (payload binário ou texto)

Comportamento:
  1. Ao receber a mensagem raiz com {"parts":N} cria sessão.
  2. Armazena partes conforme chegam. Aceita índices começando em 0 ou 1.
  3. Ao completar todas as partes, monta e grava arquivo em 'coredumps/'.
  4. Limpa sessões expiradas (timeout configurável).

Variáveis de ambiente suportadas (opcionais):
    MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASS, MQTT_BASE_TOPIC (default 'coredump')
    COREDUMP_TIMEOUT_SECONDS (default 600), COREDUMP_RAWS_OUTPUT_DIR (default 'coredumps')
    COREDUMP_ACCEPT_BASE64 (default '1'): se habilitado, tenta decodificar partes que aparentem estar em Base64.

Observação: Credenciais estão hardcoded abaixo por simplicidade; recomenda-se mover para env.
"""

from __future__ import annotations

import os
import json
import threading
import time
import logging
import base64
import subprocess
from dataclasses import dataclass, field
from typing import Dict, Optional
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import paho.mqtt.client as paho
from paho import mqtt

try:
    import db_manager
except ImportError:
    logging.error("Erro: db_manager.py não encontrado. Certifique-se de que ele está no mesmo diretório ou no PYTHONPATH.")
    exit(1)

try:
    from coredump_interpreter import generate_coredump_report_docker, CoreDumpProcessingError
except ImportError:
    logging.error("Erro: coredump_interpreter.py não encontrado. Certifique-se de que ele está no mesmo diretório ou no PYTHONPATH.")
    exit(1)

# ---------------------------- Config / Constantes ----------------------------
MQTT_HOST = os.getenv("MQTT_HOST", "d7dc78b4d42d49e8a71a4edfcfb1d6ca.s1.eu.hivemq.cloud")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USER = os.getenv("MQTT_USER", "BACKEND-TEST")
MQTT_PASS = os.getenv("MQTT_PASS", "1qxe)y~P9U+57C.!")
BASE_TOPIC = os.getenv("MQTT_BASE_TOPIC", "coredump")
SESSION_TIMEOUT = int(os.getenv("COREDUMP_TIMEOUT_SECONDS", "600"))
RAWS_OUTPUT_DIR = os.getenv("COREDUMP_RAWS_OUTPUT_DIR", "db/coredumps/raws")
REPORTS_OUTPUT_DIR = os.getenv("COREDUMP_REPORTS_OUTPUT_DIR", "db/coredumps/reports")
ACCEPT_BASE64 = os.getenv("COREDUMP_ACCEPT_BASE64", "1") not in ("0", "false", "False")

os.makedirs(RAWS_OUTPUT_DIR, exist_ok=True)
os.makedirs(REPORTS_OUTPUT_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ---------------------------- Estruturas de Dados ---------------------------
@dataclass
class CoreDumpSession:
    mac: str
    expected_parts: int
    start_time: float = field(default_factory=time.time)
    parts: Dict[int, bytes] = field(default_factory=dict)
    completed: bool = False

    def add_part(self, index: int, data: bytes):
        if index < 0 or index >= self.expected_parts + 1:  # tolerância p/ 1-based
            logging.warning("Parte fora do intervalo: mac=%s index=%s", self.mac, index)
            return
        if index in self.parts:
            logging.debug("Parte duplicada ignorada: mac=%s index=%s", self.mac, index)
            return
        self.parts[index] = data

    def is_complete(self) -> bool:
        if self.completed:
            return True
        # Suportar indices 0..N-1 ou 1..N
        if len(self.parts) < self.expected_parts:
            return False
        indices = sorted(self.parts.keys())
        if indices[0] == 0 and indices[-1] == self.expected_parts - 1 and len(indices) == self.expected_parts:
            return True
        if indices[0] == 1 and indices[-1] == self.expected_parts and len(indices) == self.expected_parts:
            return True
        return False

    def assemble(self) -> bytes:
        indices = sorted(self.parts.keys())
        # Detectar base (0 ou 1)
        base = indices[0]
        ordered = [self.parts[i] for i in range(base, base + self.expected_parts)]
        return b"".join(ordered)


class CoreDumpAssembler:
    def __init__(self):
        self._sessions: Dict[str, CoreDumpSession] = {}
        self._lock = threading.Lock()

    def start_session(self, mac: str, expected_parts: int):
        with self._lock:
            sess = self._sessions.get(mac)
            if sess and not sess.completed:
                logging.info("Reiniciando sessão existente para %s", mac)
            self._sessions[mac] = CoreDumpSession(mac=mac, expected_parts=expected_parts)
            logging.info("Sessão iniciada: mac=%s parts=%d", mac, expected_parts)

    def add_part(self, mac: str, index: int, data: bytes):
        with self._lock:
            sess = self._sessions.get(mac)
            if not sess:
                logging.warning("Parte recebida sem sessão inicial mac=%s index=%s", mac, index)
                return None
            sess.add_part(index, data)
            if sess.is_complete():
                blob = sess.assemble()
                received_at = datetime.now().timestamp()
                filename = self._write_coredump(mac, blob, received_at)
                sess.completed = True
                logging.info("CoreDump completo mac=%s salvo em %s (%d bytes)", mac, filename, len(blob))
                
                processing_thread = threading.Thread(
                    target=self.process_and_register_coredump,
                    args=(mac, filename, received_at)
                )
                processing_thread.start()

                return filename
        return None

    def _write_coredump(self, mac: str, data: bytes, received_at: int) -> str:
        try:
            tz_sp = ZoneInfo("America/Sao_Paulo")
        except Exception:
            tz_sp = timezone(timedelta(hours=-3))

        ts = datetime.fromtimestamp(received_at, tz=tz_sp).strftime("%Y-%m-%d_%H-%M-%S")
        safe_mac = mac.replace(":", "").replace("-", "").upper()
        filename = os.path.join(RAWS_OUTPUT_DIR, f"{ts}_{safe_mac}.cdmp")
        with open(filename, "wb") as f:
            f.write(data)
        return filename

    def cleanup(self, older_than: float):
        with self._lock:
            to_delete = [mac for mac, s in self._sessions.items() if (time.time() - s.start_time) > older_than and not s.completed]
            for mac in to_delete:
                logging.warning("Removendo sessão expirada mac=%s", mac)
                del self._sessions[mac]
    
    def process_and_register_coredump(self, mac: str, coredump_filepath: str, received_at: int):
        """
        Processa um coredump recém-montado: analisa com esptool e registra no BD.
        Esta função é projetada para rodar em uma thread separada para não bloquear o MQTT.
        """
        logging.info(f"Iniciando processamento do coredump para o MAC: {mac}")

        # 1. Obter informações do dispositivo do banco de dados
        device_info = db_manager.get_device(mac) # A tupla é (mac, current_firmware_id, chip_type)
        if not device_info:
            logging.error(f"Dispositivo com MAC {mac} não encontrado. O coredump não será registrado.")
            return

        firmware_id = device_info[1] # Obter o ID do firmware atual
        if not firmware_id:
            logging.error(f"Dispositivo {mac} não tem um firmware associado. Coredump não será registrado.")
            return

        chip_type = device_info[2]  # Obter o tipo de chip

        # 2. Obter o caminho do arquivo ELF do firmware a partir do seu ID
        firmware_info = db_manager.get_firmware_by_id(firmware_id)
        if not firmware_info:
            logging.error(f"Firmware com ID {firmware_id} não encontrado. Coredump não será registrado.")
            return

        # A tupla é (id, name, version, elf_path)
        firmware_elf_path = firmware_info[3]
        if not os.path.exists(firmware_elf_path):
            logging.error(f"Arquivo ELF do firmware não encontrado em: {firmware_elf_path}. Análise abortada.")
            # Mesmo sem análise, vamos registrar o coredump bruto
            log_filepath = None
        else:
            log_filepath = generate_coredump_report_docker(
                coredump_path=coredump_filepath,
                elf_path=firmware_elf_path,
                output_dir=REPORTS_OUTPUT_DIR,
                chip_type=chip_type,
                docker_image="espressif/idf:v5.5.1"
            )

        # 3. Converte o log_filepath para string
        log_filepath = str(log_filepath).replace("\\", "/") if log_filepath else None

        # 4. Adicionar a entrada final no banco de dados
        coredump_id = db_manager.add_coredump(
            device_mac=mac,
            firmware_id=firmware_id,
            raw_dump_path=coredump_filepath,
            log_path=log_filepath,
            received_at=received_at
        )

        if coredump_id:
            logging.info(f"Coredump para MAC {mac} registrado no banco com ID: {coredump_id}")
        else:
            logging.error(f"Falha ao registrar o coredump para MAC {mac} no banco de dados.")

assembler = CoreDumpAssembler()

def _cleanup_loop():
    while True:
        time.sleep(30)
        assembler.cleanup(SESSION_TIMEOUT)

cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True)
cleanup_thread.start()

# ---------------------------- Utilidades Base64 -----------------------------
BASE64_CHARS = set(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')

def maybe_decode_base64(data: bytes) -> tuple[bytes, bool]:
    """Tenta decodificar `data` como base64 se heurísticas indicarem probabilidade alta.

    Heurísticas:
      - tamanho mínimo (>= 8)
      - todos bytes no conjunto base64
      - len % 4 == 0
      - após decodificação: resultado não vazio e comprimido? (apenas checa tamanho)
      - razão tamanho_decod / tamanho_original entre 0.5 e 0.8 (aprox) ou padding '=' presente

    Retorna (payload_decodificado_ou_original, houve_decodificacao).
    Falhas silenciosas retornam (data, False).
    """
    if not ACCEPT_BASE64:
        return data, False
    if len(data) < 8:
        return data, False
    if any(c not in BASE64_CHARS for c in data):
        return data, False
    if len(data) % 4 != 0:
        return data, False
    # Evitar decodificar se parece já binário (heurística simples: presença de bytes nulos na sequência original)
    if b'\x00' in data:
        return data, False
    try:
        decoded = base64.b64decode(data, validate=True)
    except Exception:
        return data, False
    if not decoded:
        return data, False
    # Ratio heurística: base64 adiciona ~33% overhead => dec/enc ~0.75
    ratio = len(decoded) / len(data)
    if 0.45 <= ratio <= 0.85 or data.endswith(b'='):
        return decoded, True
    return data, False

# ---------------------------- Callbacks MQTT ---------------------------------
def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        logging.info("Conectado ao broker (rc=%s)", rc)
        client.subscribe(f"{BASE_TOPIC}/#", qos=2)
        logging.info("Inscrito em %s/#", BASE_TOPIC)
    else:
        logging.error("Falha na conexão rc=%s", rc)

def on_message(client, userdata, msg):
    topic = msg.topic  # exemplo: coredump/AA:BB:CC:DD:EE:FF ou coredump/AA.../3
    payload = msg.payload
    try:
        parts = topic.split('/')
        if len(parts) < 2 or parts[0] != BASE_TOPIC:
            logging.debug("Tópico ignorado: %s", topic)
            return
        mac = parts[1]
        # Mensagem raiz: BASE_TOPIC/<MAC>
        if len(parts) == 2:
            # Espera JSON {"parts": N}
            try:
                meta = json.loads(payload.decode('utf-8'))
                expected = int(meta.get('parts'))
            except Exception as e:
                logging.error("JSON inicial inválido mac=%s erro=%s payload=%r", mac, e, payload[:100])
                return
            if expected <= 0:
                logging.error("Valor 'parts' inválido mac=%s parts=%s", mac, expected)
                return
            assembler.start_session(mac, expected)
            return
        # Parte: BASE_TOPIC/<MAC>/<INDEX>
        if len(parts) == 3:
            try:
                index = int(parts[2])
            except ValueError:
                logging.warning("Índice de parte inválido topic=%s", topic)
                return
            decoded_payload, decoded = maybe_decode_base64(payload)
            if decoded:
                logging.info("Parte decodificada de base64: mac=%s index=%d tamanho_base64=%d tamanho_bin=%d", mac, index, len(payload), len(decoded_payload))
            filename = assembler.add_part(mac, index, decoded_payload)
            if filename:
                logging.info("Arquivo finalizado: %s", filename)
            else:
                sess = assembler._sessions.get(mac)
                if sess:
                    logging.info("Recebida parte %d/%d mac=%s (total recebidas=%d)", index, sess.expected_parts, mac, len(sess.parts))
            return
        logging.debug("Tópico com profundidade inesperada: %s", topic)
    except Exception as e:
        logging.exception("Erro processando mensagem topic=%s erro=%s", topic, e)

# ---------------------------- Inicialização ----------------------------------
def build_client() -> paho.Client:
    client = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set(tls_version=mqtt.client.ssl.PROTOCOL_TLS)
    return client

if __name__ == "__main__":
    client = build_client()
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_forever()
