"""
Mede onde o processamento do lote gasta tempo, usando timeit.

Coloque este arquivo na mesma pasta do main.py e rode:

    python benchmark_antecipe.py

Por padrão procura, na mesma pasta, estoque_antecipe.csv,
validadores_antecipe.json e cnpj_situacao_mock.csv. Para usar
outros arquivos:

    python benchmark_antecipe.py --csv outro.csv --regras regras.json --situacoes base.csv

Nada é gravado em disco: o script só lê os arquivos e chama as
funções do main.py. Ao final, copie todo o bloco entre as linhas
"=== COPIE A PARTIR DAQUI ===" e "=== ATÉ AQUI ===".
"""

# ============================================================
# IMPORTAÇÕES
#
# Só módulos da biblioteca padrão do Python:
#   timeit    -> mede o tempo de cada etapa (o pedido principal)
#   cProfile  -> mostra QUAIS funções gastam mais tempo por dentro
#   pstats    -> formata o relatório do cProfile
#   gc        -> liga/desliga o coletor de lixo nas medições
#   argparse  -> lê as opções da linha de comando (--csv etc.)
#   platform  -> informa versão do Python, sistema e processador
# ============================================================

import argparse
import cProfile
import csv
import gc
import io
import json
import os
import platform
import pstats
import statistics
import sys
import timeit

from datetime import date
from pathlib import Path

# A pasta deste script entra no caminho de importação: assim o
# "import main" encontra o main.py que está ao lado dele, não
# importa de qual pasta o comando foi executado.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Importar o main.py cria o app FastAPI e carrega a base de
# situação salva em cnpj_situacoes.pkl (se existir), exatamente
# como acontece quando o uvicorn sobe o servidor.
import main  # noqa: E402  (precisa vir depois do sys.path)


# ============================================================
# MEDIÇÃO
#
# Todas as medições passam por measure(), que guarda o resultado
# em RESULTS para montar a tabela final.
# ============================================================

RESULTS = []


def measure(label, func, repeat, group, per_item=None):
    """
    Executa func com timeit (number=1, `repeat` vezes) com o coletor
    de lixo LIGADO, como no servidor real (o timeit o desliga por
    padrão, o que esconderia parte do custo de criar 100 mil dicts).
    """

    # number=1: cada execução processa o lote inteiro uma vez.
    # repeat: repete a medição várias vezes; o MENOR tempo é o
    # mais confiável (os outros incluem interferências do sistema).
    # setup=gc.enable religa o coletor de lixo antes de medir.
    timer = timeit.Timer(func, setup=gc.enable)
    times = timer.repeat(repeat=repeat, number=1)

    RESULTS.append({
        "grupo": group,
        "etapa": label,
        "min": min(times),
        "mediana": statistics.median(times),
        "por_item": per_item,   # nº de notas, para calcular µs por nota
    })

    # Mostra o progresso na tela enquanto o script roda.
    print(f"  {label:<58} {fmt(min(times)):>10}", flush=True)
    return min(times)


def fmt(seconds):
    """Formata o tempo em segundos (>= 1 s) ou milissegundos."""

    if seconds >= 1:
        return f"{seconds:.3f} s"

    return f"{seconds * 1000:.1f} ms"


def fmt_us(seconds, items):
    """Tempo médio por nota, em microssegundos (µs)."""

    if not items:
        return ""

    return f"{seconds / items * 1e6:.2f} µs"


def profile_top(func, limit):
    """Top funções por tempo próprio (tottime), com caminhos curtos."""

    # O cProfile registra cada chamada de função durante func().
    # Ele deixa o código mais lento, por isso NÃO é usado para os
    # tempos da tabela, só para descobrir quem pesa por dentro.
    profiler = cProfile.Profile()
    profiler.enable()
    func()
    profiler.disable()

    # tottime = tempo gasto dentro da própria função, sem contar
    # as funções que ela chama. É o melhor indicador de "gargalo".
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream)
    stats.strip_dirs().sort_stats("tottime").print_stats(limit)

    # Remove o cabeçalho do pstats e mantém só a tabela.
    lines = stream.getvalue().splitlines()
    start = next(
        (i for i, line in enumerate(lines) if "ncalls" in line),
        0,
    )

    return "\n".join(line.rstrip() for line in lines[start:] if line.strip())


# ============================================================
# EXECUÇÃO
# ============================================================

def main_benchmark():
    # --------------------------------------------------------
    # OPÇÕES DA LINHA DE COMANDO
    #
    # Todas têm padrão: sem opções, usa os arquivos com os nomes
    # oficiais na mesma pasta e repete cada medição 3 vezes.
    # --------------------------------------------------------
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--csv", default=str(HERE / "estoque_antecipe.csv"))
    parser.add_argument(
        "--regras", default=str(HERE / "validadores_antecipe.json")
    )
    parser.add_argument(
        "--situacoes", default=str(HERE / "cnpj_situacao_mock.csv")
    )
    parser.add_argument(
        "--repeticoes",
        type=int,
        default=3,
        help="quantas vezes cada etapa é repetida (vale o menor tempo)",
    )
    parser.add_argument(
        "--sem-http",
        action="store_true",
        help="pula o teste ponta a ponta pela API (TestClient)",
    )
    args = parser.parse_args()
    repeat = max(1, args.repeticoes)

    # O CSV de notas e o JSON de regras são obrigatórios.
    for label, path in (
        ("CSV de notas", args.csv),
        ("JSON de regras", args.regras),
    ):
        if not Path(path).exists():
            sys.exit(f"Arquivo não encontrado ({label}): {path}")

    # --------------------------------------------------------
    # PREPARAÇÃO (fora da medição)
    #
    # Lê os arquivos do disco uma única vez e prepara os dados
    # de entrada de cada etapa, para que as medições comparem só
    # o processamento, e não a velocidade do disco.
    # --------------------------------------------------------
    print("Carregando arquivos...", flush=True)

    csv_bytes = Path(args.csv).read_bytes()
    rules_bytes = Path(args.regras).read_bytes()
    rules = main.parse_rules_config(rules_bytes)

    # Base de situação: usa o CSV informado; se ele não existir,
    # usa a base que a API já salvou (cnpj_situacoes.pkl).
    if Path(args.situacoes).exists():
        situations_bytes = Path(args.situacoes).read_bytes()
        situations_text = main.read_utf8_upload(situations_bytes)
        situations = main.parse_situations_csv(situations_text)
        situations_source = Path(args.situacoes).name
    else:
        situations_bytes = None
        situations_text = None
        situations = main.app.state.cnpj_situacoes
        situations_source = "cnpj_situacoes.pkl (salvo pela API)"

        if situations is None and rules[1].get("SITUACAO_CADASTRAL_SACADO"):
            sys.exit(
                "Base de situação não encontrada. Use --situacoes ARQUIVO."
            )

    # Entradas prontas para as etapas seguintes:
    #   text    -> CSV já decodificado (entrada da leitura)
    #   records -> notas já convertidas (entrada do motor)
    #   rows    -> linhas cruas do CSV (entrada dos testes internos)
    text = main.read_utf8_upload(csv_bytes)
    records = main.parse_csv_records(text)
    rows = list(main.csv_reader_from_text(text))
    n = len(records)

    print(f"{n:,} notas carregadas. Medindo ({repeat} repetições)...\n")

    # --------------------------------------------------------
    # 1. PIPELINE DO SERVIDOR, ETAPA POR ETAPA
    #
    # A sequência que acontece de verdade numa avaliação:
    #   decodificar -> ler o CSV -> motor de regras -> montar a
    #   resposta JSON (que o FastAPI faz ao devolver o resultado).
    # A soma destas etapas é a base dos percentuais da tabela.
    # --------------------------------------------------------
    print("[1] Pipeline do servidor")

    t_decode = measure(
        "Decodificar o upload (bytes -> texto UTF-8)",
        lambda: main.read_utf8_upload(csv_bytes),
        repeat, "pipeline", n,
    )

    if situations_text is not None:
        t_situations = measure(
            "Ler a base de situação (parse_situations_csv)",
            lambda: main.parse_situations_csv(situations_text),
            repeat, "pipeline",
        )
    else:
        t_situations = 0.0

    t_parse = measure(
        "Ler e converter o CSV (parse_csv_records)",
        lambda: main.parse_csv_records(text),
        repeat, "pipeline", n,
    )

    t_eval = measure(
        "Motor de regras + preço (evaluate_receivables)",
        lambda: main.evaluate_receivables(
            records, rules=rules, situations=situations
        ),
        repeat, "pipeline", n,
    )

    # Resultado final, usado para medir a montagem da resposta.
    result = main.evaluate_receivables(
        records, rules=rules, situations=situations
    )

    # Quando uma rota devolve um dict, o FastAPI primeiro passa
    # tudo pelo jsonable_encoder (percorre cada valor do resultado)
    # e só depois gera o texto JSON. As duas partes são medidas
    # separadamente para mostrar qual pesa mais.
    from fastapi.encoders import jsonable_encoder

    t_encoder = measure(
        "Resposta: jsonable_encoder do FastAPI",
        lambda: jsonable_encoder(result),
        repeat, "pipeline", n,
    )

    encoded = jsonable_encoder(result)

    def render_json():
        # O mesmo que JSONResponse.render do Starlette.
        return json.dumps(
            encoded,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        ).encode("utf-8")

    t_dumps = measure(
        "Resposta: json.dumps + encode (JSONResponse)",
        render_json, repeat, "pipeline", n,
    )

    # body = a resposta em bytes, usada para informar o tamanho
    # que o navegador precisa baixar e interpretar.
    body = render_json()
    pipeline_total = (
        t_decode + t_situations + t_parse + t_eval + t_encoder + t_dumps
    )

    # --------------------------------------------------------
    # 2. DENTRO DA LEITURA DO CSV
    #
    # Separa o custo de cada parte de parse_csv_records e mede
    # alternativas mais rápidas (marcadas com "alternativa") para
    # estimar quanto cada otimização economizaria.
    # --------------------------------------------------------
    print("\n[2] Dentro da leitura do CSV")

    # Só separar as colunas, sem converter nada.
    measure(
        "csv.DictReader: só separar as colunas",
        lambda: list(main.csv_reader_from_text(text)),
        repeat, "csv", n,
    )

    # Alternativa: listas em vez de dicionários (acesso por índice).
    measure(
        "csv.reader (listas, sem dict) - alternativa",
        lambda: list(csv.reader(io.StringIO(text, newline=""), delimiter=";")),
        repeat, "csv", n,
    )

    measure(
        "is_empty_row em todas as linhas",
        lambda: [main.is_empty_row(row) for row in rows],
        repeat, "csv", n,
    )

    # Conversão completa de cada linha (textos, valor e datas).
    measure(
        "cast_record em todas as linhas (conversão completa)",
        lambda: [main.cast_record(row) for row in rows],
        repeat, "csv", n,
    )

    # Valores e datas isolados, para medir cada conversor.
    values = [row["VALOR_NOMINAL"] for row in rows]
    dates = [row["DATA_VENCIMENTO_ORIGINAL"] for row in rows] + [
        row["DATA_AQUISICAO"] for row in rows
    ]

    measure(
        "parse_money_to_cents (1 por linha)",
        lambda: [main.parse_money_to_cents(value) for value in values],
        repeat, "csv", n,
    )

    measure(
        "parse_date com strptime (2 por linha)",
        lambda: [main.parse_date(value) for value in dates],
        repeat, "csv", n,
    )

    # Alternativa para datas dd/mm/aaaa: fatiar o texto e montar
    # a data diretamente, sem o strptime (que interpreta o
    # formato a cada chamada).
    def manual_dates():
        return [
            date(int(value[6:10]), int(value[3:5]), int(value[0:2]))
            for value in dates
        ]

    measure(
        "datas por fatiamento date(aaaa, mm, dd) - alternativa",
        manual_dates, repeat, "csv", n,
    )

    # --------------------------------------------------------
    # 3. DENTRO DO MOTOR DE REGRAS
    #
    # Mede, isoladamente, as funções que o motor chama a cada
    # nota, e o motor inteiro em duas variações: com o callback
    # de progresso do job e com o coletor de lixo desligado.
    # --------------------------------------------------------
    print("\n[3] Dentro do motor de regras")

    sacados = [record["DOC_SACADO"] for record in records]
    distinct_sacados = len(set(sacados))

    # Comparar estas duas linhas mostra o ganho de um cache por
    # sacado: o mesmo CNPJ aparece em muitas notas.
    measure(
        "valid_cnpj em todas as notas (sem cache)",
        lambda: [main.valid_cnpj(doc) for doc in sacados],
        repeat, "motor", n,
    )

    measure(
        f"valid_cnpj só nos {distinct_sacados:,} sacados distintos",
        lambda: [main.valid_cnpj(doc) for doc in set(sacados)],
        repeat, "motor", n,
    )

    # Chaves de concentração (fundo, raiz do sacado e do cedente).
    measure(
        "normalize_document + cnpj_root (3 por nota)",
        lambda: [
            (
                main.normalize_document(record["DOC_FUNDO"]),
                main.cnpj_root(record["DOC_SACADO"]),
                main.cnpj_root(record["DOC_CEDENTE"]),
            )
            for record in records
        ],
        repeat, "motor", n,
    )

    config = rules[0]

    measure(
        "calculate_pricing em todas as notas",
        lambda: [main.calculate_pricing(record, config) for record in records],
        repeat, "motor", n,
    )

    # Cada nota aprovada gera até 3 valores em centavos e reais.
    measure(
        "money_to_json (3 por nota)",
        lambda: [
            (
                main.money_to_json(record["VALOR_NOMINAL_CENTAVOS"]),
                main.money_to_json(1234),
                main.money_to_json(5678),
            )
            for record in records
        ],
        repeat, "motor", n,
    )

    # Motor com o mesmo callback de progresso usado pela barra.
    # A diferença para a etapa [1] é o custo do progresso.
    job = main.create_job(["csv", "avaliacao"])
    progress = main.job_progress_callback(job)

    measure(
        "evaluate_receivables COM callback de progresso (job)",
        lambda: main.evaluate_receivables(
            records, rules=rules, situations=situations, progress=progress
        ),
        repeat, "motor", n,
    )

    # Motor sem o coletor de lixo: a diferença para a etapa [1]
    # mostra quanto o coletor custa ao criar milhares de dicts.
    def evaluate_without_gc():
        gc.disable()

        try:
            return main.evaluate_receivables(
                records, rules=rules, situations=situations
            )
        finally:
            gc.enable()   # sempre religa, mesmo se der erro

    measure(
        "evaluate_receivables com o coletor de lixo DESLIGADO",
        evaluate_without_gc, repeat, "motor", n,
    )

    # --------------------------------------------------------
    # 4. JOB COMPLETO E API PONTA A PONTA
    #
    # (a) A função que roda em segundo plano quando o painel
    #     clica em "Avaliar notas" (leitura + motor + progresso).
    # (b) O caminho HTTP inteiro, sem navegador: upload do CSV,
    #     criação do job e download do resultado JSON.
    # --------------------------------------------------------
    print("\n[4] Job completo e API")

    def full_job():
        job = main.create_job(["csv", "avaliacao"])
        main.run_evaluation_job(
            job, text, None, rules, None, situations, False
        )
        assert job["status"] == "concluido", job["erro"]
        main.jobs.pop(job["id"], None)   # libera a memória do job

    measure(
        "run_evaluation_job completo (o que roda em segundo plano)",
        full_job, repeat, "api", n,
    )

    http_note = ""

    if not args.sem_http:
        # O TestClient do FastAPI chama a API sem abrir porta de
        # rede. Ele depende do pacote httpx; sem ele, esta parte é
        # pulada e o relatório avisa.
        try:
            from fastapi.testclient import TestClient
        except Exception as error:  # httpx ausente
            TestClient = None
            http_note = f"TestClient indisponível ({error.__class__.__name__})"

        if TestClient is not None:
            client = TestClient(main.app)

            # Coloca regras e base no estado da API (como se tivessem
            # sido enviadas pelo painel) e guarda o estado anterior
            # para restaurá-lo no fim.
            previous_state = (
                main.app.state.rules_config,
                main.app.state.rule_flags,
                main.app.state.rule_parameters,
                main.app.state.cnpj_situacoes,
            )
            main.app.state.rules_config = rules[0]
            main.app.state.rule_flags = rules[1]
            main.app.state.rule_parameters = rules[2]
            main.app.state.cnpj_situacoes = situations

            def http_flow():
                # No TestClient a tarefa em segundo plano termina
                # antes de o POST responder.
                response = client.post(
                    "/avaliar-recebiveis/jobs",
                    files={"file": ("lote.csv", csv_bytes, "text/csv")},
                )
                assert response.status_code == 202, response.text
                job_id = response.json()["job_id"]
                result_response = client.get(f"/jobs/{job_id}/resultado")
                assert result_response.status_code == 200
                main.jobs.pop(job_id, None)
                return len(result_response.content)

            measure(
                "HTTP: POST job + GET resultado (sem navegador)",
                http_flow, repeat, "api", n,
            )

            (
                main.app.state.rules_config,
                main.app.state.rule_flags,
                main.app.state.rule_parameters,
                main.app.state.cnpj_situacoes,
            ) = previous_state

    # --------------------------------------------------------
    # 5. PERFIL (cPROFILE) DAS DUAS ETAPAS PESADAS
    #
    # Lista as funções que mais consomem tempo dentro da leitura
    # do CSV e do motor de regras. É daqui que saem as sugestões
    # de otimização mais precisas.
    # --------------------------------------------------------
    print("\n[5] Gerando perfil com cProfile...")

    profile_parse = profile_top(lambda: main.parse_csv_records(text), 12)
    profile_eval = profile_top(
        lambda: main.evaluate_receivables(
            records, rules=rules, situations=situations
        ),
        15,
    )

    # --------------------------------------------------------
    # RELATÓRIO FINAL (para copiar e colar)
    #
    # Cabeçalho com ambiente e dados do lote, a tabela de tempos
    # agrupada por seção e os dois perfis do cProfile.
    # --------------------------------------------------------
    summary = result["resumo"]
    note_bytes = len(body) / n if n else 0

    # Versões das bibliotecas: o desempenho muda entre versões.
    try:
        import fastapi
        import starlette
        import pydantic
        versions = (
            f"fastapi {fastapi.__version__} · starlette "
            f"{starlette.__version__} · pydantic {pydantic.VERSION}"
        )
    except Exception:
        versions = "versões indisponíveis"

    out = []
    out.append("=== COPIE A PARTIR DAQUI ===")
    out.append("BENCHMARK ANTECIPE (timeit, gc ligado, melhor de "
               f"{repeat})")
    out.append(
        f"Python {platform.python_version()} ({platform.python_implementation()})"
        f" · {platform.system()} {platform.release()} · "
        f"{platform.machine()} · {os.cpu_count()} CPUs"
    )
    out.append(versions)
    out.append(
        f"Arquivo: {Path(args.csv).name} · {len(csv_bytes) / 1e6:.1f} MB · "
        f"{n:,} notas · {distinct_sacados:,} sacados distintos · "
        f"base: {situations_source}"
    )
    # Conferência: os números devem bater com os do painel.
    out.append(
        f"Resultado: {summary['aprovadas']:,} aprovadas · "
        f"{summary['barradas']:,} barradas · "
        f"{summary['total_a_receber_formatado']}"
    )
    out.append(
        f"Resposta JSON: {len(body) / 1e6:.1f} MB "
        f"({note_bytes:.0f} bytes por nota)"
    )
    out.append("")

    # Tabela: mínimo, mediana, tempo por nota e % em relação à
    # soma do pipeline do servidor (seção [1]).
    current_group = None
    group_titles = {
        "pipeline": "[1] PIPELINE DO SERVIDOR (soma = "
                    f"{fmt(pipeline_total)})",
        "csv": "[2] DENTRO DA LEITURA DO CSV",
        "motor": "[3] DENTRO DO MOTOR DE REGRAS",
        "api": "[4] JOB COMPLETO E API",
    }

    for item in RESULTS:
        if item["grupo"] != current_group:
            current_group = item["grupo"]
            out.append(group_titles[current_group])
            out.append(
                f"  {'etapa':<58} {'mín':>10} {'mediana':>10} "
                f"{'por nota':>10} {'% pipe':>7}"
            )

        share = (
            f"{item['min'] / pipeline_total * 100:6.1f}%"
            if pipeline_total else ""
        )
        out.append(
            f"  {item['etapa']:<58} {fmt(item['min']):>10} "
            f"{fmt(item['mediana']):>10} "
            f"{fmt_us(item['min'], item['por_item']):>10} {share:>7}"
        )

    if http_note:
        out.append(f"  ({http_note})")

    out.append("")
    out.append("[5] CPROFILE: parse_csv_records (top 12 por tempo próprio)")
    out.append(profile_parse)
    out.append("")
    out.append("[5] CPROFILE: evaluate_receivables (top 15 por tempo próprio)")
    out.append(profile_eval)
    out.append("=== ATÉ AQUI ===")

    print("\n" + "\n".join(out))


# Só executa quando o arquivo é rodado diretamente
# (python benchmark_antecipe.py), não quando é importado.
if __name__ == "__main__":
    main_benchmark()