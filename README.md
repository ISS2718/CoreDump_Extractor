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
* **Backend:** O destino final para onde os dados do coredump são enviados para ser interpretado, clusterizado e por fim armazenado.

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

  - **Finalidade:** Determinar a necessidade de um upload.
  - **Funcionamento:** Ela analisa a razão do último reset do `esp_reset_reason()`. Retorna `true` para resets anormais como `ESP_RST_PANIC` ou `ESP_RST_TASK_WDT` (Watchdog Timer), e `false` para resets normais como `ESP_RST_POWERON`.
  - **Retorno:** `true` se um upload é recomendado, `false` caso contrário.

#### ➤ `esp_err_t coredump_uploader_get_info(coredump_uploader_info_t *out, size_t desired_chunk_size, bool use_base64)`

Use esta função para obter os metadados do coredump antes de iniciar o envio.

  - **Finalidade:** Preencher a estrutura `coredump_uploader_info_t` com informações sobre o coredump existente.
  - **Parâmetros:**
      - `out`: Um ponteiro para a estrutura `coredump_uploader_info_t` que será preenchida.
      - `desired_chunk_size`: O tamanho desejado para os *chunks*. Se for 0, um valor padrão (`768` bytes) é utilizado. Se a codificação Base64 for usada, este valor é ajustado para ser um múltiplo de 3 para otimizar a codificação.
      - `use_base64`: Define se os cálculos de tamanho devem considerar a futura codificação em Base64.
  - **Retorno:** `ESP_OK` se um coredump for encontrado, ou um código de erro caso contrário (ex: `ESP_ERR_NOT_FOUND`).

#### ➤ `esp_err_t coredump_upload(const coredump_uploader_callbacks_t *cbs, const coredump_uploader_info_t *info)`

Esta é a função principal que executa todo o processo de upload.

  - **Finalidade:** Ler o coredump da flash em *chunks* e enviá-lo usando os callbacks fornecidos.
  - **Funcionamento:**
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
  - **Retorno:** `ESP_OK` se o envio e a limpeza forem bem-sucedidos, ou um código de erro indicando a falha.

---

## Backend

O backend constitui o núcleo de processamento do sistema, projetado para gerenciar o ciclo de vida completo de um coredump. Sua arquitetura foi concebida de forma modular para isolar as diferentes etapas do tratamento da falha, desde a coleta de dados brutos até a sua análise e classificação.

Esta estrutura é segmentada em três componentes lógicos principais, cada um com um escopo de responsabilidade bem definido dentro do fluxo de processamento:

![Diagrama da Arquitetura do Backend](docs/images/Arch_Backend.png)

- **Receptor:** Atua como a interface primária do sistema, responsável por estabelecer a comunicação com os dispositivos, receber os dados de falha e realizar uma validação inicial.
- **Interpretador:** Tem a função de traduzir os dados brutos do coredump em um formato estruturado e legível, extraindo as informações técnicas essenciais para o diagnóstico.
- **Clusterizador:** É o componente analítico, cuja responsabilidade é comparar as falhas interpretadas com uma base de conhecimento existente para agrupar erros recorrentes e identificar anomalias.

### Receptor

O Receptor atua como a porta de entrada (gateway) do backend. Seu objetivo principal é ser a interface de comunicação com os microcontroladores em campo, garantindo que os dados recebidos sejam autênticos e íntegros antes de iniciar o processamento.

![Fluxograma de Funcionamento do Receptor](docs/images/Arch_Backend_Recptor.png)

**Requisitos Funcionais:**
- Expor um endpoint (por exemplo, uma API HTTP) seguro e estável para o recebimento dos coredumps.
- Ser capaz de processar requisições contendo dados binários (ou Base64) e metadados associados (como ID do dispositivo, versão do firmware, etc.).
- Rejeitar dados caso haja algum problema na envio/recepção.
- Encaminhar o coredump bruto para o módulo Interpretador.

### Interpretador

O **Interpretador** é o componente responsável por traduzir os dados brutos e de baixo nível de um *coredump* em um formato estruturado e legível por humanos. O objetivo é extrair as informações essenciais para a análise da falha.

![Fluxograma de Funcionamento do Interpretador](docs/images/Arch_Backend_Interpretador.png)

**Requisitos Funcionais:**
* Acessar o arquivo binário do *coredump* encaminhado pelo Receptor.
* Utilizar os arquivos de depuração (*debugging symbols*), correspondentes à versão do firmware, para mapear os endereços de memória a nomes de funções e linhas de código.
* Estruturar as informações extraídas em um formato padronizado (como JSON), facilitando o processamento pelo módulo seguinte.
* Encaminhar as informações extraídas, e padronizadas, para o módulo Clusterizador.

### Clusterizador

O **Clusterizador** é o cérebro analítico do sistema. Sua função é analisar os dados interpretados do *coredump* e agrupá-lo com outras falhas semelhantes, permitindo a deduplicação de erros e a identificação da frequência de cada problema.

![Fluxogram de Funcionamento do Clusterizador](docs/images/Arch_Backend_Clusterizador.png)

**Requisitos Funcionais:**
* Receber e processar os dados estruturados do Interpretador.
* Calcular a similaridade entre o novo coredump e os clusters já existentes.
* Atribuir o coredump a um cluster existente ou criar um novo cluster para a falha.
* Salvar a associação do coredump ao seu respectivo cluster no banco de dados.

---

## Banco de Dados

Para um banco de dados mínimo e funcional para nosso sistema de extração remota e classificação de coredups os re
Este documento define a estrutura de dados mínima e essencial para o sistema de extração remota e classificação de coredumps. O objetivo é estabelecer um esquema de banco de dados que suporte o armazenamento de firmwares, dispositivos, coredumps e os clusters de classificação resultantes, garantindo a rastreabilidade e a integridade das informações.

### Diagrama Entidade-Relacionamento (ER)

Para visualizar as relações, podemos imaginar o seguinte diagrama:

![Diagrama Entidade Relacionamento para um Banco de Dados Mínimo](docs/images/DER_Min_DB.png)

*Legenda: `PK` - Chave Primária, `FK` - Chave Estrangeira, `UK` - Chave Única, `||` - Exatamente um, `o{` - Zero ou mais, `|{` - Um ou mais.*

### Descrição das Entidades

#### FIRMWARES

Esta entidade funciona como um catálogo de todas as versões de software existentes. Armazenar o **nome** e a **versão** de cada firmware é fundamental para saber exatamente qual código estava em execução quando uma falha ocorreu.

#### DEVICES

Representa cada dispositivo físico (hardware) monitorado pelo sistema. O seu identificador único (`id`, como um MAC Address) é a chave para rastrear a origem de um coredump. O relacionamento com `FIRMWARES` permite saber qual software está ativo em cada dispositivo em tempo real.

#### CLUSTERS

É a entidade central para a funcionalidade de **classificação**. Cada registro em `CLUSTERS` representa um "tipo" de erro ou falha única. Ao agrupar coredumps semelhantes sob o mesmo cluster, o sistema permite identificar a frequência e o impacto de cada bug específico. O `name` do cluster serve como um identificador legível para a falha (ex: "STACK\_OVERFLOW\_WIFI\_TASK").

#### COREDUMPS

Esta é a entidade principal, registrando cada evento de falha individual. Cada coredump está obrigatoriamente ligado ao **dispositivo** que o gerou e ao **firmware** que estava executando no momento da falha. O campo `cluster_id` é `Nullable` (pode ser nulo) porque um coredump é primeiro recebido e só depois classificado. O `file_path` aponta para o local do arquivo de coredump (já interpretado), evitando sobrecarregar o banco de dados.

### Justificativa dos Relacionamentos

Os relacionamentos definem as regras de negócio e garantem a integridade dos dados.

#### `FIRMWARES ||--|{ DEVICES : "executa em"`

  * **Leitura:** Um `FIRMWARE` é executado em um ou mais `DEVICES`.
  * **Justificativa (1:N):** Este relacionamento de "um para muitos" modela a realidade da implantação de software em IoT. Uma única versão de firmware (ex: `v2.1.0`) é distribuída e executada em centenas ou milhares de dispositivos físicos.

#### `DEVICES ||--o{ COREDUMPS : "gera"`

  * **Leitura:** Um `DEVICE` gera zero ou mais `COREDUMPS`.
  * **Justificativa (1:N):** Ao longo de sua vida útil, um dispositivo pode falhar várias vezes. Este relacionamento permite manter um histórico completo de todas as falhas ocorridas em um hardware específico, o que é útil para identificar problemas crônicos de hardware ou de uso.

#### `FIRMWARES ||--o{ COREDUMPS : "proveniente de"`

  * **Leitura:** Um `FIRMWARE` é a origem de zero ou mais `COREDUMPS`.
  * **Justificativa (1:N):** Essencial para a análise de software e interpretação do **coredump**. Este link direto permite agregar todas as falhas relacionadas a uma versão específica do firmware, respondendo a perguntas como: "A versão `v2.1.0` é mais ou menos estável que a `v2.0.0`?".

#### `CLUSTERS ||--|{ COREDUMPS : "pertence a"`

  * **Leitura:** Um `CLUSTER` agrupa um ou mais `COREDUMPS`.
  * **Justificativa (1:N):** Este é o relacionamento que materializa a classificação. Um tipo de erro específico (o Cluster) pode acontecer repetidamente em diferentes dispositivos e versões de firmware. Agrupar todos esses eventos de **coredump** sob um único cluster permite quantificar a frequência e priorizar a correção dos bugs mais impactantes.

### Fluxo de Dados

1.  **Registro de Firmware/Dispositivo:** Um novo `FIRMWARE` é cadastrado. Um `DEVICE` é registrado e associado a uma versão de firmware.
2.  **Recebimento do Coredump:**
      * Um dispositivo envia um coredump.
      * O sistema cria uma nova entrada na tabela `COREDUMPS`.
      * Ele preenche o `device_id` e o `firmware_id` com base nas informações do dispositivo que enviou.
      * O campo `cluster_id` é deixado como `NULL`.
      * O arquivo do coredump e o *ELF* do `FIRMWARE` gerador, são utilizados para interpretação 
      * O coredump interpretado é salvo em disco e o caminho é armazenado em `file_path`.
3.  **Classificação:**
      * Um processo analisa o coredump recém-chegado.
      * Se ele corresponde a uma falha já conhecida, seu `cluster_id` é atualizado para o ID do `CLUSTER` existente.
      * Se for uma falha nova, um novo registro é criado em `CLUSTERS` e o `cluster_id` do coredump é atualizado com o ID do novo cluster.

---

## Visualização dos Dados (GUI)

???

--- 

## Arquitetura do Protótipo

O protótipo foi desenvolvido utilizando os seguintes componentes e tecnologias, com todos os scripts do back-end implementados em **Python 3:**

![Diagrama da Arquitetura do Protótipo Desenvolvido](docs/images/Arch_Prot_CoreDump_Extractor.png)

* **Microcontrolador e Firmware:** O firmware do dispositivo foi desenvolvido para o microcontrolador **ESP32** utilizando o framework **ESP-IDF v5.5.1**.

* **Protocolo de Conectividade:** O ESP32 oferece múltiplos protocolos de conectividade nativos (Bluetooth, Wi-Fi e Ethernet). Dentre eles, o **Wi-Fi** foi selecionado por facilitar o acesso à internet e a comunicação com o servidor.

* **Protocolo de Comunicação:** A comunicação entre o dispositivo e o servidor é feita via **MQTT**, um protocolo de mensageria leve e eficiente. Para a fase de prototipagem, foi utilizado o broker gratuito da **HiveMQ**.

* **Banco de Dados:** Para o armazenamento de metadados, foi selecionado o **SQLite3** devido à sua simplicidade e fácil integração com Python, operando sem a necessidade de um servidor dedicado.

* **Análise e Clusterização:** O agrupamento e a análise dos *coredumps* são realizados com a biblioteca `Damicorepy`, uma implementação da metodologia **DAMICORE**.

* **Armazenamento dos Coredumps:** Os arquivos de *coredump* são mantidos em uma estrutura de diretórios no sistema de arquivos. Essa abordagem foi escolhida para evitar o armazenamento de dados binários grandes (`BLOBs`) no SQLite3, prevenindo problemas de performance e sobrecarga do banco.

### ESP32

A escolha do ESP32 para a Prova de Conceito (PoC) se baseia em três fatores principais:

* **Relevância de Mercado:** É um dos microcontroladores com Wi-Fi mais utilizados no mundo, fabricado pela líder de mercado Espressif, tornando a solução aplicável a um vasto ecossistema de dispositivos.
* **Custo-Benefício e Recursos:** Integra conectividade Wi-Fi e Bluetooth a um baixo custo, sendo uma plataforma ideal para projetos de IoT.
* **Ecossistema Robusto:** Possui vasta documentação, uma grande comunidade de desenvolvedores e ferramentas maduras como o ESP-IDF, que agilizam o desenvolvimento.

Apesar de a implementação prática usar o ESP32, a arquitetura da solução é agnóstica ao hardware, e toda a metodologia pode ser portada para outros microcontroladores.

#### Hardware

O projeto utiliza a placa de desenvolvimento ESP32-DevKitC V4, equipada com o módulo ESP32-WROOM-32U. Embora este módulo seja classificado pela Espressif como "Não Recomendado para Novos Projetos (NRND)" devido à existência de chips mais modernos, sua escolha para este TCC foi justificada por dois fatores principais:

* **Disponibilidade:** Foi o hardware acessível para a realização do trabalho.

* **Suficiência Técnica:** Seus recursos de processamento e conectividade Wi-Fi atendem plenamente a todos os requisitos do projeto.

#### Framework

O firmware foi desenvolvido com o ESP-IDF v5.5.1, o framework oficial da Espressif. Ele foi escolhido em vez de plataformas mais abstratas, como o Arduino Core, pela necessidade de controle de baixo nível sobre os recursos do hardware, incluindo o sistema operacional de tempo real (FreeRTOS) e as pilhas de comunicação otimizadas.

A versão v5.5.1 foi selecionada por estar, na época do desenvolvimento, em seu "Período de Serviço" conforme a política de suporte da Espressif. Esta é a fase em que o fabricante recomenda o uso para novos projetos, garantindo uma plataforma estável, com suporte ativo a correções críticas e sem alterações que quebrem a compatibilidade.

### MQTT

A Prova de Conceito utiliza MQTT sobre uma rede Wi-Fi. As escolhas foram baseadas nos seguintes motivos:

* **Wi-Fi:** Selecionado pela conveniência de usar a infraestrutura de rede local já existente no ambiente de desenvolvimento e pela compatibilidade nativa com o ESP32.
* **MQTT:** Adotado por ser um padrão de fato em aplicações IoT, oferecendo vantagens cruciais:
  - **Leveza:** Baixo consumo de banda e recursos, ideal para microcontroladores.
  - **Modelo Publish/Subscribe:** Permite criar uma arquitetura de software desacoplada e facilmente escalável.
  - **Ecossistema Maduro:** Grande disponibilidade de bibliotecas e suporte da comunidade, o que acelera o desenvolvimento.

#### Funcionamento do MQTT no Contexto de Coredumps

No nosso sistema, o MQTT funciona com base em três componentes principais:

  * **Publisher (Publicador):** A **ESP32**, que envia o coredump após uma falha.
  * **Subscriber (Assinante):** O **Receptor**, o serviço responsável por coletar, armazenar e processar os coredumps.
  * **Broker (Servidor):** O servidor central que gerencia e distribui as mensagens.

![Fluxograma de Funcinamento do MQTT](docs/images/Fluxogram_MQTT.png)

O fluxo, como demonstrado no diagrama acima, ocorre em três passos simples:

1.  **Assinatura (Subscribe):** O **Receptor** (Subscriber) se conecta ao Broker e se inscreve no tópico `coredump/#`. O caractere `#` é um coringa (*wildcard*) que indica o interesse em receber todas as mensagens de tópicos que comecem com `coredump/`, independentemente do ID do dispositivo.

2.  **Publicação (Publish):** Quando uma falha ocorre, a **ESP32** (Publisher) envia os dados do coredump para um tópico específico que a identifica, como `coredump/ESP-341A`, entregando a mensagem ao Broker. A ESP32 não precisa saber onde o Receptor está ou quem ele é.

3.  **Distribuição:** O Broker recebe o coredump, verifica que o tópico `coredump/ESP-341A` corresponde à assinatura `coredump/#` do Receptor, e então encaminha a mensagem diretamente para ele.

A principal vantagem é que o **Broker desacopla** os componentes: a ESP32 em campo não precisa conhecer o endereço ou a implementação do Receptor, e vice-versa. Isso torna o sistema de extração de coredumps muito mais flexível e escalável, permitindo adicionar novos dispositivos ou novas instâncias do Receptor sem reconfigurar todo o sistema.

#### Broker HiveMQ

Para a implementação de referência, foi utilizado o broker público e gratuito da HiveMQ. A escolha desta solução se deu por ser uma plataforma robusta, confiável e amplamente utilizada no mercado, o que simplificou a configuração do ambiente e acelerou o desenvolvimento do protótipo.

### Receptor (`subscriber.py`)

O `subscriber.py` corresponde ao componente receptor da arquitetura geral, tendo como papel fundamental reconstruir e iniciar processar os coredumps enviados pelos dispositivos ESP32. Para isso, ele atua como um cliente MQTT que escuta tópicos específicos, gerencia as sessões de transferência de dados particionados e orquestra a análise e o registro final dos coredumps no banco de dados.

#### Principais Responsabilidades

* **Conexão MQTT Segura:** Estabelece uma conexão segura (TLS) com o broker MQTT para receber os dados dos dispositivos.
* **Gerenciamento de Sessão:** Inicia e monitora uma sessão para cada coredump em transferência, rastreando as partes recebidas e o total esperado.
* **Reconstrução de Coredumps:** Agrega as múltiplas partes de um coredump, que são enviadas em mensagens MQTT separadas, para remontar o arquivo binário original.
* **Processamento Assíncrono:** Após a reconstrução, dispara um processo de análise em uma thread separada para não bloquear o recebimento de novos dados. Este processo utiliza o `coredump_interpreter.py` para gerar um relatório legível.
* **Persistência de Dados:** Interage com o `db_manager.py` para salvar o caminho do arquivo de coredump bruto, o relatório de análise e associá-los ao dispositivo e firmware correspondentes.
* **Robustez e Limpeza:** Implementa um mecanismo de *timeout* para descartar sessões incompletas, evitando o consumo indefinido de memória por transferências que nunca terminam.

#### Fluxo de Execução

1.  **Início da Sessão:** O subscriber escuta o tópico `coredump/#`. Uma transferência é iniciada quando uma mensagem JSON é recebida no tópico `coredump/<MAC_DO_DISPOSITIVO>`, contendo o número total de partes esperadas (ex: `{"parts": 15}`). Nesse momento, uma nova `CoreDumpSession` é criada.

2.  **Recebimento das Partes:** O dispositivo envia cada parte do coredump em um tópico sequencial, como `coredump/<MAC>/0`, `coredump/<MAC>/1`, etc.
    * O script possui uma heurística para detectar e decodificar automaticamente partes que possam ter sido enviadas em formato **Base64**, garantindo flexibilidade no firmware do dispositivo.

3.  **Montagem e Salvamento:** Quando todas as partes esperadas são recebidas, a classe `CoreDumpAssembler` ordena as partes e as une, formando o arquivo binário completo do coredump. O arquivo é salvo no diretório configurado (padrão: `db/coredumps/raws/`) com um nome padronizado, incluindo a data, hora e o MAC do dispositivo (ex: `2025-10-05_19-30-00_AABBCCDDEEFF.cdmp`).

4.  **Análise e Registro (em background):**
    * Imediatamente após salvar o arquivo, uma nova thread é iniciada para a etapa de análise.
    * O script consulta o banco de dados (`db_manager`) para obter o tipo de chip (ex: `esp32s3`) e o caminho para o arquivo ELF do firmware associado ao dispositivo.
    * Com essas informações, ele invoca o interpretador por meio do `generate_coredump_report_docker` que retorna o *path* do arquivo resultante.
    * Finalmente, o `subscriber` chama a função `db_manager.add_coredump` para registrar o coredump no banco de dados, salvando os caminhos para o arquivo bruto e o relatório gerado.

#### Configuração

O comportamento do subscriber pode ser ajustado por meio de variáveis de ambiente, o que facilita sua implantação e configuração em diferentes ambientes (desenvolvimento, produção).

| Variável de Ambiente          | Descrição                                                               | Valor Padrão                                  |
| ----------------------------- | ----------------------------------------------------------------------- | --------------------------------------------- |
| `MQTT_HOST`                   | Endereço do broker MQTT.                                                | (Broker URI hardcoded)                          |
| `MQTT_PORT`                   | Porta do broker MQTT.                                                   | `8883`                                        |
| `MQTT_USER`                   | Nome de usuário para autenticação.                                      | (Usuário MQQT Hardcoded)                                |
| `MQTT_PASS`                   | Senha para autenticação.                                                | (Senha MQTT hardcoded)                             |
| `MQTT_BASE_TOPIC`             | Tópico raiz para escutar os coredumps.                                  | `coredump`                                    |
| `COREDUMP_TIMEOUT_SECONDS`    | Tempo em segundos para descartar uma sessão de coredump incompleta.      | `600` (10 minutos)                            |
| `COREDUMP_RAWS_OUTPUT_DIR`    | Diretório para salvar os arquivos binários de coredump reconstruídos.    | `db/coredumps/raws`                           |
| `COREDUMP_REPORTS_OUTPUT_DIR` | Diretório para salvar os relatórios de análise de coredump.             | `db/coredumps/reports`                        |
| `COREDUMP_ACCEPT_BASE64`      | Habilita (`1`) ou desabilita (`0`) a tentativa de decodificação de Base64. | `1`                                           |

### Interpretador (`coredump_interpreter.py`)

O `coredump_interpreter.py` corresponde ao componente "Interpretador" da arquitetura geral deste projeto. Ele foi projetado para automatizar a análise de arquivos de coredump brutos (.cdmp) gerados por um ESP32, utilizando o Docker para criar um ambiente consistente e isolado com as ferramentas do ESP-IDF. Essa abordagem elimina a necessidade de instalar a toolchain de desenvolvimento localmente para realizar a interpretação.

O script recebe como entrada um arquivo de coredump bruto e o arquivo ELF correspondente do firmware que estava em execução. Ele então invoca o utilitário `esp-coredump` dentro de um contêiner Docker para gerar um relatório legível, que inclui o backtrace (pilha de chamadas) no momento da falha, o registro dos registradores e a causa do erro.

#### Principais Funcionalidades

  * **Integração com Docker:** Utiliza uma imagem Docker específica do ESP-IDF (`espressif/idf:v5.5.1`) para garantir que a análise seja consistente e reprodutível, independentemente do ambiente da máquina hospedeira.
  * **Processamento Automatizado:** Orquestra a execução do `esp-coredump` com os parâmetros corretos, montando os arquivos necessários (coredump e ELF) como volumes somente leitura dentro do contêiner.
  * **Limpeza de Relatório:** O script processa a saída bruta do Docker, extraindo apenas o conteúdo relevante do relatório de coredump (delimitado por `ESP32 CORE DUMP START` e `ESP32 CORE DUMP END`) para gerar um arquivo de texto limpo e focado.
  * **Suporte a Múltiplos Chips:** Permite a especificação do tipo de chip (ex: `esp32`), o que adiciona o respectivo ROM ELF ao comando de análise, resultando em backtraces mais detalhados que podem incluir funções da ROM interna do microcontrolador.
  * **Tratamento de Erros Robusto:** Inclui verificação de existência dos arquivos de entrada, tratamento de falhas no comando Docker e um timeout para evitar que o processo fique travado indefinidamente.

#### Como Funciona

O fluxo de execução do script é o seguinte:

1.  **Validação:** Verifica se os caminhos para o arquivo coredump, o arquivo ELF e o diretório de saída existem.
2.  **Construção do Comando:** Monta dinamicamente o comando `docker run`. Ele mapeia os arquivos locais para o diretório `/app` dentro do contêiner.
3.  **Execução:** Executa o contêiner Docker, que por sua vez executa o comando `esp-coredump info_corefile` com os arquivos mapeados.
4.  **Captura e Extração:** Captura a saída padrão (`stdout`) do contêiner. Procura pelos marcadores de início e fim do relatório para extrair apenas a análise do coredump.
5.  **Salvamento:** Salva o relatório limpo em um arquivo `.txt` no diretório de saída especificado. O nome do arquivo de saída é derivado do nome do arquivo de coredump de entrada.
6.  **Retorno:** Retorna o `Path` do arquivo de relatório que foi salvo.

#### Função `generate_coredump_report_docker`

Esta é a função principal do script, responsável por orquestrar todo o processo de interpretação do coredump utilizando um contêiner Docker.

```python
def generate_coredump_report_docker(
    coredump_path: Union[str, Path],
    elf_path: Union[str, Path],
    output_dir: Union[str, Path],
    docker_image: str = "espressif/idf:v5.1.2",
    chip_type: str = None
) -> Path:
```

**Descrição:**

A função executa o utilitário `esp-coredump` de forma isolada, garantindo que as dependências corretas do ESP-IDF estejam presentes através da imagem Docker. Ela automatiza a montagem dos arquivos necessários, a execução do comando de análise, a captura do resultado e a limpeza do relatório final.

**Parâmetros:**

  * `coredump_path` (`Union[str, Path]`): O caminho completo para o arquivo de coredump bruto (`.cdmp`) a ser analisado.
  * `elf_path` (`Union[str, Path]`): O caminho completo para o arquivo `.elf` do firmware que gerou o coredump. Este arquivo contém os símbolos de depuração necessários para traduzir os endereços de memória em nomes de funções e linhas de código.
  * `output_dir` (`Union[str, Path]`): O diretório de destino onde o relatório de texto (`.txt`) gerado será salvo.
  * `docker_image` (`str`, opcional): A tag da imagem Docker a ser utilizada para a análise. O valor padrão é `"espressif/idf:v5.1.2"` para garantir a reprodutibilidade.
  * `chip_type` (`str`, opcional): Uma string que define o chip específico (ex: `"esp32"`). Se este parâmetro for fornecido, a função adiciona o ELF da ROM interna do chip ao comando de análise, o que pode resultar em um backtrace mais completo.

**Retorna:**

  * `Path`: Em caso de sucesso, a função retorna um objeto `pathlib.Path` que aponta para o arquivo de relatório (`.txt`) recém-criado.

**Levanta (Raises):**

  * `FileNotFoundError`: Se o arquivo de coredump, o arquivo ELF ou o diretório de saída não forem encontrados nos caminhos especificados.
  * `CoreDumpProcessingError`: Uma exceção personalizada que é lançada se ocorrer qualquer um dos seguintes problemas durante o processamento:
      * O comando `docker` não for encontrado no PATH do sistema.
      * O contêiner Docker retornar um código de erro (execução falhou).
      * A execução do contêiner demorar mais do que o tempo limite definido (atualmente 120 segundos).

### Clusterizador

Na arquitetura geral do **CoreDump Extractor**, o componente denominado **Clusterizador** é responsável por agrupar coredumps semelhantes de forma não supervisionada. Esta funcionalidade é implementada através da colaboração de dois scripts principais: o `coredump_clustering.py` e o `cluster_sincronyzer.py`.

1.  **`coredump_clustering.py` (O Analista):** Este script executa a análise e o agrupamento dos coredumps. Ele utiliza a *Damicorepy* para processar os dados brutos e gerar um arquivo de saída (e.g., `clusters.csv`) com a proposta da nova organização dos clusters. Ele é o responsável por decidir "quais coredumps pertencem a qual grupo".

2.  **`cluster_sincronyzer.py` (O Sincronizador):** Este script atua na sequência, pegando o resultado gerado pelo analista e o reconciliando com o estado atual armazenado no banco de dados. Sua função é persistir as informações de forma inteligente, preservando o histórico de clusters existentes e gerenciando o ciclo de vida dos agrupamentos.

Juntos, esses dois scripts formam uma solução completa para a clusterização, onde o primeiro gera a inteligência e o segundo a gerencia e a torna persistente no sistema.

#### CoreDump Clusterizer

O `coredump_clusterizer.py` atua como o **orquestrador** do processo de análise. Sua principal responsabilidade é gerenciar o ciclo de execução da ferramenta de clusterização (DAMICORE), garantindo que ela seja executada de forma eficiente, isolada e apenas quando necessário. Ele serve como a ponte entre os coredumps brutos armazenados e o `Cluster Sincronyzer`, que precisa dos resultados da análise para atualizar o banco de dados.

Projetado para operar como um serviço de longa duração (daemon), este script monitora o sistema e inicia o processo de clusterização com base em um gatilho híbrido.

##### Principais Funcionalidades

O fluxo de trabalho do orquestrador é dividido nas seguintes etapas:

1.  **Verificação de Gatilho (Trigger):** Para otimizar recursos, a análise não é executada a cada novo coredump. O script só inicia o processo se uma de duas condições for atendida:
    * **Por Quantidade:** Um número mínimo de novos coredumps não clusterizados foi atingido.
    * **Por Tempo:** Um tempo máximo pré-definido passou desde a última execução bem-sucedida.

2.  **Criação de Snapshot:** Antes de iniciar a análise, o script cria um "snapshot" dos coredumps em um diretório temporário. Essa abordagem garante que a análise seja realizada em um conjunto de dados isolado e consistente, sem risco de interferir com os arquivos originais.

3.  **Execução da Análise em Contêiner:** A ferramenta DAMICORE é executada dentro de um **contêiner Docker**. Essa técnica garante um ambiente de execução padronizado e encapsulado, eliminando problemas de dependências e garantindo que a análise seja reproduzível. O orquestrador é responsável por iniciar o contêiner, montar os volumes de dados (com o snapshot) e capturar o arquivo de resultado, `clusters.csv`.

4.  **Delegação para o Sincronizador:** Após a conclusão bem-sucedida da análise e a geração do `clusters.csv`, este script **não interpreta os resultados**. Sua tarefa termina ao invocar o `cluster_sincronyzer.py`, passando o arquivo gerado como entrada. Nesse ponto, o Sincronizador assume a responsabilidade pela lógica de reconciliação e persistência no banco de dados.

5.  **Atualização de Estado e Limpeza:** Ao final, o script atualiza um arquivo de estado com o timestamp da execução (para o gatilho de tempo) e remove o diretório de snapshot temporário, deixando o sistema limpo para o próximo ciclo.

#### Cluster Sincronyzer

O `cluster_sincronyzer.py` atua como o orquestrador responsável por manter a base de dados de clusters de coredumps sempre atualizada e consistente. Enquanto o `coredump_clustering.py` é responsável por analisar os arquivos de coredump e gerar uma nova proposta de agrupamento, o `cluster_sincronyzer.py` é o módulo que implementa a lógica de "reconciliação": ele compara o estado atual dos clusters no banco de dados com o novo resultado e aplica as mudanças de forma inteligente e persistente.

O principal objetivo deste script é garantir a **estabilidade dos identificadores (IDs) dos clusters** ao longo do tempo. Em vez de simplesmente apagar a clusterização antiga e salvar a nova, ele reconhece clusters que "evoluíram" (ou seja, que mantiveram a maior parte de seus membros), preservando seu histórico e identidade.

##### Principais Funcionalidades

O fluxo de execução do sincronizador é dividido nas seguintes etapas:

1.  **Extração do Estado Atual:** Primeiramente, o script consulta o banco de dados para carregar a estrutura de clusters existente, mapeando cada `cluster_id` para um conjunto de `coredump_ids` que ele contém.

2.  **Carregamento e Tradução dos Novos Clusters:** O script lê o arquivo CSV gerado pelo `coredump_clustering.py`. Como este arquivo contém caminhos de arquivos (`raw_dump_path`), um passo crucial de "tradução" é realizado: cada caminho de arquivo é convertido para seu correspondente `coredump_id` numérico, consultando o banco de dados. Coredumps que estão no CSV mas não no banco de dados são ignorados.

3.  **Reconciliação de Clusters:** Esta é a lógica central do processo, implementada no módulo **`cluster_reconciler.py`**. Em vez de uma simples comparação de similaridade, o script executa uma análise sofisticada para classificar a **evolução** de cada cluster. Utilizando uma heurística mista que combina o **Índice de Jaccard** (para medir a semelhança geral) e o **Coeficiente de Sobreposição** (para detectar inclusões), o reconciliador compara cada cluster antigo com os novos. Com base em limiares pré-definidos, ele identifica e categoriza as seguintes transições:
    * **`Evolução Estável`:** O cluster manteve a grande maioria de seus membros. O ID do cluster antigo é mantido.
    * **`Crescimento`:** O cluster antigo foi quase totalmente absorvido por um novo cluster muito maior. O ID antigo também é preservado.
    * **`Divisão (Split)`:** Um cluster antigo se fragmentou em múltiplos clusters novos.
    * **`Fusão (Merge)`:** Vários clusters antigos se uniram para formar um único cluster novo.
    * **`Mudança Drástica`**, **`Novo`** ou **`Desaparecido`:** Classificações para clusters que sofreram grandes alterações, surgiram sem correspondência ou deixaram de existir.

    Essa abordagem permite que o sistema não apenas atualize o banco de dados, mas também compreenda *como* os agrupamentos de falhas estão mudando ao longo do tempo, preservando a identidade de clusters estáveis e gerenciando seu ciclo de vida de forma inteligente.

    **OBS:** A mudança drástica é aplicada no banco da mesma forma que uma evolução estável. Isso garante que o cluster antigo preserve seu ID e histórico, evitando que seja tratado como “desaparecido” e que o sistema crie um cluster totalmente novo sem necessidade.

4.  **Aplicação das Mudanças:** Com base nos resultados da reconciliação, o script executa as seguintes ações no banco de dados:
    * **DELETE:** Clusters que desapareceram são removidos.
    * **INSERT:** Novos clusters são criados. Um nome descritivo para cada novo cluster é gerado automaticamente a partir do nome do arquivo de um de seus coredumps membros.
    * **UPDATE:** As associações de todos os coredumps são atualizadas para refletir a nova estrutura de clusters, utilizando os IDs corretos (seja um ID antigo reutilizado ou um ID recém-criado).

5.  **Geração de Nomes para Novos Clusters:** Para facilitar a identificação humana, quando um novo cluster precisa ser criado, a função `gerar_nome_cluster_de_arquivo` extrai o nome do arquivo de um coredump representante, e utiliza o arquivo para gerar um nome do novo cluster (e.g., `Cluster_meu_arquivo_coredump`).

Ao final do processo, o banco de dados reflete a mais recente e precisa organização dos coredumps em clusters, preservando a identidade dos agrupamentos estáveis e gerenciando de forma controlada o ciclo de vida dos clusters.

### Banco de Dados (`db_manager.py`)

O db_manager.py corresponde ao componente "Banco de Dados" da arquitetura geral deste projeto. Sendo responsável pela persistência e o gerenciamento de todos os dados, centralizando as informações sobre firmwares, dispositivos, coredumps e os resultados do processo de clusterização. Ele atua como uma camada de abstração exclusiva, garantindo que todas as interações com o banco de dados ocorram de maneira controlada e segura.

A tecnologia escolhida foi o **SQLite 3**, um sistema de gerenciamento de banco de dados relacional que é leve, baseado em arquivo e não requer um servidor dedicado. Essa escolha se alinha perfeitamente aos requisitos do projeto, oferecendo robustez e simplicidade operacional.

#### Estrutura do Banco de Dados (Schema)

O `db_manager.py` é responsável por criar e gerenciar um schema com quatro tabelas principais, cujos relacionamentos garantem a integridade e a consistência dos dados:

1. **`firmwares`:** Armazena o registro de cada versão de firmware disponível no sistema.
    * `firmware_id`: Identificador único (Chave Primária).
    * `name`, `version`: Nome e versão que identificam unicamente um firmware.
    * `elf_path`: Caminho para o arquivo ELF correspondente, essencial para a análise do coredump.
2. **`devices`:** Mapeia os dispositivos físicos (microcontroladores ESP32) monitorados pelo sistema.
    * `mac_address`: Endereço MAC do dispositivo (Chave Primária).
    * `current_firmware_id`: Chave estrangeira que aponta para a versão de firmware atualmente instalada no dispositivo.
    * `chip_type`: Modelo do chip (ex: `ESP32-S3`).
3.  **`clusters`:** Representa os grupos ou "clusters" de falhas que foram identificados pelo sistema. Cada cluster agrupa coredumps com a mesma causa raiz.
    * `cluster_id`: Identificador único (Chave Primária).
    * `name`: Um nome descritivo para o cluster (ex: `Stack_Overflow_MQTT_Task`).
4. **`coredumps`:** Tabela central que armazena cada evento de coredump recebido. Ela conecta todas as outras entidades.
    * `coredump_id`: Identificador único (Chave Primária).
    * `device_mac_address`: Chave estrangeira que identifica o dispositivo que sofreu a falha.
    * `firmware_id_on_crash`: Chave estrangeira que aponta para a versão do firmware em execução no momento da falha.
    * `cluster_id`: Chave estrangeira (pode ser `NULL`) que associa o coredump a um cluster após a análise. Um valor `NULL` indica um coredump "não classificado".
    * `raw_dump_path` e `log_path`: Caminhos para os arquivos físicos do coredump e logs associados.
    * `received_at`: Timestamp de quando o coredump foi recebido.

##### Diagrama de Entidade-Relacionamento (DER)

O diagrama a seguir ilustra visualmente o relacionamento entre as quatro tabelas principais do banco de dados.

![Diagrama Entidade Relacionamento para um Banco de Dados Implementado](docs/images\DER_SQLite3_DB.png)

*Legenda: `PK` - Chave Primária, `FK` - Chave Estrangeira, `UK` - Chave Única, `||` - Exatamente um, `o{` - Zero ou mais, `|{` - Um ou mais.*

As linhas e seus símbolos descrevem como as tabelas se conectam, representando a lógica de negócio do sistema:

* `FIRMWARES ||--o{ DEVICES`: Um **Firmware** pode ser executado por **zero ou mais** **Dispositivos**. Um **Dispositivo**, por sua vez, executa exatamente **um** firmware atual.
* `FIRMWARES ||--o{ COREDUMPS`: Um **Firmware** pode ser a origem de **zero ou mais** **Coredumps** (uma versão estável pode nunca gerar falhas). Um **Coredump** sempre se origina de exatamente **uma** versão de firmware.
* `DEVICES ||--|{ COREDUMPS`: Um **Dispositivo** pode gerar **um ou mais** **Coredumps** ao longo de sua operação. Cada **Coredump** é obrigatoriamente gerado por exatamente **um** dispositivo.
* `CLUSTERS }o--|{ COREDUMPS`: Um **Cluster** agrupa **um ou mais** **Coredumps** semelhantes. Um **Coredump** pode pertencer a **zero ou um** **Cluster**, o que representa corretamente o estado de "não classificado" quando o `cluster_id` é nulo.

#### Principais Funcionalidades do Módulo

O `db_manager.py` foi projetado para garantir a robustez e a simplicidade na manipulação dos dados:

* **Integridade Referencial:** O uso de `PRAGMA foreign_keys = ON;` garante que o SQLite imponha as restrições de chave estrangeira. Isso impede, por exemplo, que um firmware seja deletado se ainda houver dispositivos ou coredumps associados a ele, prevenindo dados órfãos.
* **Abstração das Operações (CRUD):** O módulo fornece funções claras e diretas para Criar, Ler, Atualizar e Deletar registros em cada tabela (ex: `add_firmware`, `get_unclustered_coredumps`, `assign_cluster_to_coredump`). Isso evita que o restante da aplicação precise lidar diretamente com a sintaxe SQL.
* **Gerenciamento do Ciclo de Vida do Coredump:** As funções `add_coredump`, `get_unclustered_coredumps` e `assign_cluster_to_coredump` implementam o fluxo principal do sistema: um coredump é recebido e salvo como "não classificado" (`cluster_id` é `NULL`), fica disponível para análise e, por fim, é associado a um cluster.
* **Tratamento de Erros Centralizado:** Uma função auxiliar (`_execute_query`) centraliza a execução de comandos SQL, o gerenciamento de conexões e o tratamento de exceções comuns, como `sqlite3.IntegrityError`, tornando o código mais limpo e confiável.

### Scripts Auxiliares
#### Add Firmware/Device

Um Script simples para adição de firmwares e devices no banco de dados.
**OBS:** por enquanto feito somente para testes... (pode ser unificado ao visualizador)

#### Cluster Reconciler (`cluster_reconciler.py`)

O `cluster_reconciler.py` é o módulo responsável pela lógica de "reconciliação". Sua função é comparar o resultado de duas clusterizações — uma anterior ("antiga") e uma atual ("nova") — para entender como os agrupamentos de coredumps evoluíram ao longo do tempo.

O objetivo principal é preservar a identidade de clusters que permanecem estáveis ou que crescem, em vez de simplesmente descartar a organização antiga. Isso permite um acompanhamento histórico da frequência e do comportamento das falhas. Para alcançar essa análise, o reconciliador utiliza duas métricas principais combinadas com uma heurística de classificação.

##### Métricas Fundamentais

Para quantificar a relação entre um cluster antigo (conjunto $A$) e um novo (conjunto $B$), o script se baseia em duas métricas complementares:

1.  **Índice de Jaccard:** Mede a similaridade geral entre os dois conjuntos. Um valor alto (próximo de 1) indica que os dois clusters são compostos praticamente pelos mesmos coredumps.
    $$J(A,B) = \frac{|A \cap B|}{|A \cup B|}$$

2.  **Coeficiente de Sobreposição (Overlap):** Mede o quão completamente o menor conjunto está contido no maior. É ideal para identificar relações de superconjunto (superset), como o crescimento de um cluster. Um valor alto (próximo de 1) significa que o cluster menor foi quase que totalmente "absorvido" pelo maior.
    $$O(A,B) = \frac{|A \cap B|}{\min(|A|, |B|)}$$

##### Heurística de Classificação da Evolução

Ao combinar os resultados de Jaccard e Overlap com limiares pré-definidos, o reconciliador classifica a transição de cada cluster, fornecendo uma visão semântica das mudanças:

* **`Evolução Estável`:** Ocorre quando tanto o **Jaccard** quanto o **Overlap** são muito altos. Indica que o cluster permaneceu praticamente o mesmo entre as duas execuções.

* **`Crescimento`:** Identificado por um **Overlap** muito alto, mas um **Jaccard** menor. Isso acontece quando um cluster antigo está quase inteiramente contido em um novo cluster significativamente maior.

* **`Divisão (Split)`:** Um cluster antigo não tem uma correspondência forte com nenhum cluster novo individualmente. No entanto, a **união de vários clusters novos** consegue cobrir a maior parte dos membros do cluster antigo original.

* **`Fusão (Merge)`:** O inverso da divisão. Um novo cluster é formado majoritariamente pela **união de membros de vários clusters antigos** diferentes.

* **`Mudança Drástica`:** A ligação entre o cluster antigo e o novo é intermediária (Overlap e Jaccard moderados). Há uma continuidade parcial, mas a mudança foi muito grande para ser classificada como evolução ou crescimento.

* **`Desaparecido` ou `Novo`:** Um cluster é classificado assim quando não há nenhuma correspondência com similaridade relevante (Jaccard e Overlap baixos) no outro estado.

##### Funções Principais
###### Modo Simples (Apenas Jaccard)

Algoritmo:
1. Para cada cluster antigo, calcular Jaccard com todos os novos.
2. Selecionar a melhor correspondência (maior Jaccard).
3. Se a similaridade ≥ `LIMIAR_SIMILARIDADE` → evolução.
4. Novos não usados em nenhum mapeamento → "clusters novos".
5. Antigos sem correspondência acima do limiar → "desaparecidos".

Complexidade: \( O(N_{antigos} * N_{novos}) \). Conjuntos são `set`, logo interseção/uniao custam \( O(|A| + |B|) \), aceitável para tamanhos moderados.

###### Modo Misto (Jaccard + Overlap + Heurísticas)

Pipeline:
1. Pré-cálculo de métricas para cada par que tenha interseção não vazia.
2. Para cada cluster antigo, ordenar candidatos por `(overlap, jaccard, inter_size)`.
3. Aplicar regras hierárquicas:
   - Evolução Estável: `overlap ≥ 0.9` e `jaccard ≥ 0.7`.
   - Crescimento: `overlap ≥ 0.9` e `jaccard ≥ 0.4`.
   - Divisão: ausência de overlap alto; vários novos cobrem ≥ 80% do antigo.
   - Mudança Drástica: `overlap ≥ 0.5` e `jaccard ≥ 0.4` (sem critérios anteriores).
4. Fusão (passo separado): para cada novo, checar múltiplos antigos com `overlap ≥ 0.5` cobrindo ≥ 80% do novo.
5. Antigos não classificados → desaparecidos. Novos não citados → novos.

#### Cluster Name Generator