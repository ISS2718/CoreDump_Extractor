idf_component_register(SRCS "main.c" "coredump_uploader/coredump_uploader.c"
                    REQUIRES espcoredump spi_flash esp_partition nvs_flash mbedtls
                    INCLUDE_DIRS "." "coredump_uploader/")
