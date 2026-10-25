## Nomes: Bernardo Quaresma, Marcus Vinícius, Pedro Alves

> **Guia de estudo:** [Antecipe por dentro](https://claude.ai/artifact/DHCoJqc5EkXVUv1E4o1pE9): explica o projeto passo a passo, com diagramas e exercícios.

# Antecipe: validação e precificação de recebíveis

Solução para o desafio **Antecipe** do Hackathon Kanastra. Uma empresa envia um lote de notas (recebíveis) em CSV. A API confere cada nota contra as regras do fundo (`validadores_antecipe.json`) e devolve, para cada uma:

- **aprovada** ou **recusada**;
- o **motivo** da recusa, em linguagem simples;
- o **valor líquido** das aprovadas, por desconto simples;
- o **total a receber**.

A mesma validação está disponível **pela tela** (painel web) e **pela API** (FastAPI, com documentação Swagger).

- **Back-end:** Python + FastAPI (`main.py`)
- **Front-end:** um único arquivo HTML/JS sem dependências externas (`index.html`), entregue pela própria API
- **Desempenho:** o estoque de 100.554 notas é avaliado em cerca de 2 segundos no servidor

---

## Sumário

1. [Requisitos](#1-requisitos)
2. [Estrutura do repositório](#2-estrutura-do-repositório)
3. [Como rodar do zero](#3-como-rodar-do-zero)
4. [Variáveis de ambiente](#4-variáveis-de-ambiente)
5. [Usando o painel web](#5-usando-o-painel-web)
6. [Usando a API (Swagger e curl)](#6-usando-a-api-swagger-e-curl)
7. [Formato dos arquivos](#7-formato-dos-arquivos)
8. [Regras implementadas](#8-regras-implementadas)
9. [O que fica salvo e o que fica em memória](#9-o-que-fica-salvo-e-o-que-fica-em-memória)
10. [Medindo o desempenho](#10-medindo-o-desempenho)
11. [Solução de problemas](#11-solução-de-problemas)

---

## 1. Requisitos

| Item | Versão | Observação |
|---|---|---|
| Python | 3.10 ou mais novo | Testado com 3.13 (Linux) e 3.15 (Windows) |
| pip | o que vem com o Python | Instala as dependências |
| Navegador | Chrome, Edge ou Firefox atuais | Para o painel |
| Git | qualquer | Para clonar o repositório |
| VS Code | opcional | Extensão **Python** (Microsoft) recomendada |

Dependências Python (em `requirements.txt`):

| Pacote | Para quê |
|---|---|
| `fastapi` | Framework da API |
| `uvicorn` | Servidor que executa a API |
| `python-multipart` | Recebimento de arquivos (upload) |
| `sse-starlette` | Barra de progresso em tempo real (Server-Sent Events) |

Não é preciso banco de dados, Docker nem nenhum serviço externo.

---

## 2. Estrutura do repositório

```
.
├── main.py                      # API: leitura do CSV, motor de regras, precificação, jobs
├── index.html                   # Painel web (servido pela API em http://127.0.0.1:8000/)
├── requirements.txt             # Dependências Python
├── benchmark_antecipe.py        # Medição de desempenho (opcional)
├── README.md
├── .gitignore
│
├── validadores_antecipe.json    # Regras do ANTECIPE FIDC (fornecido pelo hackathon)
├── cnpj_situacao_mock.csv       # Base de situação cadastral dos CNPJs (fornecido)
├── estoque_antecipe.csv         # Estoque com 100.554 notas (fornecido)
├── resultado_esperado_100k.json # Gabarito do estoque (fornecido)
└── testes_antecipe.csv          # Lote pequeno (47 notas) com casos de cada regra
```

Arquivos **gerados ao usar a aplicação**, que não vão para o repositório (estão no `.gitignore`):

| Arquivo | Quem gera | Conteúdo |
|---|---|---|
| `.venv/` | você, no passo 3 | Ambiente virtual com as dependências |
| `__pycache__/` | Python | Cache de compilação |
| `cnpj_situacoes.pkl` | `POST /upload-situacoes` | Última base de situação enviada, salva para sobreviver a reinícios |
| `recebiveis_teste.pkl` | `POST /test-duplicates` | Histórico de chaves usado no teste de duplicidade |
| `logs/antecipe.log` | a API, a cada requisição | Logs estruturados (veja a [seção 4](#4-variáveis-de-ambiente)) |

---

## 3. Como rodar do zero

Os comandos abaixo valem para o terminal do VS Code (**Terminal → New Terminal**, ou Ctrl+`) ou para qualquer terminal aberto na pasta do projeto.

### 3.1. Baixar o projeto

```bash
git clone <URL-DO-REPOSITÓRIO>
cd <PASTA-DO-REPOSITÓRIO>
```

No VS Code, abra a pasta em **File → Open Folder…**.

### 3.2. Criar e ativar o ambiente virtual

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

> Se aparecer um erro de política de execução, rode uma vez
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` e tente de novo.

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

O começo da linha do terminal deve passar a mostrar `(.venv)`. No VS Code, se ele perguntar se quer usar o novo ambiente, responda **Yes**. Se não perguntar, use Ctrl+Shift+P → **Python: Select Interpreter** → `.venv`.

### 3.3. Instalar as dependências

```bash
pip install -r requirements.txt
```

### 3.4. Subir a API

```bash
python -m uvicorn main:app --reload
```

Deve aparecer:

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
INFO:     Application startup complete.
```

Deixe esse terminal aberto. Para parar o servidor, use **Ctrl+C**.

- `--reload` reinicia o servidor sozinho quando o `main.py` é salvo. É útil durante o desenvolvimento, mas a cada reinício as regras e os jobs em memória são perdidos (veja a [seção 9](#9-o-que-fica-salvo-e-o-que-fica-em-memória)). Para uma demonstração, prefira rodar sem `--reload`.
- Se a porta 8000 estiver ocupada: `python -m uvicorn main:app --port 8001`. Nesse caso, use a porta 8001 nos endereços abaixo.
- Usar `python -m uvicorn`, em vez de só `uvicorn`, garante que será usado o uvicorn do ambiente virtual.

### 3.5. Abrir

| Endereço | O que é |
|---|---|
| **http://127.0.0.1:8000/** | **Painel web**: a forma mais simples de usar e testar tudo |
| http://127.0.0.1:8000/docs | Swagger: documentação interativa da API |
| http://127.0.0.1:8000/redoc | ReDoc: a mesma documentação, em formato de leitura |
| http://127.0.0.1:8000/health | Verificação rápida: responde `{"status":"ok"}` |

> O painel também abre direto do disco (duplo clique no `index.html`) ou pelo
> Live Server do VS Code (porta 5500). Nesses casos, confira no topo da tela se o
> endereço da API é `http://127.0.0.1:8000` e clique em **Conectar**.
> Com o Live Server, crie `.vscode/settings.json` com
> `{ "liveServer.settings.ignoreFiles": [".vscode/**", "**/*.py", "**/*.pkl", "**/*.tmp", "**/*.csv", "**/*.json"] }`.
> Sem isso, a página recarrega sozinha quando a API grava os arquivos `.pkl`.

---

## 4. Variáveis de ambiente

Nenhuma variável é obrigatória, e o projeto não usa segredos, chaves de API nem arquivo `.env`.

| Variável | Padrão | Para quê |
|---|---|---|
| `CORS_ORIGINS` | `*` (qualquer origem) | Origens autorizadas a chamar a API pelo navegador quando o painel está em outro endereço. Separe várias por vírgula. |
| `LOG_LEVEL` | `INFO` | Nível mínimo dos logs (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_DIR` | pasta `logs` ao lado do `main.py` | Pasta onde fica o arquivo `antecipe.log` |

Exemplo, para restringir a um front-end publicado:

```powershell
# Windows (PowerShell)
$env:CORS_ORIGINS="https://meu-front.exemplo.com"
python -m uvicorn main:app
```

```bash
# macOS / Linux
export CORS_ORIGINS="https://meu-front.exemplo.com"
python -m uvicorn main:app
```

### Logs

A API grava logs estruturados em `logs/antecipe.log`: uma linha JSON por evento, com rotação automática (5 arquivos de até 5 MB). Exemplo:

```json
{"ts": "2026-10-24T20:17:20.552+00:00", "nivel": "INFO", "evento": "avaliacao_concluida", "request_id": "6b83cdc9a655", "modo": "job", "notas": 100554, "aprovadas": 85215, "barradas": 15339, "duracao_total_ms": 1890.4}
```

- **Sem dados do CSV:** os logs nunca registram CNPJs, nomes, números de documento, valores, datas ou nomes de arquivo. Registram só contagens, tamanhos, tempos, status e códigos de regra. Em erros de CSV, aparece apenas a quantidade de erros por coluna.
- **Erros inesperados:** só o tipo do erro e a pilha (`arquivo:linha:função`), sem a mensagem.
- **Correlação:** cada resposta da API traz o cabeçalho `X-Request-ID`, o mesmo `request_id` das linhas do log daquela requisição.

---

## 5. Usando o painel web

Abra **http://127.0.0.1:8000/**. No topo ficam o endereço da API (detectado sozinho), o botão **Conectar**, o seletor de tema (automático, claro ou escuro) e três indicadores:

- **Regras:** se há regras carregadas;
- **Base de situação:** quantos CNPJs estão carregados;
- **Resultado:** o último lote avaliado.

### Roteiro completo com os arquivos do hackathon

**1. Aba "1. Configuração"**

1. Em **Regras do fundo**, escolha `validadores_antecipe.json` e clique em **Enviar regras**. O topo passa a mostrar **"7 de 9 ativas"**.
2. Em **Base de situação cadastral**, escolha `cnpj_situacao_mock.csv` e clique em **Enviar base**. O topo passa a mostrar o número de CNPJs carregados.

**2. Aba "2. Lote de notas"**

1. Escolha `estoque_antecipe.csv`, clicando no campo ou arrastando o arquivo.
2. **Opcional:** clique em **Só validar a leitura do CSV** para conferir o arquivo sem avaliar. Aparecem o total de linhas e uma prévia, ou a tabela de erros (linha, coluna e motivo).
3. Deixe o modo **Com barra de progresso** e clique em **Avaliar notas**. A barra mostra o percentual, a etapa atual, quanto falta e o tempo estimado.
4. Quando terminar, a aba **Resultados** abre sozinha.

> **Opções desta aba:**
> - **Incluir detalhes técnicos:** acrescenta o resultado de cada regra em cada nota. A resposta fica bem maior; evite em arquivos grandes.
> - **Avaliação direta:** usa `POST /avaliar-recebiveis`, sem barra de progresso.
> - **Arquivos de regras e base só para este job:** permitem testar outra configuração sem trocar a que está carregada.

**3. Aba "3. Resultados"**

- **Indicadores:**
  - total a receber, em destaque;
  - notas analisadas, aprovadas e barradas;
  - valor de face, desconto e valor líquido.
- **Gráficos:**
  - **distribuição por status;**
  - **notas barradas por regra:** clicar numa barra filtra a tabela por aquela regra.
- **Tabela de notas:**
  - busca por texto;
  - filtros por status e por regra;
  - ordenação clicando no cabeçalho;
  - paginação de 25, 50, 100 ou 500 notas;
  - clicar numa linha mostra os detalhes da nota: motivos da recusa, simulação do preço e, se pedidos, os detalhes técnicos.
- **Exportar:**
  - CSV (com o filtro atual);
  - **formato do gabarito** (`linha`, `id`, `status`, `barrado_por`);
  - JSON completo.
- **Comparar com o resultado esperado:**
  1. escolha `resultado_esperado_100k.json` e clique em **Comparar**;
  2. o painel mostra quantas linhas são iguais e divergentes, uma tabela por regra e os tipos de divergência;
  3. **Só divergentes do gabarito** filtra a tabela para essas linhas.

**Resultado esperado com os arquivos do hackathon:** 100.554 notas, **85.215 aprovadas**, 15.339 barradas e total a receber de **R$ 406.209.532,51**. A comparação com o `resultado_esperado_100k.json` dá **100.554 de 100.554 linhas iguais**.

**4. Aba "4. Duplicidade"**

Escolha um CSV de notas, ou deixe em branco para usar o da aba 2, e clique em **Testar duplicidade**. O teste usa a chave **cedente + sacado + número do documento + tipo** e mostra:

- as duplicatas dentro do próprio arquivo;
- as notas que já apareceram em envios anteriores (histórico em `recebiveis_teste.pkl`).

Rodando duas vezes o mesmo arquivo, a segunda execução aponta todas as notas como já vistas.

**5. Aba "5. Jobs"**

Consulta o status, acompanha o progresso ou busca o resultado de qualquer job pelo código. Também lista os jobs criados na sessão.

**6. Aba "6. Log da API"**

Mostra quais das 12 rotas já foram chamadas e com qual status, além do histórico das últimas requisições, com uma prévia de cada resposta.

### Lote pequeno para testes rápidos

O arquivo `testes_antecipe.csv` tem 47 notas com pelo menos um caso de cada regra: CNPJ inválido, tipo não aceito, valor abaixo do mínimo, nota vencida, prazo acima do máximo, concentração e situação cadastral. Use-o no lugar do estoque no passo 2 para ver todos os tipos de recusa em poucos segundos.

---

## 6. Usando a API (Swagger e curl)

### Ordem de uso

1. `POST /upload-rules`: envia o JSON de regras.
2. `POST /upload-situacoes`: envia a base de situação cadastral.
3. `POST /avaliar-recebiveis/jobs`, com barra de progresso, ou `POST /avaliar-recebiveis`, direto.

No Swagger (http://127.0.0.1:8000/docs), abra a rota, clique em **Try it out**, escolha o arquivo no campo `file` e clique em **Execute**.

### Rotas

| Método | Rota | Descrição |
|---|---|---|
| GET | `/` | Painel web (`index.html`) |
| GET | `/health` | Verificação de funcionamento |
| POST | `/upload-rules` | Carrega o JSON de regras (campo `file`) |
| GET | `/rules` | Regras carregadas (404 se nenhuma) |
| POST | `/upload-situacoes` | Carrega a base `DOC;SITUACAO_CADASTRAL` (campo `file`) |
| GET | `/situacoes` | Resumo da base carregada (404 se nenhuma) |
| POST | `/parse-csv` | Só lê e valida o CSV, sem avaliar |
| POST | `/avaliar-recebiveis?detalhes=false` | Avalia o lote e devolve o resultado na hora |
| POST | `/avaliar-recebiveis/jobs?detalhes=false` | Inicia a avaliação em segundo plano e devolve `job_id` (202). Aceita também `regras` e `situacoes`, válidos só para esse job |
| GET | `/jobs/{job_id}` | Status pontual do job |
| GET | `/jobs/{job_id}/progresso` | Progresso em tempo real (SSE): eventos `progresso`, `concluido` e `erro` |
| GET | `/jobs/{job_id}/resultado` | Resultado final (409 enquanto não termina) |
| POST | `/test-duplicates` | Teste de duplicidade com histórico local |

### Exemplo com curl

No Windows, use `curl.exe` em vez de `curl` no PowerShell.

```bash
curl -F "file=@validadores_antecipe.json" http://127.0.0.1:8000/upload-rules
curl -F "file=@cnpj_situacao_mock.csv"    http://127.0.0.1:8000/upload-situacoes

# Avaliação direta
curl -F "file=@testes_antecipe.csv" http://127.0.0.1:8000/avaliar-recebiveis

# Avaliação com job (barra de progresso)
curl -F "file=@estoque_antecipe.csv" http://127.0.0.1:8000/avaliar-recebiveis/jobs
#   -> {"job_id": "abc123...", ...}
curl -N http://127.0.0.1:8000/jobs/abc123.../progresso   # acompanha em tempo real
curl    http://127.0.0.1:8000/jobs/abc123.../resultado   # resultado final
```

### Formato da resposta (resumido)

```json
{
  "status": "success",
  "fundo": {"nome": "ANTECIPE FIDC", "cnpj": "92.234.400/0001-80"},
  "resumo": {
    "total_notas": 100554,
    "aprovadas": 85215,
    "barradas": 15339,
    "pendentes": 0,
    "barradas_por_regra": {"TIPO_RECEBIVEL": 9842, "...": 0},
    "valor_nominal_total": {"centavos": 60493171169, "reais": "604931711,69"},
    "total_a_receber_formatado": "R$ 406.209.532,51"
  },
  "notas": [
    {"linha": 2, "id": "725714", "status": "APROVADO",
     "cedente": "NLR INDÚSTRIA DE ESTOFADOS LTDA ME", "sacado": "TECIDOS SANTARÉM LTDA ME",
     "doc_sacado": "22.144.961/0001-02", "tipo_recebivel": "Duplicata", "vencimento": "2026-10-07",
     "valor_nominal": {"centavos": 227808, "reais": "2278,08"},
     "desconto": {"centavos": 24755, "reais": "247,55"},
     "valor_liquido": {"centavos": 203053, "reais": "2030,53"}},
    {"linha": 3, "id": "1-759172", "status": "BARRADO",
     "barrado_por": ["CONCENTRACAO_MAXIMA_SACADO"],
     "motivos": ["As notas deste cliente somam 12,50% do total enviado para o fundo, acima do limite de 10% que o fundo aceita para um mesmo grupo (mesma raiz de CNPJ)."]}
  ]
}
```

Cada nota traz `linha`, `id`, `status` e `barrado_por`, no mesmo formato do `resultado_esperado_100k.json`. Traz também `motivos`, os dados da nota e os valores. Dinheiro sempre vem em centavos inteiros e em texto (`"reais"`).

---

## 7. Formato dos arquivos

**CSV de notas** (mesmo formato do `estoque_antecipe.csv`):

- **Codificação e separador:** UTF-8 (com ou sem BOM), separador `;`, cabeçalho na primeira linha.
- **Valores:** decimal com vírgula (`1234,56` ou `1.234,56`), com no máximo 2 casas.
- **Datas:** `dd/mm/aaaa`.
- **Colunas obrigatórias:** `NOME_FUNDO`, `DOC_FUNDO`, `NOME_CEDENTE`, `DOC_CEDENTE`, `NOME_SACADO`, `DOC_SACADO`, `NU_DOCUMENTO`, `TIPO_RECEBIVEL`, `VALOR_NOMINAL`, `DATA_VENCIMENTO_ORIGINAL`, `DATA_AQUISICAO`.
- **Erros:** um arquivo com erro devolve 422 com a lista de até 100 problemas (linha, coluna, valor e motivo).

**Base de situação cadastral:** CSV com as colunas `DOC;SITUACAO_CADASTRAL`, por exemplo `01.000.171/0001-83;ATIVA`.

**Regras:** JSON no formato do `validadores_antecipe.json`. Cada validador tem `codigo`, `ativo` e `parametros`. A precificação fica em `precificacao` (`metodo`, `taxa_mensal_percentual`, `base_dias`).

---

## 8. Regras implementadas

Todos os limites vêm do JSON de regras: nada fica fixo no código. Regras com `"ativo": false` são ignoradas.

| Código | O que verifica | Parâmetro |
|---|---|---|
| `CNPJ_SACADO_VALIDO` | CNPJ do sacado com 14 dígitos e dígitos verificadores válidos (módulo 11) | — |
| `SITUACAO_CADASTRAL_SACADO` | Situação do sacado na base enviada está entre as aceitas | `situacoes_aceitas` |
| `TIPO_RECEBIVEL` | Tipo da nota está entre os aceitos | `tipos_aceitos` |
| `VALOR_MINIMO` | Valor nominal ≥ mínimo | `valor_minimo` |
| `NAO_VENCIDA` | Vencimento não é anterior à data de aquisição | — |
| `PRAZO_MAXIMO` | Dias entre aquisição e vencimento ≤ máximo | `prazo_maximo_dias` |
| `CONCENTRACAO_MAXIMA_SACADO` | Soma das notas de um mesmo grupo de sacados (raiz de 8 dígitos do CNPJ), dentro de cada fundo, ≤ percentual máximo do total do fundo | `percentual_maximo` |
| `PRAZO_MINIMO` | Dias entre aquisição e vencimento ≥ mínimo | `prazo_minimo_dias` |
| `CONCENTRACAO_MAXIMA_CEDENTE` | Igual à de sacado, agrupando por cedente | `percentual_maximo` |

**Precificação (desconto simples):**

```
desconto = valor_nominal × taxa_mensal × dias_até_vencimento / base_dias
valor_líquido = valor_nominal − desconto
```

Os cálculos usam inteiros (centavos), sem `float`, com arredondamento *half-up* aplicado uma única vez.

---

## 9. O que fica salvo e o que fica em memória

| Item | Onde fica | Após reiniciar o servidor |
|---|---|---|
| Regras (`/upload-rules`) | Memória | **Perdidas**: envie o JSON de novo |
| Base de situação (`/upload-situacoes`) | `cnpj_situacoes.pkl` | Recarregada sozinha |
| Histórico de duplicidade | `recebiveis_teste.pkl` | Mantido |
| Jobs e resultados | Memória (descartados 1 h depois de terminar) | **Perdidos**: crie o job de novo |

Por isso, logo após subir o servidor, é normal o terminal mostrar `GET /rules 404` (e `GET /situacoes 404` na primeira vez): o painel só está verificando o que já foi carregado.

---

## 10. Medindo o desempenho

O `benchmark_antecipe.py` mede, com `timeit`, o tempo de cada etapa do processamento: leitura do CSV, motor de regras, geração do JSON, job completo e, opcionalmente, a chamada HTTP.

```bash
python benchmark_antecipe.py                 # usa os arquivos com os nomes padrão na pasta
python benchmark_antecipe.py --repeticoes 1  # mais rápido (cerca de 1 min)
python benchmark_antecipe.py --csv outro.csv --regras regras.json --situacoes base.csv
```

O teste pela API (`TestClient`) precisa do pacote `httpx` (`pip install httpx`). Sem ele, essa parte é pulada e o relatório avisa. O script não grava nada em disco.

---

## 11. Solução de problemas

| Sintoma | Causa provável e solução |
|---|---|
| `python` não é reconhecido (Windows) | Instale o Python marcando **"Add python.exe to PATH"**, ou use `py` no lugar de `python` |
| `No module named uvicorn` / `fastapi` / `sse_starlette` | O ambiente virtual não está ativo. Ative-o (passo 3.2) e rode `pip install -r requirements.txt` |
| `Form data requires "python-multipart"` | Mesma causa acima: falta instalar as dependências |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `address already in use` / porta ocupada | Já há um servidor rodando. Feche-o ou use `--port 8001` |
| Painel mostra **"Sem conexão"** | A API não está rodando, ou o endereço/porta no topo está errado. Corrija e clique em **Conectar** |
| "Carregue primeiro as regras" ao avaliar | O servidor reiniciou e as regras estavam só na memória. Envie o JSON de regras de novo |
| "Job não encontrado ou expirado" | O servidor reiniciou (por exemplo, pelo `--reload` ao salvar o `main.py`) ou o job passou de 1 h. Crie o job de novo |
| A página recarrega sozinha (Live Server) | Configure o `ignoreFiles` indicado na seção 3.5 |
| `http://127.0.0.1:8000/` responde 404 | O `index.html` precisa estar na mesma pasta do `main.py` |
| O navegador fica lento com o estoque inteiro | Desmarque **Incluir detalhes técnicos** |
