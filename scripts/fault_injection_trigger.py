from __future__ import annotations

import os
import time
import signal
import threading
import sys

from paho import mqtt
import random
import paho.mqtt.client as paho

MQTT_HOST: str = os.getenv("MQTT_HOST", "d7dc78b4d42d49e8a71a4edfcfb1d6ca.s1.eu.hivemq.cloud")
MQTT_PORT: int = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USER: str = os.getenv("MQTT_USER", "FAULT_INJECTION_TRIGGER")
MQTT_PASS: str = os.getenv("MQTT_PASS", "QeKE`B2G7Q8/")
DEVICE_READY_TOPIC: str = os.getenv("DEVICE_START_TOPIC", f"device/ready")
DEVICE_FAULT_INJECTION_TOPIC: str = os.getenv("DEVICE_FAULT_INJECTION_TOPIC", f"device/fault_injection")


global client, client_connected, device_ready
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

        for _ in range(times_of_execution):
            random.shuffle(faults_list)
            print(f"Ordem de injeção de defeitos: {faults_list}")
            for fault in faults_list:
                while True:
                    if device_ready:
                        device_ready = False
                        print(f"Injetando defeito: {fault}")
                        client.publish(DEVICE_FAULT_INJECTION_TOPIC, fault, qos=2)
                        time.sleep(5)  # Aguarda 5 segundos antes de injetar o próximo defeito
                        break
            
    except KeyboardInterrupt:
        print("Interrompido pelo usuário")
    finally:
        stop()