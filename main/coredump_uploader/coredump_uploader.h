#ifndef COREDUMP_UPLOADER_H
#define COREDUMP_UPLOADER_H

#include "esp_err.h"
#include <stdbool.h>
#include <stddef.h>

/**
 * @brief Ponteiro de função chamado antes do início da leitura do coredump.
 *
 * Use para inicializar a conexão, abrir um socket, etc.
 * @param priv Ponteiro para dados de contexto do usuário.
 * @return ESP_OK se bem-sucedido.
 */
typedef esp_err_t (*coredump_upload_start_cb_t)(void *priv);

/**
 * @brief Ponteiro de função chamado para enviar um bloco de dados do coredump.
 *
 * @param priv Ponteiro para dados de contexto do usuário.
 * @param data Buffer contendo o bloco de dados.
 * @param len Comprimento do buffer de dados.
 * @return ESP_OK se bem-sucedido.
 */
typedef esp_err_t (*coredump_upload_write_cb_t)(void *priv, const char *data, size_t len);

/**
 * @brief Ponteiro de função chamado após o término da leitura do coredump.
 *
 * Use para fechar a conexão, verificar a resposta do servidor, etc.
 * @param priv Ponteiro para dados de contexto do usuário.
 * @return ESP_OK se bem-sucedido.
 */
typedef esp_err_t (*coredump_upload_end_cb_t)(void *priv);

/**
 * @brief Estrutura de configuração contendo os callbacks de comunicação.
 */
// Forward declaration para uso no callback de progresso
struct coredump_uploader_info;

/**
 * @brief Callback de progresso chamado após cada chunk enviado (opcional).
 *
 * @param priv Contexto do usuário (callbacks->priv)
 * @param info Ponteiro para estrutura informativa do coredump (const)
 * @param chunk_index Índice do chunk enviado (0-based)
 * @param bytes_sent Quantidade de bytes efetivamente enviados neste chunk
 *                   (já codificados em Base64 se aplicável)
 * @return ESP_OK para continuar. Qualquer erro aborta o upload.
 */
typedef esp_err_t (*coredump_upload_progress_cb_t)(void *priv,
                                                   const struct coredump_uploader_info *info,
                                                   size_t chunk_index,
                                                   size_t bytes_sent);

typedef struct {
    coredump_upload_start_cb_t start;     // Chamado antes de iniciar a escrita.
    coredump_upload_write_cb_t write;     // Chamado para cada bloco de dados.
    coredump_upload_end_cb_t end;         // Chamado ao finalizar a escrita.
    coredump_upload_progress_cb_t progress; // Chamado após cada chunk enviado.
    void *priv;                           // Ponteiro privado para dados de contexto.
} coredump_uploader_callbacks_t;

/**
 * @brief Estrutura com metadados sobre o coredump e particionamento em chunks.
 */
typedef struct coredump_uploader_info {
    size_t flash_addr;            // Endereço na flash onde começa o coredump
    size_t total_size;            // Tamanho total bruto (binário) do coredump
    size_t chunk_size;            // Tamanho configurado (bruto) de cada chunk (exceto último)
    size_t chunk_count;           // Número total de chunks (>=1)
    size_t last_chunk_size;       // Tamanho bruto do último chunk
    bool use_base64;              // Se será usado Base64
    // Informações de tamanho quando Base64 está habilitado
    size_t b64_total_size;        // Total estimado após Base64
    size_t b64_chunk_size;        // Tamanho codificado típico de um chunk completo
    size_t b64_last_chunk_size;   // Tamanho codificado do último chunk
} coredump_uploader_info_t;

/**
 * @brief Verifica se o motivo do último reset indica a necessidade de enviar um coredump.
 *
 * @return true se um coredump deve ser enviado (ex: PÂNICO, WDT).
 * @return false para resets normais (ex: POWERON, SW_RESET).
 */
bool coredump_uploader_need_upload(void);

/**
 * @brief Versão estendida permitindo controle de chunk_size e progresso.
 *
 * Fluxo:
 *  1. Obtenha info via coredump_uploader_get_info (ou passe NULL para calcular internamente com default).
 *  2. Opcionalmente use callbacks->start para enviar meta (ex: JSON com chunk_count).
 *  3. Esta função fará o envio chunk a chunk.
 *
 * @param cbs Callbacks de comunicação (write obrigatório).
 * @param info Informações previamente calculadas. Se NULL será calculada com default.
 * @return ESP_OK se enviado e apagado com sucesso.
 */
esp_err_t coredump_upload(const coredump_uploader_callbacks_t *cbs, const coredump_uploader_info_t *info);

/**
 * @brief Obtém informações sobre o coredump atual e particionamento em chunks.
 *
 * @param out Estrutura de saída preenchida em caso de sucesso.
 * @param desired_chunk_size Tamanho desejado de chunk (bruto). Se 0, usa default interno.
 * @param use_base64 Define se cálculo deve considerar codificação Base64.
 * @return ESP_OK se um coredump foi encontrado e info preenchida.
 * @return Erro de esp_core_dump_image_get caso não exista ou falhe.
 */
esp_err_t coredump_uploader_get_info(coredump_uploader_info_t *out, size_t desired_chunk_size, bool use_base64);

#endif // COREDUMP_UPLOADER_H
