#include "coredump_uploader.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "nvs_flash.h"
#include <stdio.h>

static const char *TAG = "APP";

typedef struct {
    int part_quantity;
    int part_count;
} mqtt_coredump_ctx_t;

// --- Implementação dos Callbacks para MQTT ---

static esp_err_t mqtt_coredump_start(void *priv) {
    mqtt_coredump_ctx_t *ctx = (mqtt_coredump_ctx_t *)priv;
    ctx->part_count = 0;
    ESP_LOGI(TAG, "Iniciando envio do coredump com %d partes", ctx->part_quantity);
    return ESP_OK;
}

static esp_err_t mqtt_coredump_write(void *priv, const char *data, size_t len) {
    mqtt_coredump_ctx_t *ctx = (mqtt_coredump_ctx_t *)priv;
    ctx->part_count++;
    ESP_LOGI(TAG, "\033[1;35mEnviando parte %d do coredump (%d bytes)\033[0m", ctx->part_count, len);
    ESP_LOGI(TAG, "Payload: %.*s...", (int)len, data);
    return ESP_OK;
}

static esp_err_t progress_cb(void *priv, const coredump_uploader_info_t *info, size_t chunk_index, size_t bytes_sent) {
    ESP_LOGI("APP", "Chunk %u/%u (%u bytes enviados este passo)", (unsigned)(chunk_index + 1), (unsigned)info->chunk_count, (unsigned)bytes_sent);
    return ESP_OK;
}

static esp_err_t mqtt_coredump_end(void *priv) {
    mqtt_coredump_ctx_t *ctx = (mqtt_coredump_ctx_t *)priv;
    ESP_LOGI(TAG, "Finalizado envio do coredump em %d partes.", ctx->part_count);
    return ESP_OK;
}

// --- Lógica Principal na sua Aplicação ---
void check_and_upload_coredump(void) {
    if (coredump_uploader_need_upload()) {
        ESP_LOGW(TAG, "Detectada condição de falha. Tentando enviar coredump...");

        // 1. Configurar o contexto para os callbacks
        mqtt_coredump_ctx_t mqtt_ctx = {0};

        coredump_uploader_info_t info;
        esp_err_t err = coredump_uploader_get_info(&info, 1024, true); // exemplo: chunk 1024 (ajustado para múltiplo de 3) usando Base64
        if (err != ESP_OK) {
            ESP_LOGI("APP", "Sem coredump ou erro (%s).", esp_err_to_name(err));
            return;
        }

        mqtt_ctx.part_quantity = info.chunk_count;

        // 2. Preencher a estrutura de callbacks
        coredump_uploader_callbacks_t uploader_cbs = {
            .start = mqtt_coredump_start,
            .write = mqtt_coredump_write,
            .progress = progress_cb,
            .end = mqtt_coredump_end,
            .priv = &mqtt_ctx,
        };

        // 3. Chamar a função de upload (usando Base64 para MQTT)
        err = coredump_upload(&uploader_cbs, &info);
        if (err == ESP_OK)
            ESP_LOGI(TAG, "Upload do coredump concluído com sucesso!");
        else
            ESP_LOGE(TAG, "Falha no processo de upload do coredump: %s", esp_err_to_name(err));
    } else {
        ESP_LOGI(TAG, "Inicialização normal, nenhum coredump a ser enviado.");
    }
}

void app_main(void) {
    ESP_ERROR_CHECK(nvs_flash_init());
    check_and_upload_coredump();

    vTaskDelay(pdMS_TO_TICKS(60000));
    // gera um core dump para teste
    int *p = NULL;
    *p = 42;
}
