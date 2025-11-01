#pragma once
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Estrutura para representar uma mensagem MQTT.
 */
typedef struct {
    char topic[128];
    char payload[256];
} mqtt_message_t;

/**
 * @brief Inicializa e inicia o cliente MQTT.
 *
 * @param queue Ponteiro para a fila onde mensagens recebidas serão colocadas.
 * 
 * @return ESP_OK se a inicialização for bem-sucedida, caso contrário retorna um código de erro esp_err_t.
 */
esp_err_t mqtt_app_start(QueueHandle_t queue);

/**
 * @brief Publica uma mensagem em um tópico MQTT.
 * 
 * @param topic Tópico onde a mensagem será publicada.
 * @param message Conteúdo da mensagem a ser publicada.
 * @param len Tamanho da mensagem.
 * @param qos Nível de QoS para a publicação.
 * 
 * @return true se a publicação foi bem-sucedida, false caso contrário.
 */
bool publish_message(const char *topic, const char *message, int len, uint8_t qos);

/** 
 * @brief Inscreve-se em um tópico MQTT. 
 * 
 * @param topic Tópico onde a inscrição será realizada.
 * @param qos Nível de QoS para a inscrição.
 * 
 * @return true se a inscrição foi bem-sucedida, false caso contrário.
 */
bool subscribe_to_topic(const char *topic, uint8_t qos);

#ifdef __cplusplus
}
#endif
