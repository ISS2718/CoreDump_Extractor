import re
import os
import sys
import hashlib

def preprocess_content(content):
    """
    Limpa o conteúdo do coredump para facilitar o parse.
    Remove as tags e linhas em branco.
    """
    content_no_source = re.sub(r"", "", content)
    
    # Processa as linhas para remover espaços extras e linhas em branco
    processed_lines = []
    for line in content_no_source.splitlines():
        line = line.strip()
        if line:
            processed_lines.append(line)
            
    return "\n".join(processed_lines)

def extract_register_block(content):
    """
    Extrai o bloco de texto exato de 'CURRENT THREAD REGISTERS'.
    """
    try:
        # Encontra o início e o fim do bloco
        start_marker = "================== CURRENT THREAD REGISTERS ==================="
        end_marker = "==================== CURRENT THREAD STACK ====================="
        
        start_index = content.find(start_marker)
        if start_index == -1:
            return None
            
        end_index = content.find(end_marker, start_index)
        if end_index == -1:
            return None
        
        # Extrai o bloco, excluindo a linha de título
        block = content[start_index + len(start_marker) : end_index]
        return block.strip()
        
    except Exception:
        return None

def generate_cluster_name(filepath):
    """
    Gera um nome de cluster usando a "assinatura base" + um hash do 
    bloco de registradores.
    """
    
    # Padrões para a "assinatura base"
    base_patterns = {
        "exception": re.compile(r"exccause\s+0x[0-9a-fA-F]+\s+\(([^)]+)\)"),
        "stack_frame_0": re.compile(r"^#0\s+.*?in\s+([^(]+)\s+\(.*\)\s+at\s+(.+):(\d+)", re.MULTILINE)
    }
    
    found_data = {
        "exception": None,
        "function": None,
        "file": None,
        "line": None
    }

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            original_content = f.read()
            
        # ETAPA 1: Pré-processar o conteúdo
        content = preprocess_content(original_content)
        
        # ETAPA 2: Extrair a "assinatura base"
        exc_match = base_patterns["exception"].search(content)
        if exc_match:
            found_data["exception"] = exc_match.group(1)
            
        stack_match = base_patterns["stack_frame_0"].search(content)
        if stack_match:
            found_data["function"] = stack_match.group(1).strip()
            full_path = stack_match.group(2).strip()
            found_data["file"] = os.path.basename(full_path) 
            found_data["line"] = stack_match.group(3).strip()
        
        # ETAPA 3: Extrair o bloco de registradores e calcular o hash
        reg_block = extract_register_block(content)
        
        if not reg_block:
            return "Error_CouldNotParseRegisterBlock"
            
        # Calcula o hash SHA1 do bloco de texto dos registradores
        sha1 = hashlib.sha1()
        sha1.update(reg_block.encode('utf-8'))
        reg_hash = sha1.hexdigest()
        # Usamos os primeiros 10 caracteres para manter o nome mais curto
        short_hash = reg_hash[:10] 

        # --- Geração do Nome do Cluster ---
        
        if all(found_data.values()):
            base_name = (
                f"{found_data['exception']}_"
                f"{found_data['function']}_"
                f"{found_data['file']}_"
                f"{found_data['line']}"
            )
            
            # Formato: [AssinaturaBase]_REGS-HASH-[Hash]
            cluster_name = f"{base_name}_REGS-HASH-{short_hash}"
            return cluster_name
        else:
            return "Error_CouldNotParseSignature"

    except FileNotFoundError:
        return f"Error_FileNotFound: {filepath}"
    except Exception as e:
        return f"Error_General: {e}"

# --- Como usar o script ---
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python name_coredump_hash.py <caminho_para_o_arquivo_coredump.txt>")
        sys.exit(1)
        
    coredump_file = sys.argv[1]
    
    cluster_name = generate_cluster_name(coredump_file)
    
    print(f"Arquivo: {coredump_file}")
    print(f"Nome do Cluster: {cluster_name}")