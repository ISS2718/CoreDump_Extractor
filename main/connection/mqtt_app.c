#include "mqtt_app.h"
#include "esp_crt_bundle.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "mqtt_client.h"
#include "sdkconfig.h"
#include <stdio.h>
#include <string.h>

static const char *TAG_MQTT = "MQTT";
static esp_mqtt_client_handle_t mqtt_client = NULL;

bool publish_message(const char *topic, const char *message, int len, uint8_t qos) {
    if (!mqtt_client) {
        ESP_LOGE(TAG_MQTT, "Cliente MQTT não está inicializado");
        return false;
    }
    int msg_id = esp_mqtt_client_publish(mqtt_client, topic, message, len, (int)qos, 0);
    if (msg_id == -1) {
        ESP_LOGE(TAG_MQTT, "Falha ao publicar mensagem no tópico %s", topic);
        return false;
    }
    ESP_LOGI(TAG_MQTT, "Mensagem publicada no tópico %s, msg_id=%d", topic, msg_id);
    return true;
}

static void mqtt_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    switch (event_id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG_MQTT, "MQTT conectado");
        break;
    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG_MQTT, "MQTT desconectado");
        break;
    case MQTT_EVENT_ERROR:
        ESP_LOGE(TAG_MQTT, "Erro MQTT");
        break;
    default:
        break;
    }
}

esp_err_t mqtt_app_start(void) {
    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = CONFIG_MQTT_BROKER_URI,
        .credentials.username = CONFIG_MQTT_USERNAME,
        .credentials.authentication.password = CONFIG_MQTT_PASSWORD,
        .credentials.set_null_client_id = false,
    };

    mqtt_client = esp_mqtt_client_init(&mqtt_cfg);
    if (!mqtt_client) {
        ESP_LOGE(TAG_MQTT, "esp_mqtt_client_init retornou NULL");
        return ESP_ERR_NO_MEM;
    }
    esp_err_t err = esp_mqtt_client_register_event(mqtt_client, ESP_EVENT_ANY_ID, mqtt_event_handler, NULL);
    if (err != ESP_OK) {
        ESP_LOGE(TAG_MQTT, "Falha ao registrar eventos MQTT: %s", esp_err_to_name(err));
        return err;
    }
    err = esp_mqtt_client_start(mqtt_client);
    if (err != ESP_OK) {
        ESP_LOGE(TAG_MQTT, "Falha ao iniciar cliente MQTT: %s", esp_err_to_name(err));
        return err;
    }
    return ESP_OK;
}
