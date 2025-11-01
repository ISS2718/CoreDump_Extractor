#include "faults.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include <string.h>

void illegal_instruction_task(void * pvParameters) {
    ESP_LOGI("IllegalInstruction", "Iniciando tarefa de instrução ilegal");
    return;
}

void illegal_instruction_start(void) {
    xTaskCreate(illegal_instruction_task, "IllegalInstruction", 2048, NULL, 5, NULL);
}

void load_prohibited_start(void) {
    volatile int *ptr = (int *)0x40000000; // Endereço inválido para causar LoadProhibited
    ESP_LOGI("LoadProhibited", "Tentando acessar endereço inválido: %p", ptr);
    int value = *ptr; // Acesso inválido
    ESP_LOGI("LoadProhibited", "Valor lido: %d", value); // Nunca será alcançado
}

void store_prohibited_start(void) {
    volatile int *ptr = (int *)0x40000000; // Endereço inválido para causar StoreProhibited
    ESP_LOGI("StoreProhibited", "Tentando escrever no endereço inválido: %p", ptr);
    *ptr = 42; // Escrita inválida
    ESP_LOGI("StoreProhibited", "Valor escrito: 42"); // Nunca será alcançado
}


void integer_divide_by_zero_start(void) {
    volatile int a = 42;
    volatile int b = 0;
    ESP_LOGI("IntegerDivideByZero", "Tentando dividir %d por %d", a, b);
    int c = a / b; // Divisão por zero
    ESP_LOGI("IntegerDivideByZero", "Resultado da divisão: %d", c); // Nunca será alcançado
}

void stack_overflow_task(void * pvParameters) {
   char buffer[5000]; // Grande alocação na pilha
   memset(buffer, 0, sizeof(buffer)); // Uso do buffer para evitar otimizações
}

void stack_overflow_start(void) {
    xTaskCreate(stack_overflow_task, "StackOverflow", 2048, NULL, 5, NULL);
}