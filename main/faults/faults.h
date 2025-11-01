#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Cria uma falha de instrução ilegal.
 */
void illegal_instruction_start(void);

/**
 * @brief Cria uma falha de LoadProhibited.
 */
void load_prohibited_start(void);

/**
 * @brief Cria uma falha de StoreProhibited.
 */
void store_prohibited_start(void);

/**
 * @brief Cria uma falha de IntegerDivideByZero.
 */
void integer_divide_by_zero_start(void);

/**
 * @brief Cria uma falha de StackOverflow.
 */
void stack_overflow_start(void);

#ifdef __cplusplus
}
#endif