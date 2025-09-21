#pragma once
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Inicializa Wi-Fi em modo station usando as configs do menuconfig. */
esp_err_t wifi_init_start(void);

#ifdef __cplusplus
}
#endif
