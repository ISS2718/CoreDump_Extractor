"""coredump_interpreter.py

Processa um arquivo de coredump bruto (formato raw) usando a ferramenta esp-coredump
executada dentro de um contêiner Docker da Espressif e gera um relatório texto limpo.

Fluxo resumido:
1. Valida caminhos de entrada (coredump, ELF e diretório de saída).
2. Monta arquivos dentro do container e executa esp-coredump.
3. Extrai bloco entre marcadores pré-definidos; se ausentes, usa saída completa.
4. Salva relatório .txt e retorna o caminho.

Variáveis de ambiente relevantes:
  COREDUMP_DOCKER_IMAGE  -> sobrescreve a imagem Docker padrão.
  COREDUMP_DOCKER_TIMEOUT -> timeout (segundos) da execução do docker run.

Formato esperado do coredump: arquivo raw (.cdmp) exportado pelo ESP-IDF.
Formato esperado do ELF: firmware compilado correspondente ao coredump.

TODO: adicionar validação opcional de versão da imagem vs versão do firmware.
TODO: permitir extração incremental (stream) para coredumps muito grandes.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional, Union, Sequence, List

# ---------------------------------------------------------------------------
# Constantes configuráveis (podem ser sobrescritas por variáveis de ambiente)
# ---------------------------------------------------------------------------
DEFAULT_DOCKER_IMAGE: str = "espressif/idf:v5.5.1"
DEFAULT_TIMEOUT_SECONDS: int = 120
START_MARKER: str = "==================== ESP32 CORE DUMP START ===================="
END_MARKER: str = "===================== ESP32 CORE DUMP END ====================="

ENV_DOCKER_IMAGE: str = os.getenv("COREDUMP_DOCKER_IMAGE", DEFAULT_DOCKER_IMAGE)
ENV_TIMEOUT: int = int(os.getenv("COREDUMP_DOCKER_TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS)))

logger = logging.getLogger("backend.coredump_interpreter")


class CoreDumpProcessingError(Exception):
    """Erro durante o processamento do coredump (execução Docker ou parsing)."""


def _build_docker_command(
    coredump_file: Path,
    elf_file: Path,
    docker_image: str,
    chip_type: Optional[str],
    start_marker: str = START_MARKER,
    end_marker: str = END_MARKER,
) -> List[str]:
    """Monta a lista de argumentos para execução do Docker.

    Retorna a lista final (argv) para subprocess.run.
    """
    container_core_filename = coredump_file.name
    container_elf_filename = elf_file.name

    command_parts: List[str] = [
        "esp-coredump", "info_corefile",
        "--core-format", "raw",
        "--core", container_core_filename,
    ]
    if chip_type:
        # Caminho interno depende de $IDF_PATH no container.
        rom_elf_path_in_container = f"$IDF_PATH/components/esp_rom/rom_elfs/{chip_type}.elf"
        command_parts.extend(["--rom-elf", rom_elf_path_in_container])
        logger.debug("Adicionando ROM ELF para chip=%s (%s)", chip_type, rom_elf_path_in_container)

    # ELF do programa como argumento posicional final.
    command_parts.append(container_elf_filename)

    # Usamos bash -c para manter compatibilidade com expansão de $IDF_PATH.
    command_to_run_inside = " ".join(command_parts)

    docker_cmd: List[str] = [
        "docker", "run",
        "--rm",
        "-w", "/app",
        "-v", f"{coredump_file}:/app/{container_core_filename}:ro",
        "-v", f"{elf_file}:/app/{container_elf_filename}:ro",
        docker_image,
        "bash", "-c", command_to_run_inside,
    ]
    logger.debug("Comando Docker construído: %s", " ".join(docker_cmd))
    return docker_cmd


def _extract_report(stdout: str, start_marker: str, end_marker: str) -> str:
    """Extrai bloco entre marcadores; se não achar, retorna stdout inteiro.

    Mantém comportamento tolerante. TODO: oferecer modo estrito opcional.
    """
    try:
        start_index = stdout.index(start_marker)
        end_index = stdout.index(end_marker, start_index)
        content_start_index = start_index + len(start_marker)
        raw_content = stdout[content_start_index:end_index]
        cleaned = raw_content.strip()
        logger.debug("Marcadores encontrados; relatório limpo (%d chars).", len(cleaned))
        return cleaned
    except ValueError:
        logger.warning(
            "Marcadores de início/fim não encontrados; usando saída completa (%d chars).",
            len(stdout),
        )
        return stdout


def generate_coredump_report_docker(
    coredump_path: Union[str, Path],
    elf_path: Union[str, Path],
    output_dir: Union[str, Path],
    docker_image: str | None = None,
    chip_type: Optional[str] = None,
    timeout_seconds: int | None = None,
    start_marker: str = START_MARKER,
    end_marker: str = END_MARKER,
) -> Path:
    """Gera relatório de coredump a partir de arquivo raw e ELF.

    Parâmetros:
      coredump_path: caminho para arquivo .cdmp bruto.
      elf_path: firmware ELF correspondente ao coredump.
      output_dir: diretório onde o relatório .txt será escrito.
      docker_image: imagem Docker (override; default via env ou constante).
      chip_type: ex: 'esp32'; acrescenta ROM ELF se fornecido.
      timeout_seconds: override do timeout (segundos).
      start_marker/end_marker: delimitadores do bloco de interesse.
    Retorna: Path do arquivo de relatório gerado.
    Lança: FileNotFoundError, CoreDumpProcessingError.
    """
    coredump_file = Path(coredump_path).resolve()
    elf_file = Path(elf_path).resolve()
    output_path = Path(output_dir).resolve()

    if not coredump_file.is_file():  # valida arquivo de coredump
        raise FileNotFoundError(f"Arquivo de coredump não encontrado: {coredump_file}")
    if not elf_file.is_file():  # valida ELF
        raise FileNotFoundError(f"Arquivo ELF não encontrado: {elf_file}")
    if not output_path.is_dir():  # valida diretório de saída
        raise FileNotFoundError(f"Diretório de destino inexistente: {output_path}")

    image_to_use = docker_image or ENV_DOCKER_IMAGE
    effective_timeout = timeout_seconds or ENV_TIMEOUT
    output_report_path = output_path / f"{coredump_file.stem}.txt"

    logger.info(
        "Iniciando processamento do coredump name=%s image=%s timeout=%ss",
        coredump_file.name, image_to_use, effective_timeout,
    )

    cmd = _build_docker_command(
        coredump_file=coredump_file,
        elf_file=elf_file,
        docker_image=image_to_use,
        chip_type=chip_type,
        start_marker=start_marker,
        end_marker=end_marker,
    )

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
            timeout=effective_timeout,
        )
        logger.debug("Execução Docker concluída: returncode=%d", result.returncode)
        report_content = result.stdout

        clean_report = _extract_report(report_content, start_marker, end_marker)
        output_report_path.write_text(clean_report, encoding="utf-8")
        logger.info("Relatório salvo em %s (%d chars)", output_report_path, len(clean_report))
        return output_report_path

    except subprocess.CalledProcessError as e:  # erro no esp-coredump
        logger.error(
            "Comando Docker falhou returncode=%d stderr_size=%d", e.returncode, len(e.stderr or ""),
        )
        raise CoreDumpProcessingError(
            f"Falha ao executar esp-coredump (exit={e.returncode})\n{e.stderr}"
        ) from e
    except FileNotFoundError as e:  # docker não instalado
        logger.exception("'docker' não encontrado no PATH.")
        raise CoreDumpProcessingError(
            "Comando 'docker' não encontrado. Docker está instalado e no PATH do sistema?"
        ) from e
    except subprocess.TimeoutExpired as e:  # tempo excedido
        logger.error(
            "Timeout executando docker (limite=%ss). stdout=%d stderr=%d",
            effective_timeout,
            len(e.stdout or ""),
            len(e.stderr or ""),
        )
        raise CoreDumpProcessingError(
            "Tempo limite excedido ao processar coredump (timeout). Verifique o estado do Docker."
        ) from e
    except Exception as e:  # salvaguarda para erros inesperados
        logger.exception("Erro inesperado durante processamento do coredump.")
        raise CoreDumpProcessingError("Erro inesperado ao processar coredump.") from e


def main() -> int:
    """Função principal para execução direta via CLI (uso manual/teste)."""
    # Caminhos de teste padrão (ajuste conforme necessidade local)
    firmware_elf_path = Path("db/firmwares/CoreDump_Extractor.elf")
    coredump_raw_path = Path("db/coredumps/raws/2025-09-29_20-48-40_160320252207.cdmp")
    reports_output_dir = Path("db/coredumps/reports")
    docker_image_tag = "espressif/idf:v5.5.1"  # versão específica para consistência

    # Garante estrutura de diretórios (não cria arquivos)
    for p in (firmware_elf_path.parent, coredump_raw_path.parent, reports_output_dir):
        p.mkdir(parents=True, exist_ok=True)
    logger.debug("Estrutura de diretórios verificada.")

    try:
        report_path = generate_coredump_report_docker(
            coredump_path=coredump_raw_path,
            elf_path=firmware_elf_path,
            output_dir=reports_output_dir,
            docker_image=docker_image_tag,
            chip_type="esp32",
        )
        logger.info("Sucesso: relatório salvo em %s", report_path)
        return 0
    except (FileNotFoundError, CoreDumpProcessingError) as e:
        logger.error("Erro durante processamento: %s", e)
        logger.info("Dicas: validar existência de ELF, coredump, status do Docker, imagem baixada.")
        return 1


if __name__ == "__main__":  # efeito colateral isolado aqui
    # Configuração básica de logging apenas quando executado diretamente.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    raise SystemExit(main())