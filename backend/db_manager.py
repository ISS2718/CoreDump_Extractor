import sqlite3
import os
from datetime import datetime

# --- Configurações ---
DB_DIRECTORY = "db"
DB_NAME = "project.db"
DB_PATH = os.path.join(DB_DIRECTORY, DB_NAME)

# --- Função de Criação do Banco ---
def create_database():
    """Cria o arquivo do banco de dados SQLite e as tabelas necessárias."""
    os.makedirs(DB_DIRECTORY, exist_ok=True)
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS firmwares (
                firmware_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL, version TEXT NOT NULL,
                elf_path TEXT NOT NULL, UNIQUE(name, version)
            );""")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS devices (
                mac_address TEXT PRIMARY KEY,
                current_firmware_id INTEGER NOT NULL,
                chip_type TEXT,
                FOREIGN KEY(current_firmware_id) REFERENCES firmwares(firmware_id)
            );""")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS clusters (
                cluster_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );""")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS coredumps (
                coredump_id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_mac_address TEXT NOT NULL,
                firmware_id_on_crash INTEGER NOT NULL,
                cluster_id INTEGER, raw_dump_path TEXT NOT NULL,
                log_path TEXT, received_at INTEGER,
                FOREIGN KEY(device_mac_address) REFERENCES devices(mac_address),
                FOREIGN KEY(firmware_id_on_crash) REFERENCES firmwares(firmware_id),
                FOREIGN KEY(cluster_id) REFERENCES clusters(cluster_id)
            );""")
            print("Banco de dados e tabelas verificados com sucesso.")
    except sqlite3.Error as e:
        print(f"Ocorreu um erro ao criar o banco de dados: {e}")

# --- Função Auxiliar de Execução ---
def _execute_query(query, params=(), fetch=None):
    """Função auxiliar para executar consultas e evitar repetição de código."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            cursor = conn.cursor()
            cursor.execute(query, params)
            if fetch == 'one':
                return cursor.fetchone()
            elif fetch == 'all':
                return cursor.fetchall()
            else:
                conn.commit()
                return cursor.lastrowid
    except sqlite3.IntegrityError as e:
        print(f"Erro de Integridade: {e}. (Ex: Chave duplicada ou restrição de chave estrangeira violada)")
        return None
    except sqlite3.Error as e:
        print(f"Erro no banco de dados: {e}")
        return None

# --- Funções CRUD para Firmwares ---
def add_firmware(name, version, elf_path):
    query = "INSERT INTO firmwares (name, version, elf_path) VALUES (?, ?, ?)"
    return _execute_query(query, (name, version, elf_path))

def get_firmware_by_name_version(name, version):
    query = "SELECT * FROM firmwares WHERE name = ? AND version = ?"
    return _execute_query(query, (name, version), fetch='one')

def get_firmware_by_id(firmware_id):
    """Busca um firmware pelo seu ID."""
    query = "SELECT * FROM firmwares WHERE firmware_id = ?"
    return _execute_query(query, (firmware_id,), fetch='one')

def list_firmwares():
    return _execute_query("SELECT * FROM firmwares", fetch='all')

def delete_firmware(firmware_id):
    """Deleta um firmware. Falhará se algum dispositivo ou coredump o referenciar."""
    print(f"Tentando deletar firmware ID: {firmware_id}")
    query = "DELETE FROM firmwares WHERE firmware_id = ?"
    _execute_query(query, (firmware_id,))
    return True

# --- Funções CRUD para Devices ---
def add_or_update_device(mac_address, current_firmware_id, chip_type=None):
    query = """
    INSERT INTO devices (mac_address, current_firmware_id, chip_type) VALUES (?, ?, ?)
    ON CONFLICT(mac_address) DO UPDATE SET
    current_firmware_id = excluded.current_firmware_id,
    chip_type = excluded.chip_type;
    """
    _execute_query(query, (mac_address, current_firmware_id, chip_type))
    return True

def get_device(mac_address):
    return _execute_query("SELECT * FROM devices WHERE mac_address = ?", (mac_address,), fetch='one')

def list_devices():
    return _execute_query("SELECT * FROM devices", fetch='all')

def delete_device(mac_address):
    """Deleta um dispositivo. Falhará se algum coredump o referenciar."""
    print(f"Tentando deletar dispositivo: {mac_address}")
    _execute_query("DELETE FROM devices WHERE mac_address = ?", (mac_address,))
    return True

# --- Funções CRUD para Clusters ---
def add_cluster(name):
    return _execute_query("INSERT INTO clusters (name) VALUES (?)", (name,))

def get_cluster_by_name(name):
    return _execute_query("SELECT * FROM clusters WHERE name = ?", (name,), fetch='one')

def list_clusters():
    return _execute_query("SELECT * FROM clusters", fetch='all')

def rename_cluster(cluster_id, new_name):
    """Renomeia um cluster. A chave primária (ID) permanece a mesma."""
    print(f"Renomeando cluster ID {cluster_id} para '{new_name}'")
    _execute_query("UPDATE clusters SET name = ? WHERE cluster_id = ?", (new_name, cluster_id))
    return True

def delete_cluster(cluster_id):
    """Deleta um cluster. Falhará se algum coredump o referenciar."""
    print(f"Tentando deletar cluster ID: {cluster_id}")
    _execute_query("DELETE FROM clusters WHERE cluster_id = ?", (cluster_id,))
    return True

# --- Funções CRUD para Coredumps ---
def add_coredump(device_mac, firmware_id, raw_dump_path, log_path=None, received_at=None):
    query = """
    INSERT INTO coredumps (device_mac_address, firmware_id_on_crash, raw_dump_path, log_path, received_at)
    VALUES (?, ?, ?, ?, ?)
    """
    if received_at is None:
        now_unix = int(datetime.now().timestamp())
        received_at = now_unix
    coredump_id = _execute_query(query, (device_mac, firmware_id, raw_dump_path, log_path, received_at))
    return coredump_id

def get_unclustered_coredumps():
    return _execute_query("SELECT * FROM coredumps WHERE cluster_id IS NULL", fetch='all')

def assign_cluster_to_coredump(coredump_id, cluster_id):
    _execute_query("UPDATE coredumps SET cluster_id = ? WHERE coredump_id = ?", (cluster_id, coredump_id))
    return True

def list_all_coredumps():
    return _execute_query("SELECT * FROM coredumps", fetch='all')

def delete_coredump(coredump_id):
    """Deleta um registro de coredump."""
    print(f"Tentando deletar coredump ID: {coredump_id}")
    _execute_query("DELETE FROM coredumps WHERE coredump_id = ?", (coredump_id,))
    return True

# --- Bloco de Exemplo de Uso ---
if __name__ == "__main__":
    # Garante que o DB e as tabelas existam antes de começar
    print("--- Verificando a estrutura do banco de dados ---")
    create_database()

    print("\n--- Limpando dados de testes anteriores (se houver) ---")
    # A ordem importa por causa das chaves estrangeiras (deleta filhos primeiro)
    _execute_query("DELETE FROM coredumps")
    _execute_query("DELETE FROM clusters")
    _execute_query("DELETE FROM devices")
    _execute_query("DELETE FROM firmwares")
    print("Banco de dados limpo para o teste.")

    print("\n--- 1. Adicionando Dados Iniciais (CREATE) ---")
    # Adiciona firmwares
    fw_id_1 = add_firmware("SensorApp", "1.0.0", "storage/elfs/SensorApp/1.0.0/firmware.elf")
    fw_id_2 = add_firmware("DisplayApp", "2.1.0", "storage/elfs/DisplayApp/2.1.0/firmware.elf")
    print(f"Firmwares criados com IDs: {fw_id_1}, {fw_id_2}")

    # Adiciona dispositivos
    mac_1 = "AA:BB:CC:11:22:33"
    mac_2 = "DD:EE:FF:44:55:66"
    add_or_update_device(mac_1, fw_id_1)
    add_or_update_device(mac_2, fw_id_2)
    print(f"Dispositivos criados: {mac_1}, {mac_2}")

    # Adiciona clusters
    cluster_id_1 = add_cluster("Stack_Overflow_MQTT_Task")
    cluster_id_2 = add_cluster("I2C_Bus_Failure")
    print(f"Clusters criados com IDs: {cluster_id_1}, {cluster_id_2}")

    print("\n--- 2. Simulando Recebimento de Coredumps (CREATE) ---")
    cd_id_1 = add_coredump(mac_1, fw_id_1, f"storage/coredumps/{mac_1}/1759089593.cdmp")
    cd_id_2 = add_coredump(mac_1, fw_id_1, f"storage/coredumps/{mac_1}/1759089688.cdmp")
    cd_id_3 = add_coredump(mac_2, fw_id_2, f"storage/coredumps/{mac_2}/1759089901.cdmp")
    print(f"Coredumps recebidos com IDs: {cd_id_1}, {cd_id_2}, {cd_id_3}")

    print("\n--- 3. Verificando Estado e Processando Dados (READ, UPDATE) ---")
    print("Firmwares no DB:", list_firmwares())
    print("Dispositivos no DB:", list_devices())
    
    unclustered = get_unclustered_coredumps()
    print(f"Coredumps não classificados ({len(unclustered)}):", unclustered)

    # Classifica os coredumps
    print("\nIniciando classificação...")
    assign_cluster_to_coredump(cd_id_1, cluster_id_1)
    assign_cluster_to_coredump(cd_id_2, cluster_id_1) # Outro coredump do mesmo cluster
    assign_cluster_to_coredump(cd_id_3, cluster_id_2)
    print("Coredumps classificados.")

    # Renomeia um cluster para demonstrar o UPDATE
    rename_cluster(cluster_id_1, "Stack_Overflow_em_MQTT_Task")

    print("\nCoredumps não classificados agora (deve estar vazio):", get_unclustered_coredumps())
    print("Todos os coredumps (após classificação):", list_all_coredumps())
    print("Clusters (após renomear):", list_clusters())

    print("\n--- 4. Testando Deleção e Integridade dos Dados (DELETE) ---")
    
    print("\nPasso 4.1: Tentando deletar um firmware em uso (deve falhar)")
    delete_firmware(fw_id_2) # Falha, pois o dispositivo mac_2 depende dele
    print("Firmwares após tentativa de deleção:", list_firmwares())

    print("\nPasso 4.2: Deletando um coredump individualmente (deve funcionar)")
    delete_coredump(cd_id_1)
    print("Coredumps após deletar o ID 1:", list_all_coredumps())

    print("\nPasso 4.3: Deletando um firmware na ordem correta (filhos primeiro)")
    # Para deletar o firmware fw_id_2, precisamos deletar o que depende dele
    print(f"Deletando dependências do firmware {fw_id_2}...")
    delete_coredump(cd_id_3) # Deleta o coredump do dispositivo 2
    delete_device(mac_2)     # Deleta o dispositivo 2
    delete_firmware(fw_id_2) # AGORA SIM! A deleção do firmware funciona
    
    print("\n--- 5. Estado Final do Banco de Dados ---")
    print("Firmwares restantes:", list_firmwares())
    print("Dispositivos restantes:", list_devices())
    print("Coredumps restantes:", list_all_coredumps())
    print("Clusters restantes:", list_clusters())