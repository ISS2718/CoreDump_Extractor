from __future__ import annotations

import os
import time
import signal
import threading
import sys

from dotenv import load_dotenv
from paho import mqtt
import random
import paho.mqtt.client as paho

# Carrega variáveis de ambiente do arquivo .env
load_dotenv()

# Variáveis obrigatórias - falham se não estiverem definidas
MQTT_HOST: str = os.getenv("MQTT_HOST")
MQTT_PORT_STR: str = os.getenv("MQTT_PORT")
MQTT_USER: str = os.getenv("MQTT_USER")
MQTT_PASS: str = os.getenv("MQTT_PASS")

if not MQTT_HOST:
    raise ValueError("MQTT_HOST não está definido. Configure no arquivo .env ou como variável de ambiente.")
if not MQTT_PORT_STR:
    raise ValueError("MQTT_PORT não está definido. Configure no arquivo .env ou como variável de ambiente.")
if not MQTT_USER:
    raise ValueError("MQTT_USER não está definido. Configure no arquivo .env ou como variável de ambiente.")
if not MQTT_PASS:
    raise ValueError("MQTT_PASS não está definido. Configure no arquivo .env ou como variável de ambiente.")

MQTT_PORT: int = int(MQTT_PORT_STR)

# Variáveis opcionais - com valores padrão
DEVICE_READY_TOPIC: str = os.getenv("DEVICE_READY_TOPIC", "device/ready")
DEVICE_FAULT_INJECTION_TOPIC: str = os.getenv("DEVICE_FAULT_INJECTION_TOPIC", "device/fault_injection")


client: paho.Client | None = None
client_connected: bool = False
device_ready: bool = False

def start(on_connect: callable, on_message: callable) -> bool:
    global client
    if client is not None:
        return False
    
    client = paho.Client(callback_api_version=paho.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.tls_set(tls_version=mqtt.client.ssl.PROTOCOL_TLS)
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client = client
    client.loop_start()
    return True

def stop() -> bool:
    global client
    if client is None:
        return False
    try:
        client.loop_stop()
        client.disconnect()
    finally:
        client = None
    return True

def on_connect(client: paho.Client, userdata: any, flags: dict[str, any], rc: int, properties: any | None = None) -> None:
    global client_connected
    if rc == 0:
        print("Conectado ao broker MQTT com sucesso")
        client_connected = True
        client.subscribe(DEVICE_READY_TOPIC, qos=2)
    else:
        print(f"mqtt.falha_conexao rc={rc}")
        client_connected = False

def on_message(client: paho.Client, userdata: any, msg: paho.MQTTMessage) -> None:
    global device_ready
    try:
        if msg.topic == DEVICE_READY_TOPIC:
            device_ready = True
            print("Device pronto recebido")
    except Exception as e:
        print(f"Error processing message: {e}")

if __name__ == "__main__":
    faults_list = [
        "IllegalInstructionCause",
        "LoadProhibited",
        "StoreProhibited",
        "IntegerDivideByZero",
        "Stack Overflow"
    ]
    
    start(on_connect, on_message)
    try:
        while not client_connected:
            print("Aguardando conexão com o broker MQTT...")
            time.sleep(1)

        times_of_execution = int(input("Digite quantas vezes quer injetar os defeitos: "))

        total_faults_sent = 0
        for iteration in range(times_of_execution):
            random.shuffle(faults_list)
            print(f"Ordem de injeção de defeitos (iteração {iteration + 1}/{times_of_execution}): {faults_list}")
            for fault_index, fault in enumerate(faults_list):
                # Reseta device_ready antes de esperar
                device_ready = False
                print(f"Aguardando device ficar pronto para falha {fault_index + 1}/{len(faults_list)} (iteração {iteration + 1}): {fault}")
                
                # Aguarda device ficar pronto com timeout
                timeout = 0
                max_wait = 300  # 5 minutos máximo de espera
                while not device_ready and timeout < max_wait:
                    time.sleep(0.1)  # Verifica a cada 100ms
                    timeout += 0.1
                
                if not device_ready:
                    print(f"AVISO: Timeout aguardando device ficar pronto para {fault}. Pulando...")
                    continue
                
                device_ready = False
                print(f"Injetando defeito: {fault}")
                client.publish(DEVICE_FAULT_INJECTION_TOPIC, fault, qos=2)
                total_faults_sent += 1
                time.sleep(5)  # Aguarda 5 segundos antes de injetar o próximo defeito
        
        print(f"\nTotal de defeitos injetados: {total_faults_sent} de {times_of_execution * len(faults_list)} esperados")
            
    except KeyboardInterrupt:
        print("Interrompido pelo usuário")
    finally:
        stop()