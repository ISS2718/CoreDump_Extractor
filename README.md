# CoreDump Extractor

Este é meu projeto de TCC para o curso de gradução em Engenharia de Computação na USP de São Carlos.

![Diagrama da Arquitetura do CoreDump Extractor](/docs/images/Arch_CoreDump_Extractor.png)

## CoreDump Uploader

O módulo `CoreDump Uploader` foi desenvolvido para facilitar a extração e o envio de relatórios de travamento (*coredumps*) de um dispositivo ESP32 para um servidor remoto. A principal vantagem desta abordagem é a sua flexibilidade. Em vez de acoplar o código a um protocolo de comunicação específico (como HTTP, MQTT ou TCP puro), o módulo utiliza um sistema de *callbacks*. Isso permite que o desenvolvedor implemente a lógica de comunicação que melhor se adapta ao seu projeto, seja ela qual for.

> **Nota sobre a Implementação:** O design deste módulo foi inspirado em duas implementações de referência. A primeira é o componente `coredump_upload` do **ESP-ADF (Espressif Advanced Development Framework)**, que valida a abordagem de extração e envio. No entanto, sua principal desvantagem é o forte acoplamento com o framework completo, tornando-o inviável para projetos que não utilizam seus outros recursos. A segunda inspiração é o projeto open-source **ESP32_coredump-to-server**, que introduziu um sistema de callbacks flexível, mas que se encontra abandonado desde 2022.
>
> Diante disso, a solução aqui apresentada foi criada para unir o melhor dos dois mundos: um componente moderno, enxuto e independente de frameworks pesados, mas que preserva a arquitetura flexível e agnóstica de protocolo baseada em callbacks.

O fluxo de operação é projetado para ser robusto e eficiente, especialmente em dispositivos com memória limitada. Ele lê o coredump diretamente da partição flash em blocos (*chunks*), opcionalmente codifica cada bloco em Base64 e o envia sequencialmente. Ao final do processo, se o envio for bem-sucedido, o coredump é apagado da flash para evitar reenvios desnecessários em reinicializações futuras.

### Arquitetura e Componentes

A arquitetura do módulo foi projetada para ser desacoplada, atuando como um orquestrador entre as APIs de baixo nível do sistema e a camada de comunicação da aplicação. O diagrama a seguir ilustra os componentes e suas interações.

![Diagrama da Arquitetura do CoreDump Uploader](docs/images/Arch_CoreDump_Uploader.png)

* **Aplicação do Usuário / Camada de Comunicação:** É a parte do firmware responsável pela lógica de negócio e pela comunicação com a internet. É nesta camada que as funções de callback (`start`, `write`, `end`) são implementadas para definir *como* os dados serão transmitidos.
* **CoreDump Uploader (Este Módulo):** Atua como o cérebro da operação. Ele orquestra o processo: solicita a leitura dos dados à biblioteca do sistema, processa esses dados (divide em chunks, codifica) e os entrega à camada de aplicação através dos callbacks, delegando a responsabilidade da transmissão.
* **ESP-IDF Core Dump Library:** A biblioteca oficial da Espressif que fornece as APIs de baixo nível para interagir com a partição de coredump na flash (`esp_core_dump_image_get()`, etc.).
* **Memória Flash:** O componente de hardware onde o coredump fica fisicamente armazenado.
* **Servidor Remoto:** O destino final para onde os dados do coredump são enviados.

### Estruturas de Dados (Structs)

As estruturas de dados são usadas para configurar e informar o processo de upload.

#### ➤ `coredump_uploader_callbacks_t`

Esta é a estrutura central para a configuração do upload. Ela agrupa ponteiros para as funções que implementarão a comunicação. O usuário deve fornecer as implementações dessas funções.

```c
typedef struct {
    coredump_upload_start_cb_t start;
    coredump_upload_write_cb_t write;
    coredump_upload_end_cb_t end;
    coredump_upload_progress_cb_t progress;
    void *priv;
} coredump_uploader_callbacks_t;
```

  - `start`: Uma função opcional chamada uma única vez no início do processo. Ideal para inicializar conexões, enviar metadados (como o número total de *chunks*) ou autenticar com o servidor.
  - `write`: A única função **obrigatória**. É chamada para cada *chunk* de dados do coredump. Sua responsabilidade é enviar o bloco de dados para o destino remoto.
  - `end`: Uma função opcional chamada ao final do processo. Útil para fechar conexões, liberar recursos ou verificar uma resposta final do servidor.
  - `progress`: Um callback opcional invocado após o envio de cada *chunk*, permitindo monitorar o progresso do upload (ex: para atualizar uma barra de progresso ou detectar timeouts).
  - `priv`: Um ponteiro `void*` para dados de contexto do usuário. Permite passar qualquer tipo de dado (como um handle de conexão HTTP ou um objeto de cliente MQTT) para dentro dos callbacks, mantendo o estado entre as chamadas.

#### ➤ `coredump_uploader_info_t`

Esta estrutura contém todos os metadados sobre o coredump e seu particionamento. Ela pode ser pré-calculada e usada para informar o servidor sobre o upload que está prestes a começar.

```c
typedef struct coredump_uploader_info {
    size_t flash_addr;
    size_t total_size;
    size_t chunk_size;
    size_t chunk_count;
    size_t last_chunk_size;

    bool use_base64;

    // campos para tamanhos em Base64
    size_t b64_total_size;
    size_t b64_chunk_size;
    size_t b64_last_chunk_size;
} coredump_uploader_info_t;
```

  - `flash_addr`: O endereço de memória na flash onde o coredump se inicia.
  - `total_size`: O tamanho total, em bytes, do coredump.
  - `chunk_size`: O tamanho (em bytes) de cada *chunk* de dados, exceto possivelmente o último.
  - `chunk_count`: O número total de *chunks* em que o coredump será dividido.
  - `last_chunk_size`: O tamanho específico do último *chunk*.
  - `use_base64`: Um booleano que indica se a codificação Base64 está habilitada.
  - `b64_total_size`: O tamanho total do coredump após ser codificado em Base64.
  - `b64_chunk_size`: O tamanho (em bytes) de cada *chunk* de dados após a codificação em Base64, exceto possivelmente o último. 
  - `b64_last_chunk_size`: O tamanho específico do último *chunk* após a codificação em Base64.

##### Observação importante

**Por que não existe um `b64_chunk_count`?**
A estrutura não precisa de um contador de chunks separado para Base64 porque a quantidade de chunks é a mesma em ambos os cenários. O processo de upload divide o coredump binário em `N` pedaços primeiro. Em seguida, para cada pedaço, ele o codifica e envia. Isso cria uma relação de um para um: **1 chunk binário se transforma em 1 chunk codificado**. O que muda é o *tamanho* de cada chunk, não a *quantidade* total deles.

A codificação Base64 converte cada grupo de 3 bytes em 4 caracteres. Portanto, o cálculo dos tamanhos codificados geralmente segue a fórmula:

$$\text{Tamanho Base64} = 4 \times \left\lceil \frac{\text{Tamanho Original}}{3} \right\rceil$$

Esses campos de tamanho são úteis para:

  - Alocar buffers do tamanho correto para os dados codificados.
  - Informar ao servidor ou ao protocolo de comunicação o tamanho exato dos dados que serão enviados.
  - Evitar desperdício de memória ou erros de transmissão por causa de tamanhos incorretos.

### Funções Públicas

Estas são as funções que compõem a API pública do módulo.

#### ➤ `bool coredump_uploader_need_upload(void)`

Esta função deve ser chamada logo no início da aplicação para verificar se a última reinicialização foi causada por um erro que gera um coredump.

  - **Finalidade**: Determinar a necessidade de um upload.
  - **Funcionamento**: Ela analisa a razão do último reset do `esp_reset_reason()`. Retorna `true` para resets anormais como `ESP_RST_PANIC` ou `ESP_RST_TASK_WDT` (Watchdog Timer), e `false` para resets normais como `ESP_RST_POWERON`.
  - **Retorno**: `true` se um upload é recomendado, `false` caso contrário.

#### ➤ `esp_err_t coredump_uploader_get_info(coredump_uploader_info_t *out, size_t desired_chunk_size, bool use_base64)`

Use esta função para obter os metadados do coredump antes de iniciar o envio.

  - **Finalidade**: Preencher a estrutura `coredump_uploader_info_t` com informações sobre o coredump existente.
  - **Parâmetros**:
      - `out`: Um ponteiro para a estrutura `coredump_uploader_info_t` que será preenchida.
      - `desired_chunk_size`: O tamanho desejado para os *chunks*. Se for 0, um valor padrão (`768` bytes) é utilizado. Se a codificação Base64 for usada, este valor é ajustado para ser um múltiplo de 3 para otimizar a codificação.
      - `use_base64`: Define se os cálculos de tamanho devem considerar a futura codificação em Base64.
  - **Retorno**: `ESP_OK` se um coredump for encontrado, ou um código de erro caso contrário (ex: `ESP_ERR_NOT_FOUND`).

#### ➤ `esp_err_t coredump_upload(const coredump_uploader_callbacks_t *cbs, const coredump_uploader_info_t *info)`

Esta é a função principal que executa todo o processo de upload.

  - **Finalidade**: Ler o coredump da flash em *chunks* e enviá-lo usando os callbacks fornecidos.
  - **Funcionamento**:
    1.  Valida se o callback `write` foi fornecido.
    2.  Se a estrutura `info` não for passada, ela chama `coredump_uploader_get_info` internamente com configurações padrão.
    3.  Aloca buffers de memória para a leitura da flash e, se necessário, para a codificação Base64.
    4.  Chama o callback `start` (se existir).
    5.  Entra em um loop que, para cada *chunk*:
        a. Lê o bloco de dados da flash.
        b. Codifica-o em Base64 (se `use_base64` for `true`).
        c. Chama o callback `write` para enviar os dados.
        d. Chama o callback `progress` (se existir).
    6.  Ao final do loop, chama o callback `end` (se existir).
    7.  Se todo o processo ocorrer sem erros, chama `esp_core_dump_image_erase()` para apagar o coredump da flash. Caso contrário, o coredump é mantido para uma futura tentativa.
  - **Retorno**: `ESP_OK` se o envio e a limpeza forem bem-sucedidos, ou um código de erro indicando a falha.

---

## Backend
### Estrutura mínima
### Subscriber
### CoreDump Interpreter
### CoreDump Clusterizer
### Cluster Sincronyzer

---

## Banco de Dados
### Requisitos
### DB Manager

---

## Visualização dos Dados (GUI)
