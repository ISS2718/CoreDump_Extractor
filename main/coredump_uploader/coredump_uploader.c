#include "coredump_uploader.h"
#include "esp_core_dump.h"
#include "esp_flash.h"
#include "esp_log.h"
#include "esp_system.h"
#include "mbedtls/base64.h"
#include <string.h>

// Tamanho default de chunk (múltiplo de 3 para evitar padding interno em Base64)
#define COREDUMP_DEFAULT_CHUNK_SIZE (3 * 256) // 768 bytes

static const char *TAG = "COREDUMP_UPLOADER";

bool coredump_uploader_need_upload(void) {
    esp_reset_reason_t reason = esp_reset_reason();
    ESP_LOGI(TAG, "Reset reason: %d", reason);
    switch (reason) {
    // Casos que geram coredump
    case ESP_RST_PANIC:
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:
    // Caso de reset indefinido, é mais seguro verificar
    case ESP_RST_UNKNOWN:
        return true;
    // Resets normais que não devem acionar upload
    case ESP_RST_POWERON:
    case ESP_RST_SW:
    case ESP_RST_DEEPSLEEP:
    default:
        return false;
    }
}

// Função interna para calcular tamanhos Base64 de um bloco
static inline size_t _b64_encoded_size(size_t in_len) {
    return ((in_len + 2) / 3) * 4; // Sem considerar terminador NUL
}

esp_err_t coredump_uploader_get_info(coredump_uploader_info_t *out, size_t desired_chunk_size, bool use_base64) {
    if (!out)
        return ESP_ERR_INVALID_ARG;
    memset(out, 0, sizeof(*out));

    size_t addr = 0, size = 0;
    esp_err_t err = esp_core_dump_image_get(&addr, &size);
    if (err != ESP_OK)
        return err;
    if (size == 0)
        return ESP_ERR_NOT_FOUND;

    // Ajuste de chunk size
    size_t chunk = desired_chunk_size ? desired_chunk_size : COREDUMP_DEFAULT_CHUNK_SIZE;
    // Garante múltiplo de 3 se usar Base64 para minimizar padding interno
    if (use_base64 && (chunk % 3) != 0) {
        chunk -= (chunk % 3); // arredonda para baixo
        if (chunk == 0)
            chunk = 3; // mínimo válido
    }

    size_t chunk_count = (size + chunk - 1) / chunk;
    size_t last_chunk_size = (size % chunk) ? (size % chunk) : chunk;

    out->flash_addr = addr;
    out->total_size = size;
    out->chunk_size = chunk;
    out->chunk_count = chunk_count;
    out->last_chunk_size = last_chunk_size;
    out->use_base64 = use_base64;

    if (use_base64) {
        out->b64_chunk_size = _b64_encoded_size(chunk);
        out->b64_last_chunk_size = _b64_encoded_size(last_chunk_size);
        out->b64_total_size = (chunk_count > 1) ? (out->b64_chunk_size * (chunk_count - 1) + out->b64_last_chunk_size) : out->b64_last_chunk_size;
    }
    return ESP_OK;
}

esp_err_t coredump_upload(const coredump_uploader_callbacks_t *cbs, const coredump_uploader_info_t *info) {
    if (!cbs || !cbs->write) {
        ESP_LOGE(TAG, "Callbacks 'write' não pode ser nulo.");
        return ESP_ERR_INVALID_ARG;
    }

    coredump_uploader_info_t local_info;
    if (!info) {
        esp_err_t ierr = coredump_uploader_get_info(&local_info, 0, false); // default sem base64
        if (ierr != ESP_OK) {
            ESP_LOGI(TAG, "Nenhum coredump encontrado (%s)", esp_err_to_name(ierr));
            return ierr;
        }
        info = &local_info;
    }

    ESP_LOGI(TAG, "Coredump: %u bytes @0x%08x em %u chunks (chunk=%u, último=%u) base64=%d", (unsigned)info->total_size, (unsigned)info->flash_addr,
             (unsigned)info->chunk_count, (unsigned)info->chunk_size, (unsigned)info->last_chunk_size, info->use_base64);

    // Aloca buffer de leitura
    uint8_t *read_chunk = malloc(info->chunk_size);
    if (!read_chunk) {
        ESP_LOGE(TAG, "Falha ao alocar buffer de leitura.");
        return ESP_ERR_NO_MEM;
    }

    // Buffer Base64 se necessário
    uint8_t *b64_buf = NULL;
    size_t b64_buf_capacity = 0;
    if (info->use_base64) {
        // Capacidade suficiente para o maior chunk (chunk_size) + terminador
        b64_buf_capacity = _b64_encoded_size(info->chunk_size) + 1;
        b64_buf = malloc(b64_buf_capacity);
        if (!b64_buf) {
            ESP_LOGE(TAG, "Falha ao alocar buffer Base64.");
            free(read_chunk);
            return ESP_ERR_NO_MEM;
        }
    }

    esp_err_t err = ESP_OK;

    // Callback de início
    if (cbs->start) {
        err = cbs->start(cbs->priv);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Callback 'start' falhou.");
            if (read_chunk)
                free(read_chunk);
            if (b64_buf)
                free(b64_buf);
            return err;
        }
    }

    // Loop de envio
    for (size_t chunk_index = 0; chunk_index < info->chunk_count; ++chunk_index) {
        size_t offset = chunk_index * info->chunk_size;
        size_t bytes_to_read = (chunk_index == info->chunk_count - 1) ? info->last_chunk_size : info->chunk_size;

        err = esp_flash_read(esp_flash_default_chip, read_chunk, info->flash_addr + offset, bytes_to_read);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Falha ao ler coredump (chunk %u)", (unsigned)chunk_index);
            break;
        }

        const char *data_to_send = (const char *)read_chunk;
        size_t len_to_send = bytes_to_read;
        if (info->use_base64) {
            size_t actual_b64_len = 0;
            int b64_ret = mbedtls_base64_encode(b64_buf, b64_buf_capacity, &actual_b64_len, read_chunk, bytes_to_read);
            if (b64_ret != 0) {
                ESP_LOGE(TAG, "Base64 falhou (chunk %u, mbedtls=-0x%04x)", (unsigned)chunk_index, -b64_ret);
                err = ESP_FAIL;
                break;
            }
            data_to_send = (const char *)b64_buf;
            len_to_send = actual_b64_len;
        }

        err = cbs->write(cbs->priv, data_to_send, len_to_send);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "Callback 'write' falhou (chunk %u)", (unsigned)chunk_index);
            break;
        }

        if (cbs->progress) {
            esp_err_t p_err = cbs->progress(cbs->priv, info, chunk_index, len_to_send);
            if (p_err != ESP_OK) {
                ESP_LOGW(TAG, "Upload interrompido pelo callback de progresso (chunk %u)", (unsigned)chunk_index);
                err = p_err;
                break;
            }
        }
    }

    // Callback de fim
    if (cbs->end) {
        esp_err_t end_err = cbs->end(cbs->priv);
        if (err == ESP_OK && end_err != ESP_OK) {
            ESP_LOGE(TAG, "Callback 'end' falhou.");
            err = end_err;
        }
    }

    if (err == ESP_OK) {
        ESP_LOGI(TAG, "Coredump enviado com sucesso. Apagando da flash...");
        esp_err_t erase_err = esp_core_dump_image_erase();
        if (erase_err != ESP_OK) {
            ESP_LOGE(TAG, "Falha ao apagar coredump (%s)", esp_err_to_name(erase_err));
            err = erase_err; // Pode optar por não sobrescrever; aqui sobrescrevemos para alertar
        }
    } else {
        ESP_LOGW(TAG, "Upload incompleto. Coredump mantido para nova tentativa.");
    }

    if (read_chunk)
        free(read_chunk);
    if (b64_buf)
        free(b64_buf);
    return err;
}
