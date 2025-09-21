idf_component_register(SRCS "main.c" "connection/wifi.c" "connection/mqtt_app.c" "coredump_uploader/coredump_uploader.c"
                    REQUIRES espcoredump spi_flash mqtt esp_partition nvs_flash esp_wifi
                    INCLUDE_DIRS "." "connection/" "coredump_uploader/")
