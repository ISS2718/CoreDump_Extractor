from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from paho import mqtt
import paho.mqtt.client as paho

from ..ports import IDataRepository, ICoreDumpParser, ICoreDumpIngestor


logger = logging.getLogger("backend.components.receiver_mqtt")


MQTT_HOST: str = os.getenv("MQTT_HOST", "d7dc78b4d42d49e8a71a4edfcfb1d6ca.s1.eu.hivemq.cloud")
MQTT_PORT: int = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USER: str = os.getenv("MQTT_USER", "BACKEND-TEST")
MQTT_PASS: str = os.getenv("MQTT_PASS", "1qxe)y~P9U+57C.!")
BASE_TOPIC: str = os.getenv("MQTT_BASE_TOPIC", "coredump")
SESSION_TIMEOUT: int = int(os.getenv("COREDUMP_TIMEOUT_SECONDS", "600"))
RAWS_OUTPUT_DIR: Path = Path(os.getenv("COREDUMP_RAWS_OUTPUT_DIR", "db/coredumps/raws"))
REPORTS_OUTPUT_DIR: Path = Path(os.getenv("COREDUMP_REPORTS_OUTPUT_DIR", "db/coredumps/reports"))
ACCEPT_BASE64: bool = os.getenv("COREDUMP_ACCEPT_BASE64", "1") not in ("0", "false", "False")

RAWS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
REPORTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class CoreDumpSession:
    mac: str
    expected_parts: int
    start_time: float = field(default_factory=time.time)
    parts: Dict[int, bytes] = field(default_factory=dict)
    completed: bool = False

    def add_part(self, index: int, data: bytes) -> None:
        if index < 0 or index >= self.expected_parts + 1:
            logger.warning("parte.fora_intervalo mac=%s index=%s", self.mac, index)
            return
        if index in self.parts:
            return
        self.parts[index] = data

    def is_complete(self) -> bool:
        if self.completed:
            return True
        if len(self.parts) < self.expected_parts:
            return False
        idxs = sorted(self.parts.keys())
        return (
            idxs[0] == 0 and idxs[-1] == self.expected_parts - 1 and len(idxs) == self.expected_parts
        ) or (
            idxs[0] == 1 and idxs[-1] == self.expected_parts and len(idxs) == self.expected_parts
        )

    def assemble(self) -> bytes:
        idxs = sorted(self.parts.keys())
        base = idxs[0]
        ordered = [self.parts[i] for i in range(base, base + self.expected_parts)]
        return b"".join(ordered)


BASE64_CHARS = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")


def maybe_decode_base64(data: bytes) -> Tuple[bytes, bool]:
    if not ACCEPT_BASE64 or len(data) < 8:
        return data, False
    if any(c not in BASE64_CHARS for c in data) or len(data) % 4 != 0:
        return data, False
    if b"\x00" in data:
        return data, False
    try:
        decoded = base64.b64decode(data, validate=True)
    except Exception:
        return data, False
    if not decoded:
        return data, False
    ratio = len(decoded) / len(data)
    if 0.45 <= ratio <= 0.85 or data.endswith(b"="):
        return decoded, True
    return data, False


class _Assembler:
    def __init__(self, repo: IDataRepository, parser: ICoreDumpParser) -> None:
        self.repo = repo
        self.parser = parser
        self._sessions: Dict[str, CoreDumpSession] = {}
        self._lock = threading.Lock()

    def start_session(self, mac: str, expected_parts: int) -> bool:
        """Inicia nova sessão de coredump. Retorna True se criou, False se já existe sessão ativa."""
        with self._lock:
            existing = self._sessions.get(mac)
            if existing and not existing.completed:
                logger.warning("sessao_ja_existe mac=%s expected_parts=%s ignorando", mac, expected_parts)
                return False
            self._sessions[mac] = CoreDumpSession(mac=mac, expected_parts=expected_parts)
            return True

    def add_part(self, mac: str, index: int, data: bytes) -> Optional[str]:
        with self._lock:
            sess = self._sessions.get(mac)
            if not sess:
                return None
            # Verificar se já está completado antes de processar (evita duplicação)
            if sess.completed:
                return None
            sess.add_part(index, data)
            if not sess.is_complete():
                return None
            # Marcar como completado ANTES de iniciar processamento assíncrono
            # Isso evita que múltiplas threads processem o mesmo coredump
            sess.completed = True
            blob = sess.assemble()
            received_at = int(time.time())
            filepath = self._write_coredump(mac, blob, received_at)
            threading.Thread(
                target=self._process_and_register,
                args=(mac, filepath, received_at),
                daemon=True,
            ).start()
            return filepath

    def cleanup(self, older_than: float) -> None:
        with self._lock:
            stale = [m for m, s in self._sessions.items() if (time.time() - s.start_time) > older_than and not s.completed]
            for mac in stale:
                del self._sessions[mac]

    def _write_coredump(self, mac: str, data: bytes, received_at: int) -> str:
        try:
            from zoneinfo import ZoneInfo  # Python 3.9+
            tz_sp = ZoneInfo("America/Sao_Paulo")
        except Exception:
            tz_sp = timezone(timedelta(hours=-3))
        ts = datetime.fromtimestamp(received_at, tz=tz_sp).strftime("%Y-%m-%d_%H-%M-%S")
        safe_mac = mac.replace(":", "").replace("-", "").upper()
        filename = RAWS_OUTPUT_DIR / f"{ts}_{safe_mac}.cdmp"
        filename.write_bytes(data)
        return str(filename)

    def _process_and_register(self, mac: str, coredump_filepath: str, received_at: int) -> None:
        try:
            device_info = self.repo.get_device(mac)
            if not device_info:
                logger.error("dispositivo.nao_encontrado mac=%s", mac)
                return
            _, firmware_id, chip_type = device_info
            if not firmware_id:
                logger.error("dispositivo.sem_firmware mac=%s", mac)
                return
            fw = self.repo.get_firmware_by_id(int(firmware_id))
            if not fw:
                logger.error("firmware.nao_encontrado id=%s mac=%s", firmware_id, mac)
                return
            elf_path = Path(str(fw[3]))

            # Primeiro, registra o coredump bruto
            coredump_id = self.repo.save_coredump_raw(
                mac=mac,
                firmware_id=int(firmware_id),
                raw_path=Path(coredump_filepath),
                received_at=received_at,
            )

            # Depois, se possível, gera relatório e atualiza o registro
            if elf_path.exists():
                report = self.parser.generate_report(
                    raw_path=Path(coredump_filepath),
                    elf_path=elf_path,
                    out_dir=REPORTS_OUTPUT_DIR,
                    chip_type=fw[2] if len(fw) > 2 else None,
                )
                self.repo.save_coredump_report(coredump_id=coredump_id, report_path=report)
            else:
                logger.error("elf.inexistente path=%s mac=%s", elf_path, mac)
        except Exception:
            logger.exception("receiver.processamento_excecao mac=%s", mac)


class MqttReceiver(ICoreDumpIngestor):
    def __init__(self, repo: IDataRepository, parser: ICoreDumpParser) -> None:
        self.repo = repo
        self.parser = parser
        self.assembler = _Assembler(repo, parser)
        self.client: Optional[paho.Client] = None

    def start(self) -> None:
        if self.client is not None:
            return
        self._start_cleanup_thread()
        client = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2)
        client.on_connect = self._on_connect
        client.on_message = self._on_message
        client.username_pw_set(MQTT_USER, MQTT_PASS)
        client.tls_set(tls_version=mqtt.client.ssl.PROTOCOL_TLS)
        client.connect(MQTT_HOST, MQTT_PORT, 60)
        self.client = client
        client.loop_start()

    def stop(self) -> None:
        if self.client is None:
            return
        try:
            self.client.loop_stop()
            self.client.disconnect()
        finally:
            self.client = None

    def _start_cleanup_thread(self) -> None:
        threading.Thread(target=self._cleanup_loop, name="coredump_session_gc", daemon=True).start()

    def _cleanup_loop(self) -> None:
        while True:
            time.sleep(30)
            self.assembler.cleanup(SESSION_TIMEOUT)

    # MQTT callbacks
    def _on_connect(self, client: paho.Client, userdata: Any, flags: Dict[str, Any], rc: int, properties: Any | None = None) -> None:
        if rc == 0:
            logger.info("mqtt.conectado rc=%s", rc)
            client.subscribe(f"{BASE_TOPIC}/#", qos=2)
        else:
            logger.error("mqtt.falha_conexao rc=%s", rc)

    def _on_message(self, client: paho.Client, userdata: Any, msg: paho.MQTTMessage) -> None:
        try:
            topic = msg.topic
            payload = msg.payload
            seg = topic.split("/")
            if len(seg) < 2 or seg[0] != BASE_TOPIC:
                return
            mac = seg[1]
            if len(seg) == 2:
                meta = json.loads(payload.decode("utf-8"))
                expected = int(meta.get("parts"))
                if expected > 0:
                    created = self.assembler.start_session(mac, expected)
                    if not created:
                        logger.warning("mensagem_meta_duplicada mac=%s expected_parts=%s ignorada", mac, expected)
                return
            if len(seg) == 3:
                try:
                    index = int(seg[2])
                except ValueError:
                    return
                decoded_payload, _ = maybe_decode_base64(payload)
                self.assembler.add_part(mac, index, decoded_payload)
        except Exception:
            logger.exception("mqtt.on_message_excecao")


__all__ = ["MqttReceiver"]


