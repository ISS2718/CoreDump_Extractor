#include "coredump_uploader.h"
#include "esp_log.h"
#include "faults.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "mqtt_app.h"
#include "nvs_flash.h"
#include "wifi.h"
#include <stdio.h>

static const char *TAG = "APP";

/** Fila para mensagens MQTT recebidas */
static QueueHandle_t mqtt_queue = NULL;

// Contexto para upload do coredump via MQTT
typedef struct {
    char topic[128];      // Tópico MQTT para envio do coredump
    int part_quantity;    // Quantidade total de partes do coredump
    int part_count;       // Contador de partes já enviadas
} mqtt_coredump_ctx_t;

// --- Callbacks para upload do coredump via MQTT ---

// Callback chamado no início do upload do coredump
static esp_err_t mqtt_coredump_start(void *priv) {
    mqtt_coredump_ctx_t *ctx = (mqtt_coredump_ctx_t *)priv;
    ESP_LOGI(TAG, "Iniciando envio do coredump para o tópico: %s (%d partes)", ctx->topic, ctx->part_quantity);
    char start_msg[64];
    // Publica mensagem inicial informando a quantidade de partes
    snprintf(start_msg, sizeof(start_msg), "{\"parts\":%d}", ctx->part_quantity);
    publish_message(ctx->topic, start_msg, strlen(start_msg), 1);
    return ESP_OK;
}

// Callback chamado para enviar cada parte do coredump
static esp_err_t mqtt_coredump_write(void *priv, const char *data, size_t len) {
    mqtt_coredump_ctx_t *ctx = (mqtt_coredump_ctx_t *)priv;
    ctx->part_count++;

    // O tópico dinâmico para indicar a parte atual
    char part_topic[150];
    snprintf(part_topic, sizeof(part_topic), "%s/%d", ctx->topic, ctx->part_count);

    ESP_LOGI(TAG, "Enviando parte %d do coredump (%d bytes)", ctx->part_count, len);
    // Publica a parte atual do coredump
    if (publish_message(part_topic, data, len, 1) == false) {
        ESP_LOGE(TAG, "Falha ao publicar coredump via MQTT.");
        return ESP_FAIL;
    }
    return ESP_OK;
}

// Callback de progresso do upload
static esp_err_t progress_cb(void *priv, const coredump_uploader_info_t *info, size_t chunk_index, size_t bytes_sent) {
    ESP_LOGI(TAG, "Chunk %u/%u (%u bytes enviados este passo)", (unsigned)(chunk_index + 1), (unsigned)info->chunk_count, (unsigned)bytes_sent);
    return ESP_OK;
}

// Callback chamado ao finalizar o upload do coredump
static esp_err_t mqtt_coredump_end(void *priv) {
    mqtt_coredump_ctx_t *ctx = (mqtt_coredump_ctx_t *)priv;
    ESP_LOGI(TAG, "Finalizado envio do coredump em %d partes.", ctx->part_count);
    return ESP_OK;
}

// --- Lógica principal da aplicação ---

// Verifica se há coredump para enviar e realiza o upload via MQTT
void check_and_upload_coredump(void) {
    if (coredump_uploader_need_upload()) {
        ESP_LOGW(TAG, "Detectada condição de falha. Tentando enviar coredump...");

        // 1. Configura o contexto para os callbacks
        mqtt_coredump_ctx_t mqtt_ctx = {
            .part_count = 0,
            .part_quantity = 0,
        };

        // Adiciona um identificador único ao tópico, como o MAC address
        uint8_t mac[6] = {0x16, 0x03, 0x25, 0x22, 0x07, 0x02};
        // esp_efuse_mac_get_default(mac);
        snprintf(mqtt_ctx.topic, sizeof(mqtt_ctx.topic), "coredump/%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

        // 2. Obtém informações do coredump
        coredump_uploader_info_t info;
        esp_err_t err = coredump_uploader_get_info(&info, 0, true);
        if (err != ESP_OK) {
            ESP_LOGI("APP", "Sem coredump ou erro (%s).", esp_err_to_name(err));
            return;
        }

        mqtt_ctx.part_quantity = info.chunk_count;

        // 3. Preenche a estrutura de callbacks
        coredump_uploader_callbacks_t uploader_cbs = {
            .start = mqtt_coredump_start,
            .write = mqtt_coredump_write,
            .progress = progress_cb,
            .end = mqtt_coredump_end,
            .priv = &mqtt_ctx,
        };

        // 4. Realiza o upload do coredump (usando Base64)
        err = coredump_upload(&uploader_cbs, &info);
        if (err == ESP_OK)
            ESP_LOGI(TAG, "Upload do coredump concluído com sucesso!");
        else
            ESP_LOGE(TAG, "Falha no processo de upload do coredump: %s", esp_err_to_name(err));
    } else {
        ESP_LOGI(TAG, "Inicialização normal, nenhum coredump a ser enviado.");
    }
}

// Função principal da aplicação
void app_main(void) {
    ESP_ERROR_CHECK(nvs_flash_init());
    ESP_LOGI(TAG, "Inicializando Wi-Fi...");
    if (wifi_init_start() == ESP_OK) {
        ESP_LOGI(TAG, "Inicializando MQTT...");
        mqtt_queue = xQueueCreate(10, sizeof(mqtt_message_t));
        ESP_ERROR_CHECK(mqtt_app_start(mqtt_queue));
    } else {
        ESP_LOGE(TAG, "Abortando inicialização do MQTT devido a falha no Wi-Fi");
    }
    mqtt_message_t msg;
    if (xQueueReceive(mqtt_queue, &msg, portMAX_DELAY) == pdTRUE) {
        if (strcmp(msg.payload, "client_connected") == 0) {
            ESP_LOGI(TAG, "Cliente MQTT conectado, iniciando verificação de coredump...");
        }
        memset(&msg, 0, sizeof(msg));
    }
    check_and_upload_coredump();

    subscribe_to_topic("device/fault_injection", 2);
    publish_message("device/ready", "Device Ready!", 14, 2);
    while(1) {
        if (xQueueReceive(mqtt_queue, &msg, portMAX_DELAY) == pdTRUE) {
            ESP_LOGI(TAG, "Processando mensagem do tópico %s: %s", msg.topic, msg.payload);
            if (strcmp(msg.payload, "IllegalInstructionCause") == 0) {
                ESP_LOGW(TAG, "Comando de falha recebido via MQTT. Forçando falha de instrução ilegal...");
                illegal_instruction_start();
            } else if (strcmp(msg.payload, "LoadProhibited") == 0) {
                ESP_LOGW(TAG, "Comando de falha recebido via MQTT. Forçando falha de acesso a memória inválida...");
                load_prohibited_start();
            } else if (strcmp(msg.payload, "StoreProhibited") == 0) {
                ESP_LOGW(TAG, "Comando de falha recebido via MQTT. Forçando falha de escrita em memória inválida...");
                store_prohibited_start();
            } else if (strcmp(msg.payload, "IntegerDivideByZero") == 0) {
                ESP_LOGW(TAG, "Comando de falha recebido via MQTT. Forçando falha de divisão por zero...");
                integer_divide_by_zero_start();
            } else if (strcmp(msg.payload, "Stack Overflow") == 0) {
                ESP_LOGW(TAG, "Comando de falha recebido via MQTT. Forçando falha de estouro de pilha...");
                stack_overflow_start();
            } else {
                ESP_LOGW(TAG, "Comando desconhecido recebido via MQTT: %s", msg.payload);
            }
        }
        memset(&msg, 0, sizeof(msg));
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}
