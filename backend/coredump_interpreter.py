import subprocess
import os
from pathlib import Path
from typing import Union

class CoreDumpProcessingError(Exception):
    """Exceção personalizada para erros durante o processamento do coredump."""
    pass

def generate_coredump_report_docker(
    coredump_path: Union[str, Path],
    elf_path: Union[str, Path],
    output_dir: Union[str, Path],
    docker_image: str = "espressif/idf:v5.1.2",
    chip_type: str = None
) -> Path:
    coredump_file = Path(coredump_path).resolve()
    elf_file = Path(elf_path).resolve()
    output_path = Path(output_dir).resolve()

    if not coredump_file.is_file():
        raise FileNotFoundError(f"Arquivo de coredump não encontrado em: {coredump_file}")
    if not elf_file.is_file():
        raise FileNotFoundError(f"Arquivo ELF não encontrado em: {elf_file}")
    if not output_path.is_dir():
        raise FileNotFoundError(f"Diretório de destino não existe: {output_path}")

    output_report_path = output_path / f"{coredump_file.stem}.txt"
    
    container_core_filename = coredump_file.name
    container_elf_filename = elf_file.name

    command_parts = [
        "esp-coredump", "info_corefile",
        "--core-format", "raw",
        "--core", container_core_filename
    ]

    if chip_type:
        # Se um tipo de chip foi fornecido, adicionamos a flag --rom-elf.
        # O caminho é construído usando a variável $IDF_PATH que existe DENTRO do contêiner.
        rom_elf_path_in_container = f"$IDF_PATH/components/esp_rom/rom_elfs/{chip_type}.elf"
        command_parts.extend(["--rom-elf", rom_elf_path_in_container])
        print(f"Adicionando ROM ELF para o chip '{chip_type}' ao comando.")

    # Adiciona o argumento posicional do ELF do programa no final
    command_parts.append(container_elf_filename)

    # Junta todas as partes em uma única string de comando
    command_to_run_inside = " ".join(command_parts)
    
    command = [
        "docker", "run",
        "--rm",
        "-w", "/app",
        "-v", f"{coredump_file}:/app/{container_core_filename}:ro",
        "-v", f"{elf_file}:/app/{container_elf_filename}:ro",
        docker_image,
        "bash", "-c", command_to_run_inside
    ]

    print(f"Executando comando Docker: {' '.join(command)}")

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            encoding='utf-8',
            timeout=120
        )
        
        report_content = result.stdout
        
        start_marker = "==================== ESP32 CORE DUMP START ===================="
        end_marker = "===================== ESP32 CORE DUMP END ====================="
        
        try:
            start_index = report_content.index(start_marker)
            end_index = report_content.index(end_marker, start_index)
            content_start_index = start_index + len(start_marker)
            raw_content = report_content[content_start_index:end_index]
            clean_report = raw_content.strip()

            print("Conteúdo do relatório extraído e limpo com sucesso.")

        except ValueError:
            print("AVISO: Marcadores de início e/ou fim não encontrados. Salvando a saída completa.")
            clean_report = report_content

        print(f"Salvando relatório limpo em: {output_report_path}")
        with open(output_report_path, "w", encoding="utf-8") as f:
            f.write(clean_report)
            
        return output_report_path

    except subprocess.CalledProcessError as e:
        error_message = (
            f"O comando Docker falhou com o código de saída {e.returncode}.\n"
            f"Erro:\n{e.stderr}"
        )
        raise CoreDumpProcessingError(error_message)
    except FileNotFoundError:
        raise CoreDumpProcessingError(
            "Comando 'docker' não encontrado. Docker está instalado e no PATH do sistema?"
        )
    except subprocess.TimeoutExpired:
         raise CoreDumpProcessingError(
            "O processo Docker demorou demais para responder (timeout). "
            "Verifique se o Docker Desktop não está travado."
        )

# --- Bloco Principal de Execução ---
if __name__ == "__main__":
    # --- 1. Configuração dos Caminhos ---
    # Altere aqui se necessário
    FIRMWARE_ELF_PATH = Path("db/firmwares/CoreDump_Extractor.elf")
    COREDUMP_RAW_PATH = Path("db/coredumps/raws/2025-09-29_10-07-04_160320252207.cdmp")
    REPORTS_OUTPUT_DIR = Path("db/coredumps/reports")
    DOCKER_IMAGE_TAG = "espressif/idf:v5.1.2" # Use uma versão específica para consistência

    # --- 2. Preparação do Ambiente ---
    # Este bloco garante que as pastas existem.
    print("--- Preparando ambiente de teste ---")
    FIRMWARE_ELF_PATH.parent.mkdir(parents=True, exist_ok=True)
    COREDUMP_RAW_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Estrutura de pastas verificada.")
    print("-" * 20)
    
    # --- 3. Execução do Processamento ---
    try:
        print(f"Iniciando processamento do coredump: {COREDUMP_RAW_PATH.name}")
        
        # Chamada da função principal da nossa biblioteca
        generated_report = generate_coredump_report_docker(
            coredump_path=COREDUMP_RAW_PATH,
            elf_path=FIRMWARE_ELF_PATH,
            output_dir=REPORTS_OUTPUT_DIR,
            docker_image=DOCKER_IMAGE_TAG,
            chip_type="esp32"
        )
        print(f"\n✅ Sucesso! O relatório foi salvo em: {generated_report}")

    except (FileNotFoundError, CoreDumpProcessingError) as e:
        print(f"\n❌ Erro durante o processamento com Docker: {e}")
        print("\n--- Dicas para solução de problemas ---")
        print(f"1. Verifique se o arquivo ELF realmente existe em: '{FIRMWARE_ELF_PATH.resolve()}'")
        print(f"2. Verifique se o arquivo de coredump realmente existe em: '{COREDUMP_RAW_PATH.resolve()}'")
        print("3. Certifique-se de que o Docker Desktop está em execução.")
        print(f"4. Verifique se a imagem Docker '{DOCKER_IMAGE_TAG}' foi baixada (use o comando 'docker images').")
        print("-" * 39)