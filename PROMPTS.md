# Registro de prompts de IA

Projeto **Antecipe** (Hackathon Kanastra). Este arquivo lista, em **ordem cronológica**, todos os prompts usados com assistentes de IA durante o desenvolvimento, cada um seguido da descrição do que foi feito com a resposta.

Como ler:

- O texto do prompt aparece como foi enviado.
- Materiais colados junto com o prompt (código, saídas de teste, trechos de arquivos) aparecem só como uma nota *[Colado: ...]*, para o registro continuar legível. O texto bruto completo está no export original (`Prompts.txt`).
- Os prompts marcados com *(recuperado do histórico da conversa)* não estavam no export e foram reconstituídos a partir do histórico, na posição em que foram enviados.
- *(...)* indica um trecho longo omitido dentro do próprio prompt.
- O caminho local de um arquivo foi substituído por `[caminho local]`.

Total: **67 prompts**.

## Sumário

- Fase 1: Planejamento e arquitetura (prompts 1–10)
- Fase 2: API base: duplicidade e conversão de dados (prompts 11–21)
- Fase 3: Motor de regras, revisões e correções (prompts 22–37)
- Fase 4: Testes, barra de progresso e resultado final (prompts 38–46)
- Fase 5: Situação cadastral, painel web e acerto com o gabarito (prompts 47–54)
- Fase 6: Desempenho (prompts 55–57)
- Fase 7: Entrega: README, logs e visual (prompts 58–67)

---

## Fase 1: Planejamento e arquitetura

### 1. Contexto inicial do hackathon

**Prompt:**

> Você integra um time de desenvolvedores para uma competição hackathon para a empresa Kanastra. Você servirá como um assistente de desenvolvimento e de ideação para a criação do projeto. Em suas respostas, busque sempre dar instruções passo-a-passo de como executar cada tarefa, sendo conciso e claro. As informações a respeito de que o programa a ser desenvolvido consiste serão fornecidas em seguida. Agora, deixo em anexo o edital da competição. Temos a nossa disposição as linguagens de programação Python e Java, com os editores VS Code e NetBeans, sendo que este deve ser usado primariamente caso seja necessário desenvolver uma interface gráfica. Temos também à nossa disposição o software Xampp, caso seja necessário desenvolver um banco de dados com SQL.
>
> *[Anexos: edital do hackathon; texto do desafio com o formato do CSV; trecho do estoque_antecipe.csv; validadores_antecipe.json]*
> *[Colado: descrição do desafio e dicionário de colunas (108 linhas) e trecho do CSV (7 linhas)]*

**O que foi feito com a resposta:** A IA resumiu o desafio Antecipe (validar recebíveis contra regras configuráveis de um FIDC e calcular o desconto simples) e propôs um caminho passo a passo. A equipe escolheu Python + FastAPI no VS Code para a API, deixando a interface para depois.

### 2. Checklist de requisitos da API

**Prompt:**

> Já tenho venv configurado com fastapi instalado. Faça uma checklist dos requisitos funcionais e não funcionais da API, que será a primeira parte que implementaremos. Agora, deixaremos temporariamente a UX em segundo plano.

**O que foi feito com a resposta:** A IA listou os requisitos funcionais (upload de CSV e JSON, validação de cada nota, motivo da recusa, precificação, resposta pela API) e os não funcionais (desempenho, precisão nos valores, mensagens claras). A lista virou o roteiro da implementação em etapas.

### 3. Mock-up da API: leitura do CSV

**Prompt:**

> Faça um mock-up de como a api em fastapi deve se comportar, escrevendo um curto código em Python que possamos testar. Essa parte deve apenas lidar com o string handling e a extração de cada field.

**O que foi feito com a resposta:** A IA escreveu uma API FastAPI mínima com um endpoint que recebe o CSV (separador `;`, UTF-8) e extrai cada campo. O código foi testado localmente e funcionou.

### 4. Upload do JSON de regras e flags

**Prompt:**

> Até o momento, tudo está funcionando. Precisamos agora de uma maneira de fazer upload do JSON com as regras e transformar essas regras em flags booleanas que possam ser usadas para comparações. Não precisamos ainda validar os dados do CSV.

**O que foi feito com a resposta:** Foram implementados `POST /upload-rules`, que lê o JSON e transforma cada validador em uma flag ativo/inativo com seus parâmetros, e `GET /rules`, que mostra as regras carregadas. O CSV ainda não era validado.

### 5. Entendendo o requisito de arquitetura

**Prompt:**

> Explique o que isso significa:
> **Arquitetura:** como modelar um motor de regras que o gestor do fundo altere sem deploy, e como garantir que o mesmo recebível não seja vendido duas vezes (idempotência e duplicidade).

**O que foi feito com a resposta:** A IA explicou os dois pontos do requisito. As regras vêm de uma configuração (JSON) carregada em tempo de execução, por isso o gestor muda as regras sem novo deploy. Cada recebível precisa de uma chave única, para que a mesma nota não seja comprada duas vezes. Isso definiu duas decisões do projeto: regras lidas do JSON e controle de duplicidade por chave.

### 6. Banco de dados ou set do Python?

**Prompt:**

> Seria ideal:
> a) usar um banco de dados e implementar chave primária (mais complexo, menos conveciente)
> b) usar um set nativo do Python para remover duplicatas (eficiente, mais conveniente)
> c) algo diferente

**O que foi feito com a resposta:** A IA comparou as alternativas para detectar duplicatas: banco de dados com chave primária ou `set` do Python. A conversa seguiu para uma solução sem banco de dados.

### 7. Abordagem híbrida: set + pickle

**Prompt:**

> Seria possível uma abordagem híbrida usando um set do Python e ao invés de banco de dados usar serialização com pickle para persistência?

**O que foi feito com a resposta:** A IA confirmou que a abordagem funciona: um `set` em memória para checagem em tempo O(1) e serialização com `pickle` para manter o histórico entre execuções.

### 8. SQLite e Xampp

**Prompt:**

> O SQLite usaria o Xampp?

**O que foi feito com a resposta:** A IA esclareceu que o SQLite já vem embutido no Python e não depende do Xampp, que serve ao MySQL.

### 9. Prós e contras: SQLite × MySQL + Xampp

**Prompt:**

> Explique concisamente os pros e contras dessas duas abordagens (SQLite e MySQL + Xampp).

**O que foi feito com a resposta:** A IA fez uma comparação curta entre as duas opções: instalação, desempenho, concorrência e complexidade.

### 10. Qual opção é mais rápida?

**Prompt:**

> E se a prioridade for eficiência para analisar todo o CSV o mais rápido possível? Qual das 3 é melhor (set + pickle, SQLite ou MySQL + Xampp)?

**O que foi feito com a resposta:** A IA indicou `set` + `pickle` como a opção mais rápida para checar o CSV inteiro, por não ter consultas a banco. A equipe adotou essa decisão.

---

## Fase 2: API base: duplicidade e conversão de dados

### 11. Teste de duplicatas com set + pickle

**Prompt:**

> Com base no que já foi implementado (que já foi validado e funciona), implemente um teste de duplicatas usando set + pickle. Decida a melhor opção de serialização para persistência. Aqui está o nosso código até o momento:
>
> *[Colado: código do main.py até aquele momento (175 linhas)]*

**O que foi feito com a resposta:** Foi criado o endpoint `POST /test-duplicates`. A chave é cedente + sacado + número do documento + tipo, com CNPJs normalizados. As chaves já vistas ficam em um `set` salvo com `pickle` em `recebiveis_teste.pkl`, com gravação atômica.

### 12. CSV de teste para a duplicidade

**Prompt:**

> Escreva um arquivo CSV com o teste a ser executado na nova seção e mostre o resultado esperado.

**O que foi feito com a resposta:** A IA gerou um CSV de 4 linhas (uma delas repetida) e o resultado esperado da primeira e da segunda execução.

### 13. Resultado do 1º teste de duplicatas

**Prompt:**

> (resultado do endpoint colado para conferência)
>
> *[Colado: resposta JSON de /test-duplicates (19 linhas)]*

**O que foi feito com a resposta:** O resultado bateu com o esperado: 4 linhas, 1 duplicata dentro do CSV e 3 chaves novas registradas no histórico.

### 14. Resultado do 2º teste: erro na contagem

**Prompt:**

> (resultado da segunda execução colado para conferência)
>
> *[Colado: resposta JSON de /test-duplicates (37 linhas)]*

**O que foi feito com a resposta:** Na segunda execução, as notas foram apontadas como já vistas, mas `chaves_unicas_neste_csv` veio 0 e a duplicata interna do CSV deixou de ser contada. A IA identificou o erro: uma chave encontrada no histórico não era registrada como vista no arquivo atual.

### 15. Correção da contagem de duplicatas

**Prompt:**

> Isso é em que função? Reescreva todo o código fazendo a correção.

**O que foi feito com a resposta:** A IA apontou a função `test_duplicates` e reescreveu o código completo com a correção: toda chave passa a ser registrada como vista no arquivo atual, esteja ou não no histórico.

### 16. Teste de duplicatas após a correção

**Prompt:**

> Segundo teste de duplicatas:
>
> *[Colado: resposta JSON de /test-duplicates (39 linhas)]*

**O que foi feito com a resposta:** O resultado ficou correto: 3 chaves únicas no CSV, 1 duplicata interna e 3 já presentes no histórico.

### 17. Como fazer o type casting

**Prompt:**

> Antes de prosseguirmos com isso, devemos cuidar do type casting na entrada dos dados CSV. Como devemos abordar isso?

**O que foi feito com a resposta:** A IA propôs converter cada campo para o tipo certo: textos sem espaços, datas `dd/mm/aaaa` em `date` e valor monetário em número exato. Erros seriam reportados por linha e coluna. Para dinheiro, sugeriu `Decimal`.

### 18. Float, Decimal ou algo mais rápido?

**Prompt:**

> Preciso priorizar eficiência acima de tudo. Há alguma maneira de usar o float com uma precisão maior por ser mais rápido ou uma outra solução mais simples e rápida que o Decimal?

**O que foi feito com a resposta:** A IA explicou que `float` tem erro de arredondamento em valores monetários e apresentou o *integer scaling* (guardar dinheiro como centavos inteiros) como alternativa exata e mais rápida que `Decimal`.

### 19. Implementação do integer scaling

**Prompt:**

> Para VALOR_NOMINAL, usaremos integer scaling para ganhar mais velocidade e ainda assim manter a precisão. Explique melhor essa abordagem e escreva todo o código de como faríamos com essa abordagem no type casting.

**O que foi feito com a resposta:** O type casting foi implementado. `VALOR_NOMINAL` é convertido direto dos dígitos para centavos (`1.234,56` → `123456`), sem `float`, e valores com mais de 2 casas são rejeitados. O `POST /parse-csv` passou a devolver `VALOR_NOMINAL_CENTAVOS`.

### 20. Teste do type casting

**Prompt:**

> Gere um teste para a nova funcionalidade.

**O que foi feito com a resposta:** A IA gerou um CSV de teste com valores e datas em formatos variados.

### 21. Resultado do teste de type casting

**Prompt:**

> (resultado do /parse-csv colado para conferência)
>
> *[Colado: resposta JSON de /parse-csv (58 linhas)]*

**O que foi feito com a resposta:** Os 4 recebíveis foram convertidos corretamente, com valores em centavos e datas válidas.

---

## Fase 3: Motor de regras, revisões e correções

### 22. Próximo passo: motor de regras

**Prompt:**

> Agora devemos fazer o motor de regras?

**O que foi feito com a resposta:** A IA confirmou que o próximo passo era o motor de regras e propôs a estrutura: uma verificação por validador do JSON, resultado por regra e status final de cada nota.

### 23. Motor de regras com todos os validadores

**Prompt:**

> No caso, mesmo as que estão inativas no JSON já devem ser implementadas para quando um JSON de teste eventualmente ativá-las. Reescreva todo o código com as modificações que combinamos.
>
> *[Colado: código do main.py (671 linhas)]*

**O que foi feito com a resposta:** O código foi reescrito com o motor de regras completo. Os 9 validadores estão implementados, inclusive os que estão inativos no JSON (`PRAZO_MINIMO`, `CONCENTRACAO_MAXIMA_CEDENTE`), que aparecem como `IGNORADA`. O status de cada nota é `ELEGIVEL`, `NAO_ELEGIVEL` ou `PENDENTE_VERIFICACAO`, a precificação usa desconto simples e foi criado o endpoint `POST /avaliar-recebiveis`.

### 24. Validação de CNPJ por 14 dígitos e módulo 11

**Prompt:**

> A validação será baseada em:
> a) haver 14 dígitos
> b) seguir o algoritmo de mod 11
> Reescreva todo o código novamente tendo isso em vista:
>
> *[Colado: código do main.py (672 linhas)]*

**O que foi feito com a resposta:** A validação de CNPJ foi reescrita para exigir 14 dígitos e conferir os dois dígitos verificadores pelo algoritmo módulo 11. A situação real na Receita não era consultada.

### 25. Correção para o código voltar a funcionar

**Prompt:**

> Conserte o código para que funcione mudando o mínimo possível (reescreva-o por completo para copy-paste):
>
> *[Colado: código do main.py (1376 linhas)]*

**O que foi feito com a resposta:** A IA fez correções mínimas no código colado e devolveu o arquivo completo para copiar e colar.

### 26. Revisão de código colada: dois bugs

**Prompt:**

> Encontrei **um bug crítico**, e há um segundo que só aparece dependendo do JSON de regras.
> ### 1. Erros de cabeçalho do CSV retornam 500 em vez de 400 (crítico)
> Em `parse_csv_records`, o `try` só captura `csv.Error`. Só que `csv_reader_from_text` avisa sobre problemas de cabeçalho com `ValueError`, e esse erro não é tratado em lugar nenhum. Isso vale para CSV sem cabeçalho, colunas duplicadas e colunas ausentes. O resultado é um **500 Internal Server Error** em `/parse-csv` e `/avaliar-recebiveis`.
>
> *[Colado: restante da revisão e código do main.py (463 linhas)]*

**O que foi feito com a resposta:** Uma revisão feita por IA foi colada junto com o código. Ela apontava erros de cabeçalho do CSV retornando 500 em vez de 400 e uma `precificacao` nula derrubando a avaliação. As duas correções foram aplicadas: `ValueError` passou a ser tratado como 400, e a precificação ausente ou nula passou a ser tolerada.

### 27. Resultado da avaliação de 8 recebíveis

**Prompt:**

> (resultado do /avaliar-recebiveis colado para conferência)
>
> *[Colado: resposta JSON de /avaliar-recebiveis (601 linhas)]*

**O que foi feito com a resposta:** As 8 notas foram barradas, principalmente por concentração máxima por sacado (num lote pequeno, cada cliente passa de 10% do total), além de CNPJ inválido, tipo não aceito, valor mínimo, vencimento e prazo.

### 28. CSV com metade aprovada e metade recusada

**Prompt:**

> Gere um csv novo como plain text para copy-paste com metade a ser rejeitado e metade aceito para teste.

**O que foi feito com a resposta:** A IA gerou um novo CSV de teste com metade das notas para aprovar e metade para recusar.

### 29. Resultado do novo CSV

**Prompt:**

> (resultado colado para conferência)
>
> *[Colado: resposta JSON de /avaliar-recebiveis (601 linhas)]*

**O que foi feito com a resposta:** As 8 notas continuaram barradas, porque a concentração reprova tudo em lotes muito pequenos. Ficou registrado como ajuste dos dados de teste: usar raízes de CNPJ distintas.

### 30. Resumo do que foi implementado

**Prompt:**

> Resuma em bullet-points curtos tudo que eu te pedi para implementar até agora e como foi feito isso.

**O que foi feito com a resposta:** A IA resumiu em tópicos o que havia sido implementado: estrutura da API, leitura do CSV, integer scaling, regras configuráveis, motor de regras, precificação, duplicidade e testes. O resumo foi reaproveitado no prompt 35.

### 31. Code review: só bugs críticos (v1)

**Prompt:**

> Você é um agente de code review, cujo objetivo é avaliar códigos de um projeto em desenvolvimento constante. Seu papel é apenas identificar pontos absolutamente críticos no código, ou seja, não mudar ou sugerir coisas que não sejam absolutamente necessárias. NÃO HÁ PROBLEMA SE NÃO HOUVER SUGESTÃO ALGUMA. Avalie o código abaixo e notifique quaisquer bugs CRÍTICOS antes de continuarmos o desenvolvimento, ou caso contrário, apenas dê okay para seguirmos.
>
> *[Colado: código do main.py (444 linhas)]*

**O que foi feito com a resposta:** A IA revisou o código buscando apenas bugs críticos e apontou os que precisavam ser corrigidos antes de continuar.

### 32. Code review da versão integral (v2)

**Prompt:**

> Resumo em uma frase: o projeto já tem uma arquitetura definida para uma API FastAPI que recebe CSV e JSON, converte dados financeiros com precisão em centavos, aplica nove regras configuráveis, calcula concentração e desconto simples, explica os resultados e oferece um endpoint de teste de duplicatas; a principal tarefa pendente é validar a versão integral do código com os arquivos reais e corrigir os problemas encontrados. Agora faça a proxima avaliação:
>
> *[Colado: código do main.py (1098 linhas)]*

**O que foi feito com a resposta:** Segunda revisão crítica, agora da versão integral do código, com a lista dos problemas que impediam o funcionamento.

### 33. Os erros já existiam antes?

**Prompt:**

> esses erros ja existiam antes?

**O que foi feito com a resposta:** A IA comparou as versões para separar os erros novos (regressões) dos que já existiam.

### 34. Comparação detalhada entre versões

**Prompt:**

> primeiro olhe se os erros que foram introduzidos foram corrigidos, depois compare oq ja havia sido implementado na primeira versao que te mandei e essas mesma coisa na versao mais atual e me fale exatemnte oq mudou e onde mudou
>
> *[Colado: código do main.py (1035 linhas)]*

**O que foi feito com a resposta:** A IA verificou se os erros introduzidos tinham sido corrigidos e listou exatamente o que mudou entre a primeira versão e a atual, e onde.

### 35. Correção guiada pela lista de funcionalidades

**Prompt:**

> Analise agora essa lista com o que tentamos implementar. Acabamos por modificar negativamente o código, e ele deve ser corrigido por você.
> (...)
> Você deve consertar o código mudando o mínimo possível e assegurando que ele apresenta uma estrutura como a descrita. Pontos importantes: as duplicatas + a serialização devem usar set + pickles e a precisão dos cálculos financeiros deverão usar integer scaling para melhor eficiência. Reescreva o código por completo para copy-paste.
>
> *[Colado: lista de funcionalidades (itens 1 a 9) e código de referência (1459 linhas)]*

**O que foi feito com a resposta:** O código foi corrigido com mudanças mínimas, seguindo a lista de funcionalidades: duplicatas com `set` + `pickle` e cálculos financeiros com integer scaling. O código completo foi reescrito.

### 36. Remover todo uso de Decimal

**Prompt:**

> Não quero que use Decimal nenhuma vez, pois precisamos maximizar ao máximo a eficiência. Apenas extraia o valor e converta para int usando integer scaling. Mude o mínimo possível, reescreva todo o código.

**O que foi feito com a resposta:** Todo uso de `Decimal` foi removido. Valores e percentuais do JSON passaram a ser convertidos direto em inteiros escalados (centavos; percentuais com 4 casas), o arredondamento half-up é feito só com inteiros e a concentração é comparada por multiplicação cruzada.

### 37. Explicação para leigos e medidas de eficiência

**Prompt:**

> Explique de modo geral para uma pessoa razoavelmente leiga o funcionamento desse código, especialmente listando todas as medidas adotadas para aumentar a eficiência e a velocidade (como usar set + pickle e integer scaling).

**O que foi feito com a resposta:** A IA explicou em linguagem simples como o código funciona e listou as medidas de eficiência: `set` + `pickle`, integer scaling, conversão sem `float`, validações em uma passada e cache.

---

## Fase 4: Testes, barra de progresso e resultado final

### 38. CSV único com todos os testes

**Prompt:**

> Agora, só vou fornecer algumas coisas para seu contexto. Use essas informações para gerar um único arquivo CSV com todos os testes para as funcionalidades implementadas. Eu executarei os testes e retornarei os resultados.
>
> *[Anexos: validadores_antecipe.json]*
> *[Colado: trecho do CSV real (8 linhas)]*

**O que foi feito com a resposta:** A IA gerou o `testes_antecipe.csv` (47 notas, com casos de cada regra e duplicatas) e o roteiro de execução no Swagger.

### 39. Saídas de todos os testes

**Prompt:**

> (saídas dos testes coladas para conferência)
>
> *[Colado: respostas JSON (6529 linhas)]*

**O que foi feito com a resposta:** Foram coladas as saídas de `/health`, `/parse-csv`, `/upload-rules`, `/rules`, `/avaliar-recebiveis` e da 1ª execução de `/test-duplicates`. A IA conferiu cada uma com o resultado esperado.

### 40. 2ª execução do teste de duplicatas

**Prompt:**

> (resultado colado para conferência)
>
> *[Colado: resposta JSON de /test-duplicates (80 linhas)]*

**O que foi feito com a resposta:** Na 2ª execução, as 46 chaves únicas apareceram como já vistas no histórico e a duplicata interna continuou detectada, como esperado.

### 41. Barra de progresso em tempo real

**Prompt:**

> Durante o processamento, ou seja, parse e type casting do CSV e do JSON e a execução do motor de regras para gerar os resultados, precisamos de uma barra de progresso que atualize em tempo real mostrando o que já foi feito e o quanto falta para acabar. Mantenha em mente que será feito futuramente o front-end em web, e que essa barra será mostrada nesse contexto. Decida a biblioteca que usaremos, mostre o comando que a instala e reescreva todo o código implementando essa barra de progresso.

**O que foi feito com a resposta:** A biblioteca escolhida foi a `sse-starlette` (Server-Sent Events), instalada com `pip install sse-starlette`. Foram implementados jobs em segundo plano: `POST /avaliar-recebiveis/jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/progresso` (SSE com percentual, etapa e quanto falta) e `GET /jobs/{id}/resultado`.

### 42. Resultado completo e base de situação cadastral

**Prompt:**

> Mais informações para te contextualizar:
> (...)
> ## Formato do CSV
> Separador `;`, decimal com vírgula, datas `dd/mm/aaaa`, UTF-8, cabeçalho na primeira linha. Data-base dos dados: October 22, 2026.
> Vou testar a barra depois, como você já fez um teste inicial, lembre-me no futuro. Temos a última coisa para implementar no back-end e uma alteração para fazer no código existente.
> a) última coisa: gerar todos os resultados pedidos da API no final de todo o processamento
> b) há um novo arquivo CSV que deve ter o upload, será uma base com os CNPJs e suas situações ativo ou não; ao invés de usar o algoritmo que estáva-mos usando, devemos fazer a verificação nesse arquivo, lembrando que a solução deve ser a mais eficiente POSSÍVEL, então implemente primeiro a parte de gerar todos os resultados e depois mostre as opções para essa modificação
> Reescreva todo o código para copy-paste.
>
> *[Anexos: resultado_esperado_100k.json; cnpj_situacao_mock.csv]*
> *[Colado: texto do desafio e requisitos mínimos de entrega (59 linhas no total)]*

**O que foi feito com a resposta:** O resultado final passou a ser completo: status `APROVADO`/`BARRADO` no formato do gabarito, `barrado_por`, motivos em linguagem simples, valor líquido de cada aprovada e total a receber. A IA apresentou as opções para checar a situação cadastral pelo novo CSV.

### 43. Como testar e como funciona o job

**Prompt:**

> Com base no CSV que você havia me dado para teste, me explique como e o que testar. Também não entendi como funciona a parte de job.

**O que foi feito com a resposta:** A IA explicou o roteiro de testes com o CSV e o fluxo do job: criar, acompanhar o progresso e buscar o resultado.

### 44. O que preencher no Swagger

**Prompt:**

> Explique o que devo inserir aqui para o teste com esse csv.
>
> *[Anexos: testes_antecipe.csv; print; print]*

**O que foi feito com a resposta:** A IA explicou campo a campo o que enviar no Swagger para criar o job com o CSV de testes.

### 45. Job criado

**Prompt:**

> (resposta da criação do job colada)
>
> *[Colado: resposta JSON (6 linhas)]*

**O que foi feito com a resposta:** O job foi criado com status `na_fila`. A IA explicou como acompanhar o progresso e buscar o resultado.

### 46. Resultado completo do job

**Prompt:**

> (resultado do job colado para conferência)
>
> *[Colado: resposta JSON de /jobs/{id}/resultado (1066 linhas)]*

**O que foi feito com a resposta:** Resultado do job: 47 notas, 34 aprovadas, 13 barradas e R$ 73.435,91 a receber. A IA conferiu com o esperado.

---

## Fase 5: Situação cadastral, painel web e acerto com o gabarito

### 47. Situação cadastral por dicionário

**Prompt:**

> Vamos usar o dicionário para implementar a checagem de CNPJs no CSV como enviado. Reescreva todo o código para copy-paste, execute testes você mesmo que isso eu testarei depois. Em seguida, iniciaremos o front-end.
>
> *[Anexos: print]*

**O que foi feito com a resposta:** A situação cadastral passou a ser checada por um dicionário `{CNPJ: situação}`, com busca O(1) e cache por sacado. A base é carregada do `cnpj_situacao_mock.csv` em `POST /upload-situacoes`, consultada em `GET /situacoes` e salva em `pickle`. A IA rodou os testes antes de entregar.

### 48. Front-end completo em um arquivo

**Prompt:**

> Agora, faça todo o front-end em um arquivo self-contained que se conecta com a API e tem todos os campos para upload e visualização dos dados e dos resultados. Ele deve ser feito de tal modo a possibilitar que testemos todas as funcionalidades do back-end melhor, ou seja, deve ser completo. Dê também instruções detalhadas de como conectar e rodar como VS Code. Pense passo a passo e verifique todo o seu raciocínio. Lembre-se que será para web, e rodaremos inicialmente localmente.

**O que foi feito com a resposta:** Foi criado o `index.html`: um painel em um único arquivo, sem dependências, com 6 abas cobrindo todos os endpoints (configuração, lote com barra de progresso, resultados com filtros, gráficos, exportação e comparação com o gabarito, duplicidade, jobs e log). A API passou a servir o painel em `/` e ganhou CORS. A IA deu instruções detalhadas para o VS Code e validou o painel com 72 testes automatizados no navegador.

### 49. Corrigir as divergências com o gabarito *(recuperado do histórico da conversa)*

**Prompt:**

> Conserte, rodando testes com os arquivos fornecidos.
>
> *[Anexos: print da comparação (6.399 divergentes); validadores_antecipe.json; cnpj_situacao_mock.csv; estoque_antecipe.csv; resultado_esperado_100k.json]*

**O que foi feito com a resposta:** A IA rodou o estoque real contra o gabarito e descobriu que a concentração é calculada por fundo (`DOC_FUNDO`), não pelo arquivo inteiro. Com a correção, as 3.305 notas barradas por concentração passaram a bater. As divergências restantes (3.094) eram só de situação cadastral, e a IA mostrou que vinham de uma base diferente do mock. Gerou também o `divergencias_situacao.csv`.

### 50. Reescrever só o que deu errado *(recuperado do histórico da conversa)*

**Prompt:**

> Reescreva todo o main.py para copy-paste, mudando exclusivamente o que havia dado errado:
>
> *[Colado: código do main.py (cerca de 2.400 linhas)]*

**O que foi feito com a resposta:** A IA aplicou ao código colado apenas a correção da concentração por fundo e avisou que essa versão não tinha as mudanças usadas pelo painel (CORS, rota `/` e etapas do job).

### 51. Arquivo completo com as mudanças do painel *(recuperado do histórico da conversa)*

**Prompt:**

> Reescreva o arquivo inteiro.

**O que foi feito com a resposta:** A IA entregou o `main.py` com a correção da concentração e com CORS, rota `/` e etapas do job, todos testados.

### 52. Log do terminal com 404 *(recuperado do histórico da conversa)*

**Prompt:**

> (log do uvicorn colado)
>
> *[Colado: linhas do terminal do uvicorn]*

**O que foi feito com a resposta:** A IA explicou que os 404 em `/rules` e `/situacoes` são normais: o painel só verifica o que já foi carregado. Também explicou a ordem de uso.

### 53. Verificar o problema da situação cadastral *(recuperado do histórico da conversa)*

**Prompt:**

> Verifique o problema.
>
> *[Anexos: print da comparação (3.094 divergentes); CSV exportado das divergências; Edital_Hackathon_Kanastra.pdf; link da página do desafio no Notion]*

**O que foi feito com a resposta:** A IA confirmou que as 3.094 divergências eram todas de situação cadastral e que os descontos estavam corretos. Recomendou consultar os organizadores. Depois, eles corrigiram o arquivo e o resultado chegou a 100% de acerto.

### 54. Resumo do projeto e do problema *(recuperado do histórico da conversa)*

**Prompt:**

> Resuma em 3 parágrafos detalhados todo o projeto e em um outro o problema que estamos tentando encontrar.

**O que foi feito com a resposta:** A IA resumiu o projeto (back-end, eficiência e painel) e o problema da situação cadastral. O texto serviu de base para o relatório.

---

## Fase 6: Desempenho

### 55. Benchmark com timeit

**Prompt:**

> Eles consertaram o arquivo e deu certo, com 100% de match. Escreva um outro arquivo .py que utilize o módulo built-in timeit para descobrir em que parte está sendo gasto mais tempo. Analise o printscreen. Gastaram-se 8 segundos. Queremos reduzir esse tempo a até um quarto, ou seja, até 2 segundos. Analise o resumo que você deu para sugerir onde haveria maiores ganhos em performance após executar os testes. Esses testes nesse arquivo separado devem chamar o main.py automaticamente e retornar os resultados para que eu possa fazer copy-paste para você.
>
> *[Anexos: print]*

**O que foi feito com a resposta:** Foi criado o `benchmark_antecipe.py`, que importa o `main.py` e mede com `timeit` cada etapa (leitura do CSV, motor, serialização, job e HTTP), com perfil `cProfile` e um bloco de resultados pronto para colar.

### 56. Benchmark comentado no chat

**Prompt:**

> Escreva aqui na interface do chat esse mesmo código com comentários explicando o que ele está fazendo seção por seção para copy-paste.

**O que foi feito com a resposta:** A IA escreveu no chat o mesmo script, com comentários explicando cada seção.

### 57. Resultados do benchmark

**Prompt:**

> (saída do benchmark colada)
>
> *[Colado: saída do benchmark (106 linhas)]*

**O que foi feito com a resposta:** Na máquina da equipe, o job levava 8,9 s: motor de regras 4,9 s, conversão da resposta pelo FastAPI 2,8 s e leitura do CSV 2,6 s (só o `strptime` custava 1,2 s), além do coletor de lixo. As otimizações aplicadas foram: JSON gerado uma única vez no job, coletor de lixo pausado durante o lote, parâmetros das regras validados uma vez, cache de CNPJ e datas, datas por fatiamento e CSV lido como listas. O job caiu cerca de 4 vezes, com a saída idêntica, verificada em 178 comparações.

---

## Fase 7: Entrega: README, logs e visual

### 58. README: execução passo a passo

**Prompt:**

> Próximos passos:
> * README.md com o passo a passo para rodar a aplicação do zero: dependências, comandos e variáveis de ambiente. Linguagem, framework e infraestrutura são livres.
> * Relatório curto (deve estar no README): o que o time construiu, as tecnologias usadas, as principais decisões e o que ficou de fora.
> * Histórico dos prompts de IA usados durante o projeto (exportado ou em arquivo de texto).
> Vamos iniciar pela parte do README. Primeiramente, quero que analise a estrutura do diretório do printscreen anexado. Resuma tudo sobre o funcionamento e execução passo a passo que você tem registrado no seu contexto. Por exemplo, coisas como python -m uvicorn main:app --reload e http://127.0.0.1:8000/docs que devem ser feitos para executar, além de como instalar e o que fazer exatamente para navegar a UI. Faremos o upload para o GitHub momentariamente. Primeiramente, vamos trabalhar nisso, no README.
>
> *[Anexos: print]*

**O que foi feito com a resposta:** A IA analisou a estrutura da pasta, testou a instalação do zero e escreveu o `README.md` com requisitos, estrutura, instalação, execução, variáveis de ambiente, uso do painel e da API, regras e solução de problemas. Também criou o `.gitignore` e apontou que faltava o `requirements.txt`.

### 59. Avisos do VS Code

**Prompt:**

> Ignore esses erros por hora, do VS Code. Acredito que sejam aqueles que você havia falado que não influenciavam.

**O que foi feito com a resposta:** A IA confirmou que os avisos eram de checagem de tipos (sem efeito na execução) e seguiu com o README.

### 60. requirements.txt

**Prompt:**

> Faltou o requirements.txt

**O que foi feito com a resposta:** A IA enviou o `requirements.txt` com `fastapi`, `uvicorn`, `python-multipart` e `sse-starlette`.

### 61. Logs estruturados

**Prompt:**

> Altere nada na interface, faça uma alteração mínima apenas nesse main.py para que ele faça um arquivo com logs detalhados seguindo as instruções do edital abaixo:
> 5.2.4. Logs e credenciais (15 pontos): logs estruturados, ausência de dados do
> CSV nos logs e ausência de segredos no repositório.
>
> *[Anexos: main.py]*

**O que foi feito com a resposta:** Foram adicionados logs estruturados em JSON em `logs/antecipe.log`, com rotação e `request_id`. Os logs nunca registram dados do CSV (CNPJ, nomes, valores, datas, nomes de arquivo): só contagens, tempos e status, e em erros, só o tipo e a pilha. A IA plantou dados num CSV inválido e conferiu que nada vazou para o log.

### 62. Novo visual do painel

**Prompt:**

> @"[caminho local]\index.html" https://www.figma.com/design/tLGbY5RZNwev0WFP1UtaIx/Projeto-Comunidade?node-id=0-1&t=IkPZiA46HZuy2boF-1
> https://www.figma.com/files/team/1362897737665221713/resources/community/file/1392241066678485427?fuid=1362897735224327664&q_id=89a7c2f5-e599-4626-9007-0e9a1d3a3a75
> quero que vc altere apenas a aparencia desse html, nao mude nenhuma funcionalidade, se baseie tambem no site da kanastra: https://www.kanastra.com.br/
>
> *[Anexos: index.html]*

**O que foi feito com a resposta:** Em outra ferramenta de IA, o visual do `index.html` foi refeito no estilo da Kanastra (cores, tipografia e layout), sem mudar nenhuma funcionalidade.

### 63. Ajuste na comparação com o gabarito

**Prompt:**

> tire a parte igual para comparação com o gabarito e deixe apenas a coluna com o status de aprovado/reprovado
>
> *[Anexos: print]*

**O que foi feito com a resposta:** Na mesma ferramenta, a comparação com o gabarito na tabela foi simplificada: ficou só a coluna de status aprovado/reprovado.

### 64. Onde manter a comparação

**Prompt:**

> apenas nessa parte que deve ser mantida a comparação com o gabarito
>
> *[Anexos: print]*

**O que foi feito com a resposta:** Ficou definido que a comparação com o gabarito permanece só no cartão de comparação indicado no print.

### 65. Verificar o novo index.html

**Prompt:**

> Esse é o novo index.html. Faça uma verificação rápida que ele também funcionará com a nova versão do main.py que você escreveu. Em seguida, escreva todo o código do main.py mais recente aqui para copy-paste e dê o link de download.
>
> *[Anexos: index.html]*

**O que foi feito com a resposta:** A IA comparou o novo `index.html` com o anterior: as chamadas à API não mudaram. A suíte do painel passou (69 de 69; 3 testes removidos junto com o filtro excluído). A IA entregou o `main.py` completo no chat e para download.

### 66. README com a seção de logs

**Prompt:**

> Reescreva o README apenas com a pequena adição sobre os logs.
> Me dê para copy-paste o texto inteiro.

**O que foi feito com a resposta:** A IA acrescentou ao README as variáveis `LOG_LEVEL` e `LOG_DIR` e uma seção sobre os logs, entregou o texto completo e atualizou o `.gitignore` para ignorar `logs/`.

### 67. Registro de prompts

**Prompt:**

> agora eu quero o registro de prompts de acordo com essa instrução: 4.1.2. Registro de prompts: arquivo com todos os prompts utilizados durante o desenvolvimento, em ordem cronológica, acompanhados da descrição do que foi feito com cada resposta. Todos os prompts estao nesse arquivo txt separados por grandes espaços entre eles.
>
> 5.2.5. Registro de prompts (5 pontos): existência do arquivo de prompts, ordem cronológica e descrição do que foi feito com cada resposta.
>
> *[Anexos: Prompts.txt]*

**O que foi feito com a resposta:** A IA separou os prompts do arquivo exportado, completou os que faltavam com o histórico da conversa e gerou este registro.
