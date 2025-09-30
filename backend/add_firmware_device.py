import logging

try:
    import db_manager
except ImportError:
    logging.error("Erro: db_manager.py não encontrado. Certifique-se de que ele está no mesmo diretório ou no PYTHONPATH.")
    exit(1)

esp_chip_types = ['esp32', 'esp32c2', 'esp32c3', 'esp32c5', 'esp32c6', 'esp32c61', 'esp32h2', 'esp32h21', 'esp32h4', 'esp32p4', 'esp32s2', 'esp32s3']

if __name__ == "__main__":
    db_manager.create_database()

    # Pede ao usuário o MAC do dispositivo
    mac = input("Digite o MAC do dispositivo (formato XX:XX:XX:XX:XX:XX): ").strip()
    if not mac:
        print("MAC inválido.")
        exit(1)
    
    # Pede o nome do firmware
    firmware_name = input("Digite o nome do firmware: ").strip()

    # Pede a versão do firmware
    firmware_version = input("Digite a versão do firmware: ").strip()

    firmware = db_manager.get_firmware_by_name_version(firmware_name, firmware_version)
    if not firmware:
        # Pede PATH do firmware
        firmware_path = input("Digite o caminho do arquivo de firmware (.elf): ").strip()
        if not firmware_path:
            print("Caminho do firmware inválido.")
            exit(1)

        # adiciona o firmware ao banco de dados
        firmware = db_manager.add_firmware(firmware_name, firmware_version, firmware_path)

    # Pede o chip_type
    chip_type = input(f"Digite o tipo do chip (ex: {', '.join(esp_chip_types)}): ").strip()
    if chip_type not in esp_chip_types:
        print(f"Tipo de chip inválido. Deve ser um dos: {', '.join(esp_chip_types)}")
        exit(1)

    # Adiciona o dispositivo ao banco de dados
    device = db_manager.add_or_update_device(mac, firmware[0], chip_type)
    if device:
        print(f"Dispositivo com MAC {mac} adicionado/atualizado com sucesso.")
    else:
        print(f"Falha ao adicionar/atualizar dispositivo com MAC {mac}.")
    