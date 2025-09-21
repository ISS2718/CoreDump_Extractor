#pragma once
#include "esp_err.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Inicializa e inicia o cliente MQTT conforme configs do menuconfig. */
esp_err_t mqtt_app_start(void);

/** Publica uma mensagem em um tópico MQTT. */
bool publish_message(const char *topic, const char *message, int len, uint8_t qos);

#ifdef __cplusplus
}
#endif
