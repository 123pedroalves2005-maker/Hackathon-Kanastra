import asyncio
import csv
import gc
import io
import json
import logging
import operator
import os
import pickle
import re
import sys
import threading
import time
import traceback
import uuid

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from sse_starlette.sse import EventSourceResponse


app = FastAPI()

# Origens autorizadas a chamar a API pelo navegador (front-end
# servido em outro endereço, como o Live Server do VS Code).
# Separe várias por vírgula na variável de ambiente CORS_ORIGINS;
# o padrão "*" libera qualquer origem, adequado para uso local.
CORS_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", "*").split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# CONFIGURAÇÃO
# ============================================================

app.state.rules_config = None
app.state.rule_flags = {}
app.state.rule_parameters = {}
app.state.cnpj_situacoes = None

DUPLICATES_PATH = Path(__file__).with_name("recebiveis_teste.pkl")
SITUATIONS_PATH = Path(__file__).with_name("cnpj_situacoes.pkl")
FRONTEND_PATH = Path(__file__).with_name("index.html")

# Colunas obrigatórias da base de situação cadastral.
SITUATION_FIELDS = {"DOC", "SITUACAO_CADASTRAL"}

# CNPJ do sacado que não consta na base de situação cadastral.
# O resultado esperado do hackathon não barra esses casos (ex.: o
# sacado 58.206.002/0001-11 da linha 3 não está no mock e não é
# barrado por situação). Mude para True para barrá-los.
BLOCK_MISSING_CNPJ = False

EXPECTED_FIELDS = {
    "NOME_FUNDO",
    "DOC_FUNDO",
    "NOME_CEDENTE",
    "DOC_CEDENTE",
    "NOME_SACADO",
    "DOC_SACADO",
    "NU_DOCUMENTO",
    "TIPO_RECEBIVEL",
    "VALOR_NOMINAL",
    "DATA_VENCIMENTO_ORIGINAL",
    "DATA_AQUISICAO",
}

TEXT_FIELDS = {
    "NOME_FUNDO",
    "DOC_FUNDO",
    "NOME_CEDENTE",
    "DOC_CEDENTE",
    "NOME_SACADO",
    "DOC_SACADO",
    "NU_DOCUMENTO",
    "TIPO_RECEBIVEL",
}

# Percentuais do JSON são guardados como inteiros com
# quatro casas decimais: 2,5% -> 25000; 100% -> 1000000.
PERCENT_DECIMALS = 4
PERCENT_SCALE = 10 ** PERCENT_DECIMALS

# Progresso: quantas atualizações, no máximo, cada etapa publica
# (evita custo de lock por linha em arquivos grandes), o intervalo
# de envio do SSE e por quanto tempo jobs finalizados ficam em memória.
PROGRESS_STEPS = 200
PROGRESS_INTERVAL_SECONDS = 0.1
JOB_TTL_SECONDS = 3600

# Peso de cada etapa no percentual geral do job.
STAGE_WEIGHTS = {
    "regras": 5,
    "situacoes": 5,
    "csv": 45,
    "avaliacao": 50,
}

STAGE_LABELS = {
    "regras": "Lendo e validando o JSON de regras",
    "situacoes": "Lendo a base de situação cadastral",
    "csv": "Lendo e convertendo o CSV",
    "avaliacao": "Executando o motor de regras",
}

# Nomes das regras em linguagem simples, para as explicações.
RULE_NAMES = {
    "CNPJ_SACADO_VALIDO": "CNPJ do cliente",
    "SITUACAO_CADASTRAL_SACADO": "situação cadastral do cliente",
    "TIPO_RECEBIVEL": "tipo de nota",
    "VALOR_MINIMO": "valor mínimo",
    "NAO_VENCIDA": "vencimento",
    "PRAZO_MAXIMO": "prazo máximo",
    "CONCENTRACAO_MAXIMA_SACADO": "concentração por cliente",
    "PRAZO_MINIMO": "prazo mínimo",
    "CONCENTRACAO_MAXIMA_CEDENTE": "concentração por empresa vendedora",
}

# Status final de cada nota, no formato do resultado esperado.
STATUS_BY_ELIGIBILITY = {
    "ELEGIVEL": "APROVADO",
    "NAO_ELEGIVEL": "BARRADO",
    "PENDENTE_VERIFICACAO": "PENDENTE",
}

SUPPORTED_VALIDATORS = {
    "CNPJ_SACADO_VALIDO",
    "SITUACAO_CADASTRAL_SACADO",
    "TIPO_RECEBIVEL",
    "VALOR_MINIMO",
    "NAO_VENCIDA",
    "PRAZO_MAXIMO",
    "CONCENTRACAO_MAXIMA_SACADO",
    "PRAZO_MINIMO",
    "CONCENTRACAO_MAXIMA_CEDENTE",
}

# Ordem de execução das regras (é também a ordem de "barrado_por").
RULE_ORDER = (
    "CNPJ_SACADO_VALIDO",
    "SITUACAO_CADASTRAL_SACADO",
    "TIPO_RECEBIVEL",
    "VALOR_MINIMO",
    "NAO_VENCIDA",
    "PRAZO_MAXIMO",
    "CONCENTRACAO_MAXIMA_SACADO",
    "PRAZO_MINIMO",
    "CONCENTRACAO_MAXIMA_CEDENTE",
)

# Expressões regulares compiladas uma única vez (re.fullmatch com
# o padrão em texto consulta o cache do módulo re a cada chamada).
MONEY_RE = re.compile(
    r"(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d{1,2})?",
    flags=re.ASCII,
)
DATE_RE = re.compile(r"\d{2}/\d{2}/\d{4}", flags=re.ASCII)
NON_DIGITS_RE = re.compile(r"[^0-9]")
CNPJ_CHARS_RE = re.compile(r"[0-9./-]+")

# Campos de texto em ordem fixa (evita ordenar a cada linha).
TEXT_FIELDS_ORDER = tuple(sorted(TEXT_FIELDS))
DATE_FIELDS = ("DATA_VENCIMENTO_ORIGINAL", "DATA_AQUISICAO")

# Colunas convertidas, na ordem esperada por cast_values (é
# também a ordem usada para identificar a coluna de um erro).
CAST_FIELDS = TEXT_FIELDS_ORDER + ("VALOR_NOMINAL",) + DATE_FIELDS


# ============================================================
# LOGS ESTRUTURADOS
#
# Cada evento vira uma linha JSON em logs/antecipe.log (com
# rotação: 5 arquivos de até 5 MB). Regras do log:
#   - NUNCA registra dados do CSV: nada de CNPJ, nomes, números
#     de documento, valores, datas ou nomes de arquivo. Só
#     contagens, tamanhos, tempos, códigos de regra e status.
#   - Mensagens de erro (que podem citar um valor da linha) não
#     são registradas: só o tipo do erro, a coluna e a pilha
#     (arquivo:linha:função).
#   - Não há segredos: a aplicação não usa senhas nem chaves.
# Variáveis de ambiente opcionais: LOG_LEVEL (padrão INFO) e
# LOG_DIR (padrão: pasta logs ao lado do main.py).
# ============================================================

LOG_DIR = Path(os.environ.get("LOG_DIR") or Path(__file__).with_name("logs"))
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()

# Identificador da requisição atual, repetido em todos os eventos
# gerados por ela (inclusive no job em segundo plano).
request_id_var = ContextVar("request_id", default=None)


class JsonLogFormatter(logging.Formatter):
    """Formata o evento como uma linha JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(timespec="milliseconds"),
            "nivel": record.levelname,
            "evento": record.getMessage(),
            "request_id": request_id_var.get(),
        }

        entry.update(getattr(record, "dados", None) or {})

        if record.exc_info:
            entry["erro"] = exception_summary(record.exc_info)

        return json.dumps(entry, ensure_ascii=False, default=str)


def exception_summary(exc_info) -> dict:
    """Tipo do erro e pilha resumida, SEM a mensagem (pode ter dados)."""

    exc_type, _, tb = exc_info

    return {
        "tipo": exc_type.__name__ if exc_type else None,
        "pilha": [
            f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_tb(tb)
        ][-10:],
    }


def setup_logger() -> logging.Logger:
    log = logging.getLogger("antecipe")

    if not log.handlers:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_DIR / "antecipe.log",
            maxBytes=5_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(JsonLogFormatter())
        log.addHandler(handler)
        log.setLevel(LOG_LEVEL)
        log.propagate = False

    return log


logger = setup_logger()


def log_event(event: str, level: int = logging.INFO, exc_info=None,
              **fields) -> None:
    """Registra um evento com campos estruturados (sem dados do CSV)."""

    logger.log(level, event, exc_info=exc_info, extra={"dados": fields})


def elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


def result_metrics(result: dict) -> dict:
    """Só contagens agregadas do resultado (sem valores ou documentos)."""

    summary = result["resumo"]

    return {
        "notas": summary["total_notas"],
        "aprovadas": summary["aprovadas"],
        "barradas": summary["barradas"],
        "pendentes": summary["pendentes"],
        "barradas_por_regra": summary["barradas_por_regra"],
    }


class RequestLogMiddleware:
    """
    Registra cada requisição HTTP: método, rota (o modelo, ex.:
    /jobs/{job_id}), status e duração até o fim da resposta.
    Também devolve o cabeçalho X-Request-ID para correlacionar
    a requisição com os eventos do log.
    """

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        state = {"status": 500, "logged": False}

        def log_request(completed: bool) -> None:
            state["logged"] = True
            route = scope.get("route")

            log_event(
                "http_requisicao",
                level=logging.INFO if completed else logging.WARNING,
                metodo=scope.get("method"),
                rota=getattr(route, "path", None) or scope.get("path"),
                status=state["status"],
                duracao_ms=elapsed_ms(start),
                concluida=completed,
            )

        async def send_with_log(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"x-request-id", request_id.encode()),
                    ],
                }

            await send(message)

            if (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and not state["logged"]
            ):
                log_request(completed=True)

        try:
            await self.app(scope, receive, send_with_log)

        except Exception:
            log_event(
                "http_erro_inesperado",
                level=logging.ERROR,
                exc_info=sys.exc_info(),
                metodo=scope.get("method"),
                rota=scope.get("path"),
            )
            raise

        finally:
            if not state["logged"]:
                log_request(completed=False)

            request_id_var.reset(token)


app.add_middleware(RequestLogMiddleware)


# ============================================================
# COLETOR DE LIXO DURANTE O PROCESSAMENTO
#
# Um lote cria centenas de milhares de dicts de uma vez, e o
# coletor de lixo cíclico do Python varre esses objetos várias
# vezes enquanto eles são criados (no estoque de 100 mil notas,
# isso custava quase metade do tempo do job). As estruturas do
# lote não têm referências circulares: a memória continua sendo
# liberada normalmente pela contagem de referências. O coletor é
# pausado só enquanto há algum lote em andamento.
# ============================================================

gc_pause_lock = threading.Lock()
gc_pause_count = 0
gc_was_enabled = True


@contextmanager
def gc_paused():
    """Pausa o coletor cíclico; seguro com vários lotes ao mesmo tempo."""

    global gc_pause_count, gc_was_enabled

    with gc_pause_lock:
        if gc_pause_count == 0:
            gc_was_enabled = gc.isenabled()
            gc.disable()

        gc_pause_count += 1

    try:
        yield

    finally:
        with gc_pause_lock:
            gc_pause_count -= 1

            if gc_pause_count == 0 and gc_was_enabled:
                gc.enable()


# ============================================================
# PROGRESSO
# ============================================================

def progress_reporter(progress, stage: str, total: int):
    """
    Cria uma função report(feitos) para uma etapa.

    Só repassa ao callback a cada ~1/PROGRESS_STEPS do total
    (e sempre no fim), para não pesar no processamento.
    Sem callback, devolve uma função que não faz nada.
    """

    if progress is None:
        return lambda done: None

    step = max(1, total // PROGRESS_STEPS)

    def report(done: int) -> None:
        if done % step == 0 or done == total:
            progress(stage, done, total)

    report(0)
    return report


# ============================================================
# LEITURA E VALIDAÇÃO ESTRUTURAL DO CSV
# ============================================================

def read_utf8_upload(content: bytes) -> str:
    """Decodifica UTF-8, aceitando arquivos com BOM."""

    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="O arquivo deve estar codificado em UTF-8."
        )


def csv_reader_from_text(text: str):
    """Cria o leitor e valida os cabeçalhos obrigatórios."""

    reader = csv.DictReader(
        io.StringIO(text, newline=""),
        delimiter=";"
    )

    if not reader.fieldnames:
        raise ValueError("O CSV não possui cabeçalho.")

    reader.fieldnames = [
        field.strip() for field in reader.fieldnames
    ]

    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError(
            "O CSV possui nomes de colunas duplicados."
        )

    missing = EXPECTED_FIELDS - set(reader.fieldnames)

    if missing:
        raise ValueError(
            f"Colunas ausentes: {', '.join(sorted(missing))}"
        )

    return reader


def csv_errors_exception(errors: list) -> HTTPException:
    """Monta a resposta 422 com até 100 erros de linha."""

    # No log, só quantos erros há em cada coluna (sem linhas,
    # valores ou motivos, que podem conter dados do arquivo).
    errors_by_column = defaultdict(int)

    for error in errors:
        errors_by_column[error["coluna"] or "LINHA"] += 1

    log_event(
        "csv_rejeitado",
        level=logging.WARNING,
        total_erros=len(errors),
        erros_por_coluna=dict(errors_by_column),
    )

    return HTTPException(
        status_code=422,
        detail={
            "mensagem": "Foram encontrados erros no CSV.",
            "total_erros": len(errors),
            "erros": errors[:100],
            "observacao": (
                "A resposta mostra até 100 erros. "
                "Corrija os dados e envie o arquivo novamente."
            ),
        },
    )


def is_empty_row(row: dict) -> bool:
    """Verifica se uma linha está completamente vazia."""

    if not row:
        return True

    return all(
        value is None or not str(value).strip()
        for value in row.values()
    )


# ============================================================
# TYPE CASTING
# ============================================================

def parse_text(value: str) -> str:
    """Remove espaços externos de um campo textual."""

    return (value or "").strip()


def parse_money_to_cents(value: str) -> int:
    """
    Converte valores monetários brasileiros em centavos.

    Exemplos:
        '100,00'   -> 10000
        '15,50'    -> 1550
        '1.234,56' -> 123456
        '100'      -> 10000

    Não utiliza float.
    """

    value = parse_text(value)

    if not value:
        raise ValueError("O valor monetário está vazio.")

    if not MONEY_RE.fullmatch(value):
        raise ValueError(
            f"Formato monetário inválido: {value!r}. "
            "Use, por exemplo, 1234,56 ou 1.234,56."
        )

    normalized = value.replace(".", "")

    if "," in normalized:
        integer_part, decimal_part = normalized.split(",", 1)
    else:
        integer_part = normalized
        decimal_part = ""

    decimal_part = decimal_part.ljust(2, "0")

    reais = int(integer_part)
    centavos = int(decimal_part or "0")

    return reais * 100 + centavos


@lru_cache(maxsize=4096)
def parse_date(value: str) -> date:
    """
    Converte dd/mm/aaaa em um objeto date.

    Um lote tem poucas datas distintas repetidas em milhares de
    linhas; o lru_cache guarda as já convertidas (date é imutável,
    então compartilhar o mesmo objeto é seguro). Erros não são
    guardados: a exceção é gerada de novo a cada chamada.
    """

    value = parse_text(value)

    if not value:
        raise ValueError("A data está vazia.")

    # Caminho rápido para o formato do arquivo (dd/mm/aaaa): montar
    # a data pelas fatias do texto é cerca de 10x mais rápido que o
    # strptime. Data impossível (ex.: 31/02) ou formato diferente
    # (ex.: 1/2/2026) seguem para o strptime, que mantém as mesmas
    # regras e a mesma mensagem de erro de antes.
    if DATE_RE.fullmatch(value):
        try:
            return date(int(value[6:]), int(value[3:5]), int(value[:2]))
        except ValueError:
            pass

    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError:
        raise ValueError(
            f"Data inválida: {value!r}. "
            "Use uma data válida no formato dd/mm/aaaa."
        )


def cast_values(values) -> dict:
    """
    Converte os valores de uma linha (na ordem de CAST_FIELDS)
    para os tipos internos.

    Valores monetários são inteiros em centavos.
    Datas são objetos date.
    Documentos permanecem strings.
    """

    # Caminho rápido: todos os textos presentes e não vazios
    # (strip e montagem do dict feitos em lote).
    texts = values[:len(TEXT_FIELDS_ORDER)]
    stripped = None if None in texts else list(map(str.strip, texts))

    if stripped is not None and all(stripped):
        record = dict(zip(TEXT_FIELDS_ORDER, stripped))
    else:
        # Há problema: percorre campo a campo para apontar o
        # primeiro, na ordem de TEXT_FIELDS_ORDER.
        for field, value in zip(TEXT_FIELDS_ORDER, texts):
            if value is None:
                raise ValueError(f"O campo {field} está ausente.")

            if not value.strip():
                raise ValueError(
                    f"O campo obrigatório {field} está vazio."
                )

    # O nome do campo entra na mensagem para que
    # parse_csv_records consiga identificar a coluna.
    try:
        record["VALOR_NOMINAL_CENTAVOS"] = parse_money_to_cents(
            values[len(TEXT_FIELDS_ORDER)]
        )
    except ValueError as error:
        raise ValueError(f"VALOR_NOMINAL: {error}")

    for field, value in zip(
        DATE_FIELDS,
        values[len(TEXT_FIELDS_ORDER) + 1:],
    ):
        try:
            record[field] = parse_date(value)
        except ValueError as error:
            raise ValueError(f"{field}: {error}")

    return record


def cast_record(row: dict) -> dict:
    """Converte uma linha lida como dict (nome da coluna -> valor)."""

    return cast_values(tuple(row.get(field) for field in CAST_FIELDS))


def record_to_json(record: dict) -> dict:
    """Converte as datas para ISO, mantendo valores em centavos."""

    result = record.copy()

    for field in (
        "DATA_VENCIMENTO_ORIGINAL",
        "DATA_AQUISICAO",
    ):
        if isinstance(result.get(field), date):
            result[field] = result[field].isoformat()

    return result


def parse_csv_records(text: str, progress=None) -> list:
    """Valida e converte o CSV inteiro."""

    try:
        reader = csv_reader_from_text(text)

        # Depois de validar o cabeçalho, as linhas são lidas como
        # listas pelo csv.reader interno do DictReader: montar um
        # dict por linha custaria mais do que a própria leitura.
        # As posições das colunas são calculadas uma única vez.
        fieldnames = reader.fieldnames
        width = len(fieldnames)
        position = {field: index for index, field in enumerate(fieldnames)}
        pick = operator.itemgetter(*(position[field] for field in CAST_FIELDS))
        lines = reader.reader

        # Ler as linhas antes da conversão permite conhecer
        # o total e calcular quanto falta. line_num é a linha
        # física no arquivo (linhas em branco são puladas, então
        # contar registros deslocaria a numeração).
        rows = [(lines.line_num, row) for row in lines if row]
        report = progress_reporter(progress, "csv", len(rows))

        records = []
        errors = []

        for done, (line_number, row) in enumerate(rows):
            report(done)
            length = len(row)

            if length > width:
                errors.append({
                    "linha": line_number,
                    "coluna": None,
                    "valor": None,
                    "motivo": (
                        "A linha possui campos além dos cabeçalhos."
                    ),
                })
                continue

            # Linha só com campos vazios ou espaços: ignorada.
            if not "".join(row).strip():
                continue

            # Linha curta: verifica se falta alguma coluna obrigatória.
            if length < width:
                missing_values = [
                    field
                    for field in EXPECTED_FIELDS
                    if position[field] >= length
                ]

                if missing_values:
                    for field in sorted(missing_values):
                        errors.append({
                            "linha": line_number,
                            "coluna": field,
                            "valor": None,
                            "motivo": "O campo está ausente na linha.",
                        })
                    continue

            try:
                record = cast_values(pick(row))
                record["LINHA"] = line_number
                records.append(record)

            except ValueError as error:
                message = str(error)

                field = next(
                    (
                        candidate
                        for candidate in CAST_FIELDS
                        if candidate in message
                    ),
                    None,
                )

                errors.append({
                    "linha": line_number,
                    "coluna": field,
                    "valor": row[position[field]] if field else None,
                    "motivo": message,
                })

        report(len(rows))

        if errors:
            raise csv_errors_exception(errors)

        return records

    except csv.Error as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except ValueError as error:
        # Cabeçalho ausente, duplicado ou incompleto.
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )


# ============================================================
# DOCUMENTOS E VALIDAÇÃO DE CNPJ
# ============================================================

def normalize_document(value) -> str:
    """Mantém somente os dígitos ASCII de um documento."""

    if value is None:
        return ""

    return NON_DIGITS_RE.sub("", str(value))


def valid_cnpj(value) -> bool:
    """
    Verifica se o CNPJ contém 14 dígitos e se os dois
    dígitos verificadores passam no algoritmo módulo 11.

    Aceita a formatação usual com pontos, barra e hífen,
    mas rejeita letras e outros caracteres.

    Não consulta a situação cadastral na Receita Federal.
    """

    raw = str(value or "").strip()

    if not CNPJ_CHARS_RE.fullmatch(raw):
        return False

    cnpj = normalize_document(raw)

    if len(cnpj) != 14:
        return False

    if len(set(cnpj)) == 1:
        return False

    digits = [int(character) for character in cnpj]

    def calculate_digit(numbers, weights):
        total = sum(
            number * weight
            for number, weight in zip(numbers, weights)
        )

        remainder = total % 11

        return 0 if remainder < 2 else 11 - remainder

    first_weights = [
        5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2
    ]

    first_digit = calculate_digit(
        digits[:12],
        first_weights
    )

    second_weights = [
        6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2
    ]

    second_digit = calculate_digit(
        digits[:12] + [first_digit],
        second_weights
    )

    return (
        digits[12] == first_digit
        and digits[13] == second_digit
    )


def fund_key(record: dict) -> str:
    """
    Identifica o fundo da nota para a concentração: dígitos do
    DOC_FUNDO ou, se não houver dígitos, o texto informado.
    """

    return normalize_document(record["DOC_FUNDO"]) or record["DOC_FUNDO"]


def cnpj_root(value) -> str:
    """Retorna a raiz de oito dígitos de um CNPJ."""

    digits = normalize_document(value)

    if len(digits) == 14:
        return digits[:8]

    return digits


# ============================================================
# FUNÇÕES DE DUPLICATAS
# ============================================================

def receivable_key(row: dict) -> tuple:
    """Cria uma chave de comparação para um recebível."""

    return (
        normalize_document(row.get("DOC_CEDENTE")),
        normalize_document(row.get("DOC_SACADO")),
        str(row.get("NU_DOCUMENTO") or "").strip().upper(),
        str(row.get("TIPO_RECEBIVEL") or "").strip().upper(),
    )


def load_receivables() -> set:
    """Carrega o histórico local de chaves de teste."""

    if not DUPLICATES_PATH.exists():
        return set()

    try:
        with DUPLICATES_PATH.open("rb") as file:
            data = pickle.load(file)

    except (pickle.UnpicklingError, EOFError, OSError) as error:
        raise HTTPException(
            status_code=500,
            detail=f"Não foi possível ler o histórico: {error}"
        )

    if not isinstance(data, set):
        raise HTTPException(
            status_code=500,
            detail="O histórico não contém um conjunto válido."
        )

    return data


def write_pickle(path: Path, data, description: str) -> None:
    """Grava com pickle em arquivo temporário e troca de uma vez."""

    temp_path = path.with_suffix(".tmp")

    try:
        with temp_path.open("wb") as file:
            pickle.dump(
                data,
                file,
                protocol=pickle.HIGHEST_PROTOCOL
            )

        temp_path.replace(path)

    except OSError as error:
        raise HTTPException(
            status_code=500,
            detail=f"Não foi possível salvar {description}: {error}"
        )


def save_receivables(receivables: set) -> None:
    """Persiste o histórico em um arquivo local."""

    write_pickle(DUPLICATES_PATH, receivables, "o histórico")


def example_from_row(row: dict) -> dict:
    """Seleciona os campos usados nos exemplos de duplicatas."""

    return {
        "DOC_CEDENTE": row.get("DOC_CEDENTE"),
        "DOC_SACADO": row.get("DOC_SACADO"),
        "NU_DOCUMENTO": row.get("NU_DOCUMENTO"),
        "TIPO_RECEBIVEL": row.get("TIPO_RECEBIVEL"),
    }


# ============================================================
# BASE DE SITUAÇÃO CADASTRAL
#
# Guardada como dicionário {cnpj_14_digitos: situacao}: a
# consulta de cada sacado é O(1). O dicionário é salvo com
# pickle para sobreviver a reinícios do servidor.
# ============================================================

def parse_situations_csv(text: str, progress=None) -> dict:
    """Valida o CSV DOC;SITUACAO_CADASTRAL e monta o dicionário."""

    try:
        reader = csv.DictReader(
            io.StringIO(text, newline=""),
            delimiter=";"
        )

        if not reader.fieldnames:
            raise ValueError("O CSV não possui cabeçalho.")

        reader.fieldnames = [
            field.strip() for field in reader.fieldnames
        ]

        missing = SITUATION_FIELDS - set(reader.fieldnames)

        if missing:
            raise ValueError(
                f"Colunas ausentes: {', '.join(sorted(missing))}"
            )

        rows = [(reader.line_num, row) for row in reader]
        report = progress_reporter(progress, "situacoes", len(rows))

        situations = {}
        errors = []

        for done, (line_number, row) in enumerate(rows):
            report(done)

            if is_empty_row(row):
                continue

            raw_doc = parse_text(row.get("DOC"))
            situation = parse_text(row.get("SITUACAO_CADASTRAL")).upper()
            doc = normalize_document(raw_doc)

            if len(doc) != 14:
                errors.append({
                    "linha": line_number,
                    "coluna": "DOC",
                    "valor": raw_doc,
                    "motivo": "O CNPJ deve ter 14 dígitos.",
                })
                continue

            if not situation:
                errors.append({
                    "linha": line_number,
                    "coluna": "SITUACAO_CADASTRAL",
                    "valor": None,
                    "motivo": "A situação cadastral está vazia.",
                })
                continue

            previous = situations.get(doc)

            if previous is not None and previous != situation:
                errors.append({
                    "linha": line_number,
                    "coluna": "SITUACAO_CADASTRAL",
                    "valor": situation,
                    "motivo": (
                        "CNPJ repetido com situação diferente "
                        f"({previous})."
                    ),
                })
                continue

            situations[doc] = situation

        report(len(rows))

        if errors:
            raise csv_errors_exception(errors)

        if not situations:
            raise ValueError("O arquivo não possui nenhum CNPJ.")

        return situations

    except csv.Error as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )


def situations_summary(situations: dict) -> dict:
    """Quantidade de CNPJs na base, total e por situação."""

    counts = defaultdict(int)

    for situation in situations.values():
        counts[situation] += 1

    return {
        "total_cnpjs": len(situations),
        "por_situacao": dict(sorted(counts.items())),
    }


def load_situations() -> Optional[dict]:
    """
    Carrega a base salva em disco. Devolve None se ela não existir
    ou estiver corrompida (nesse caso, basta enviá-la de novo).
    """

    if not SITUATIONS_PATH.exists():
        return None

    try:
        with SITUATIONS_PATH.open("rb") as file:
            data = pickle.load(file)

    except (
        pickle.UnpicklingError,
        EOFError,
        OSError,
        AttributeError,
        ImportError,
    ):
        return None

    return data if isinstance(data, dict) else None


def save_situations(situations: dict) -> None:
    write_pickle(
        SITUATIONS_PATH,
        situations,
        "a base de situação cadastral",
    )


app.state.cnpj_situacoes = load_situations()

log_event(
    "aplicacao_iniciada",
    nivel_log=LOG_LEVEL,
    cors_qualquer_origem=CORS_ORIGINS == ["*"],
    base_situacao_em_disco=app.state.cnpj_situacoes is not None,
    total_cnpjs=len(app.state.cnpj_situacoes or {}),
)


# ============================================================
# FUNÇÕES AUXILIARES DO MOTOR DE REGRAS
# ============================================================

def money_to_json(cents: int) -> dict:
    """Representa o valor em centavos e em reais sem usar float."""

    return {
        "centavos": cents,
        "reais": f"{cents // 100},{cents % 100:02d}",
    }


def divide_round_half_up(numerator: int, denominator: int) -> int:
    """Divisão inteira com arredondamento half-up (valores >= 0)."""

    return (2 * numerator + denominator) // (2 * denominator)


def parse_scaled_int(value, decimals: int):
    """
    Converte um número do JSON em inteiro escalado por 10**decimals.
    Não faz conta com float: os dígitos são extraídos do texto.

    Exemplos (decimals=2):
        100      -> 10000
        100.5    -> 10050
        "15.25"  -> 1525

    Retorna None para valores negativos, não numéricos ou com
    mais casas decimais do que a escala permite.
    """

    if type(value) is bool or value is None:
        return None

    if type(value) is int:
        return value * 10 ** decimals if value >= 0 else None

    if not isinstance(value, (float, str)):
        return None

    match = re.fullmatch(
        r"(\d+)(?:\.(\d+))?",
        str(value).strip(),
        flags=re.ASCII,
    )

    if not match:
        return None

    integer_part = match.group(1)
    fraction_part = match.group(2) or ""

    if len(fraction_part) > decimals:
        return None

    return (
        int(integer_part) * 10 ** decimals
        + int(fraction_part.ljust(decimals, "0") or "0")
    )


def format_scaled(value: int, decimals: int) -> str:
    """Formata um inteiro escalado sem zeros à direita."""

    integer_part, fraction_part = divmod(value, 10 ** decimals)

    if not fraction_part:
        return str(integer_part)

    return (
        f"{integer_part}.{fraction_part:0{decimals}d}".rstrip("0")
    )


def concentration_percent(
    group_cents: int,
    total_cents: int
) -> int:
    """Concentração em centésimos de ponto percentual (1250 = 12,50%)."""

    if total_cents <= 0:
        return 0

    return divide_round_half_up(group_cents * 10000, total_cents)


def format_percent(hundredths: int) -> str:
    """Formata centésimos de percentual com duas casas."""

    return f"{hundredths // 100}.{hundredths % 100:02d}"


def format_brl(cents: int) -> str:
    """Formata centavos como 'R$ 1.234,56' sem usar float."""

    reais = f"{cents // 100:,}".replace(",", ".")

    return f"R$ {reais},{cents % 100:02d}"


def format_iso_date(value: str) -> str:
    """Converte 'aaaa-mm-dd' em 'dd/mm/aaaa'."""

    year, month, day = value.split("-")

    return f"{day}/{month}/{year}"


def explain_rule(item: dict) -> str:
    """
    Explica, em linguagem simples para a empresa, por que a
    regra barrou a nota (ou por que não pôde ser verificada).
    """

    code = item["codigo"]
    details = item.get("detalhes", {})

    if item["status"] == "NAO_VERIFICAVEL":
        return (
            f"Não foi possível verificar a regra de "
            f"{RULE_NAMES[code]}, porque a configuração do fundo "
            "está incompleta. A nota será analisada manualmente."
        )

    if code == "CNPJ_SACADO_VALIDO":
        return (
            f"O CNPJ do seu cliente ({details['cnpj']}) não é válido. "
            "Confira se ele foi digitado corretamente."
        )

    if code == "SITUACAO_CADASTRAL_SACADO":
        if not details["encontrado"]:
            return (
                f"Não encontramos o CNPJ do seu cliente "
                f"({details['cnpj']}) na base de situação cadastral. "
                "Sem essa confirmação, o fundo não pode comprar a nota."
            )

        accepted = ", ".join(details["situacoes_aceitas"])

        return (
            f"O CNPJ do seu cliente ({details['cnpj']}) está com "
            f"situação cadastral {details['situacao']} na Receita "
            f"Federal. O fundo só compra notas de clientes com "
            f"situação {accepted}."
        )

    if code == "TIPO_RECEBIVEL":
        accepted = ", ".join(str(item) for item in details["tipos_aceitos"])

        return (
            f"O fundo não compra notas do tipo "
            f"'{details['tipo_informado']}'. "
            f"Tipos aceitos: {accepted}."
        )

    if code == "VALOR_MINIMO":
        return (
            f"O valor da nota ({format_brl(details['valor_centavos'])}) "
            f"é menor que o mínimo aceito pelo fundo "
            f"({format_brl(details['minimo_centavos'])})."
        )

    if code == "NAO_VENCIDA":
        return (
            "A nota já estava vencida na data da antecipação: venceu em "
            f"{format_iso_date(details['data_vencimento'])} e a "
            "antecipação seria em "
            f"{format_iso_date(details['data_aquisicao'])}."
        )

    if code == "PRAZO_MAXIMO":
        return (
            f"A nota vence em {details['dias_ate_vencimento']} dias, "
            f"mas o fundo só aceita prazos de até "
            f"{details['prazo_maximo_dias']} dias."
        )

    if code == "PRAZO_MINIMO":
        return (
            f"A nota vence em {details['dias_ate_vencimento']} dias, "
            f"mas o fundo exige prazo de pelo menos "
            f"{details['prazo_minimo_dias']} dias."
        )

    if code in (
        "CONCENTRACAO_MAXIMA_SACADO",
        "CONCENTRACAO_MAXIMA_CEDENTE",
    ):
        who = (
            "deste cliente"
            if code == "CONCENTRACAO_MAXIMA_SACADO"
            else "desta empresa vendedora"
        )
        calculated = details["percentual_calculado"].replace(".", ",")
        maximum = details["percentual_maximo"].replace(".", ",")

        return (
            f"As notas {who} somam {calculated}% do total enviado "
            f"para o fundo, "
            f"acima do limite de {maximum}% que o fundo aceita para "
            "um mesmo grupo (mesma raiz de CNPJ)."
        )

    return item["mensagem"]


def evaluate_receivables(
    records: list,
    rules: Optional[tuple] = None,
    progress=None,
    include_details: bool = False,
    situations: Optional[dict] = None,
) -> dict:
    """
    Executa todos os validadores configurados e monta o
    resultado final: status e motivos de cada nota, valor
    líquido das aprovadas e o total a receber.

    rules = (config, flags, parameters); se omitido, usa as
    regras carregadas por POST /upload-rules.

    situations = {cnpj: situacao}; se omitido, usa a base
    carregada por POST /upload-situacoes.

    include_details=True acrescenta o registro completo, o
    resultado técnico de cada regra e a precificação de todas
    as notas (resposta bem maior).

    Desempenho: os parâmetros de cada regra são lidos e validados
    uma única vez, antes do laço; CNPJs repetidos usam cache; e,
    sem include_details, o resultado técnico de uma regra só é
    montado quando ela barra a nota (é o que vira o motivo).
    """

    if rules is None:
        if app.state.rules_config is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Carregue primeiro as regras usando POST /upload-rules."
                ),
            )

        rules = (
            app.state.rules_config,
            app.state.rule_flags,
            app.state.rule_parameters,
        )

    config, flags, parameters = rules

    unknown_codes = set(flags) - SUPPORTED_VALIDATORS

    if unknown_codes:
        raise HTTPException(
            status_code=400,
            detail={
                "mensagem": "Há validadores sem implementação.",
                "codigos": sorted(unknown_codes),
            },
        )

    # Base de situação cadastral: só é exigida se a regra estiver
    # ativa. As situações aceitas viram um frozenset uma única vez.
    situation_rule_active = (
        flags.get("SITUACAO_CADASTRAL_SACADO") is True
    )
    accepted_situations = None
    accepted_situations_list = []

    if situation_rule_active:
        if situations is None:
            situations = app.state.cnpj_situacoes

        if situations is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "A regra SITUACAO_CADASTRAL_SACADO está ativa, mas a "
                    "base de situação cadastral não foi carregada. "
                    "Envie-a em POST /upload-situacoes."
                ),
            )

        raw_accepted = parameters["SITUACAO_CADASTRAL_SACADO"].get(
            "situacoes_aceitas"
        )

        if (
            isinstance(raw_accepted, list)
            and raw_accepted
            and all(
                isinstance(item, str) and item.strip()
                for item in raw_accepted
            )
        ):
            accepted_situations_list = sorted({
                item.strip().upper() for item in raw_accepted
            })
            accepted_situations = frozenset(accepted_situations_list)

    full = include_details

    # --------------------------------------------------------
    # TOTAIS DA CONCENTRAÇÃO (uma passada)
    #
    # Concentração calculada dentro de cada fundo (DOC_FUNDO):
    # o grupo (raiz do CNPJ) é comparado com o total do próprio
    # fundo. Fundo e raiz de cada documento distinto são
    # calculados uma única vez (cache pelo texto original).
    # --------------------------------------------------------
    fund_cache = {}
    root_cache = {}
    total_cents = 0
    fund_totals = defaultdict(int)
    sacado_totals = defaultdict(int)
    cedente_totals = defaultdict(int)
    group_keys = []

    for record in records:
        value_cents = record["VALOR_NOMINAL_CENTAVOS"]

        raw_fund = record["DOC_FUNDO"]
        fund = fund_cache.get(raw_fund)

        if fund is None:
            fund = fund_cache[raw_fund] = fund_key(record)

        raw_sacado = record["DOC_SACADO"]
        sacado_root = root_cache.get(raw_sacado)

        if sacado_root is None:
            sacado_root = root_cache[raw_sacado] = cnpj_root(raw_sacado)

        raw_cedente = record["DOC_CEDENTE"]
        cedente_root = root_cache.get(raw_cedente)

        if cedente_root is None:
            cedente_root = root_cache[raw_cedente] = cnpj_root(raw_cedente)

        sacado_key = (fund, sacado_root)
        cedente_key = (fund, cedente_root)

        total_cents += value_cents
        fund_totals[fund] += value_cents
        sacado_totals[sacado_key] += value_cents
        cedente_totals[cedente_key] += value_cents
        group_keys.append((fund, sacado_key, cedente_key))

    # --------------------------------------------------------
    # PREPARAÇÃO DAS REGRAS
    #
    # Cada regra ativa vira uma função check(...) criada uma única
    # vez. Ela devolve o resultado técnico da regra (dict) ou None
    # quando a nota passou e o detalhamento não foi pedido.
    # Parâmetros inválidos geram NAO_VERIFICAVEL em todas as notas.
    # --------------------------------------------------------

    def constant_unverifiable(code, message):
        def check(record, value_cents, days, fund_cents, sacado_key,
                  cedente_key):
            return {
                "codigo": code,
                "status": "NAO_VERIFICAVEL",
                "mensagem": message,
            }

        return check

    def ignored(code):
        def check(record, value_cents, days, fund_cents, sacado_key,
                  cedente_key):
            return {
                "codigo": code,
                "status": "IGNORADA",
                "mensagem": "Regra desativada na configuração.",
            }

        return check

    # 1. CNPJ_SACADO_VALIDO
    def build_cnpj():
        cache = {}

        def check(record, value_cents, days, fund_cents, sacado_key,
                  cedente_key):
            raw_doc = record["DOC_SACADO"]
            valid = cache.get(raw_doc)

            if valid is None:
                valid = cache[raw_doc] = valid_cnpj(raw_doc)

            if valid and not full:
                return None

            return {
                "codigo": "CNPJ_SACADO_VALIDO",
                "status": "APROVADA" if valid else "REPROVADA",
                "mensagem": (
                    "CNPJ do sacado possui 14 dígitos e "
                    "dígitos verificadores válidos."
                    if valid else
                    "CNPJ do sacado inválido: deve possuir "
                    "14 dígitos e passar na validação módulo 11."
                ),
                "detalhes": {
                    "cnpj": raw_doc,
                    "quantidade_digitos": len(normalize_document(raw_doc)),
                    "validacao_modulo_11": valid,
                },
            }

        return check

    # 2. SITUACAO_CADASTRAL_SACADO
    #
    # Consulta a base enviada em POST /upload-situacoes
    # (dicionário CNPJ -> situação, busca O(1)) e compara
    # com "situacoes_aceitas" do JSON de regras.
    def build_registration():
        if accepted_situations is None:
            return constant_unverifiable(
                "SITUACAO_CADASTRAL_SACADO",
                "O parâmetro situacoes_aceitas deve ser uma "
                "lista não vazia de textos.",
            )

        # Cache por documento do sacado: cada CNPJ distinto é
        # normalizado e consultado uma única vez por avaliação.
        cache = {}

        def check(record, value_cents, days, fund_cents, sacado_key,
                  cedente_key):
            raw_doc = record["DOC_SACADO"]

            if raw_doc in cache:
                situation = cache[raw_doc]
            else:
                situation = situations.get(normalize_document(raw_doc))
                cache[raw_doc] = situation

            found = situation is not None

            if found:
                valid = situation in accepted_situations
            else:
                valid = not BLOCK_MISSING_CNPJ

            if valid and not full:
                return None

            if found and valid:
                message = f"Situação cadastral {situation} aceita."
            elif found:
                message = f"Situação cadastral {situation} não aceita."
            elif valid:
                message = (
                    "CNPJ não consta na base de situação cadastral; "
                    "a regra não barra a nota."
                )
            else:
                message = (
                    "CNPJ não encontrado na base de situação cadastral."
                )

            return {
                "codigo": "SITUACAO_CADASTRAL_SACADO",
                "status": "APROVADA" if valid else "REPROVADA",
                "mensagem": message,
                "detalhes": {
                    "cnpj": raw_doc,
                    "encontrado": found,
                    "situacao": situation,
                    "situacoes_aceitas": accepted_situations_list,
                },
            }

        return check

    # 3. TIPO_RECEBIVEL
    def build_type():
        accepted = parameters["TIPO_RECEBIVEL"].get("tipos_aceitos", [])

        # Uma string faria "in" comparar substrings.
        if not isinstance(accepted, list):
            return constant_unverifiable(
                "TIPO_RECEBIVEL",
                "O parâmetro tipos_aceitos deve ser uma lista.",
            )

        def check(record, value_cents, days, fund_cents, sacado_key,
                  cedente_key):
            informed = record["TIPO_RECEBIVEL"]
            valid = informed in accepted

            if valid and not full:
                return None

            return {
                "codigo": "TIPO_RECEBIVEL",
                "status": "APROVADA" if valid else "REPROVADA",
                "mensagem": (
                    "Tipo de recebível aceito."
                    if valid else
                    "Tipo de recebível não permitido."
                ),
                "detalhes": {
                    "tipo_informado": informed,
                    "tipos_aceitos": accepted,
                },
            }

        return check

    # 4. VALOR_MINIMO
    def build_minimum_value():
        minimum = parameters["VALOR_MINIMO"].get("valor_minimo")

        if minimum is None:
            return constant_unverifiable(
                "VALOR_MINIMO",
                "O parâmetro valor_minimo não foi informado.",
            )

        # Converte o limite do JSON (em reais) para centavos
        # uma única vez; a comparação é inteira.
        minimum_cents = parse_scaled_int(minimum, 2)

        if minimum_cents is None:
            return constant_unverifiable(
                "VALOR_MINIMO",
                "O parâmetro valor_minimo é inválido.",
            )

        def check(record, value_cents, days, fund_cents, sacado_key,
                  cedente_key):
            valid = value_cents >= minimum_cents

            if valid and not full:
                return None

            return {
                "codigo": "VALOR_MINIMO",
                "status": "APROVADA" if valid else "REPROVADA",
                "mensagem": (
                    "Valor nominal atende ao mínimo."
                    if valid else
                    "Valor nominal abaixo do mínimo permitido."
                ),
                "detalhes": {
                    "valor_centavos": value_cents,
                    "minimo_centavos": minimum_cents,
                },
            }

        return check

    # 5. NAO_VENCIDA
    def build_not_expired():
        def check(record, value_cents, days, fund_cents, sacado_key,
                  cedente_key):
            # days >= 0 equivale a vencimento >= aquisição.
            valid = days >= 0

            if valid and not full:
                return None

            return {
                "codigo": "NAO_VENCIDA",
                "status": "APROVADA" if valid else "REPROVADA",
                "mensagem": (
                    "Recebível não estava vencido na data de aquisição."
                    if valid else
                    "Recebível já estava vencido na data de aquisição."
                ),
                "detalhes": {
                    "data_aquisicao": record["DATA_AQUISICAO"].isoformat(),
                    "data_vencimento": (
                        record["DATA_VENCIMENTO_ORIGINAL"].isoformat()
                    ),
                    "dias_ate_vencimento": days,
                },
            }

        return check

    # 6. PRAZO_MAXIMO
    def build_maximum_term():
        maximum = parameters["PRAZO_MAXIMO"].get("prazo_maximo_dias")

        if type(maximum) is not int or maximum < 0:
            return constant_unverifiable(
                "PRAZO_MAXIMO",
                "O parâmetro prazo_maximo_dias é inválido.",
            )

        def check(record, value_cents, days, fund_cents, sacado_key,
                  cedente_key):
            valid = days <= maximum

            if valid and not full:
                return None

            return {
                "codigo": "PRAZO_MAXIMO",
                "status": "APROVADA" if valid else "REPROVADA",
                "mensagem": (
                    "Prazo dentro do limite máximo."
                    if valid else
                    "Prazo excede o limite máximo permitido."
                ),
                "detalhes": {
                    "dias_ate_vencimento": days,
                    "prazo_maximo_dias": maximum,
                },
            }

        return check

    # 7 e 9. CONCENTRACAO_MAXIMA_SACADO / CONCENTRACAO_MAXIMA_CEDENTE
    def build_concentration(code, totals, use_sacado, who):
        maximum_scaled = parse_scaled_int(
            parameters[code].get("percentual_maximo"),
            PERCENT_DECIMALS,
        )

        if maximum_scaled is None or maximum_scaled > 100 * PERCENT_SCALE:
            return constant_unverifiable(
                code,
                "O parâmetro percentual_maximo é inválido.",
            )

        maximum_text = format_scaled(maximum_scaled, PERCENT_DECIMALS)

        def check(record, value_cents, days, fund_cents, sacado_key,
                  cedente_key):
            key = sacado_key if use_sacado else cedente_key
            group_cents = totals[key]

            # Comparação exata por multiplicação cruzada:
            # grupo / total do fundo * 100 <= máximo
            valid = (
                group_cents * 100 * PERCENT_SCALE
                <= maximum_scaled * fund_cents
            )

            if valid and not full:
                return None

            return {
                "codigo": code,
                "status": "APROVADA" if valid else "REPROVADA",
                "mensagem": (
                    f"Concentração por {who} dentro do limite."
                    if valid else
                    f"Concentração por {who} excede o limite."
                ),
                "detalhes": {
                    "raiz_cnpj": key[1],
                    "fundo": record["DOC_FUNDO"],
                    "valor_grupo_centavos": group_cents,
                    "carteira_total_centavos": fund_cents,
                    "percentual_calculado": format_percent(
                        concentration_percent(group_cents, fund_cents)
                    ),
                    "percentual_maximo": maximum_text,
                },
            }

        return check

    # 8. PRAZO_MINIMO
    def build_minimum_term():
        minimum = parameters["PRAZO_MINIMO"].get("prazo_minimo_dias")

        if type(minimum) is not int or minimum < 0:
            return constant_unverifiable(
                "PRAZO_MINIMO",
                "O parâmetro prazo_minimo_dias é inválido.",
            )

        def check(record, value_cents, days, fund_cents, sacado_key,
                  cedente_key):
            valid = days >= minimum

            if valid and not full:
                return None

            return {
                "codigo": "PRAZO_MINIMO",
                "status": "APROVADA" if valid else "REPROVADA",
                "mensagem": (
                    "Prazo atende ao limite mínimo."
                    if valid else
                    "Prazo inferior ao mínimo permitido."
                ),
                "detalhes": {
                    "dias_ate_vencimento": days,
                    "prazo_minimo_dias": minimum,
                },
            }

        return check

    builders = {
        "CNPJ_SACADO_VALIDO": build_cnpj,
        "SITUACAO_CADASTRAL_SACADO": build_registration,
        "TIPO_RECEBIVEL": build_type,
        "VALOR_MINIMO": build_minimum_value,
        "NAO_VENCIDA": build_not_expired,
        "PRAZO_MAXIMO": build_maximum_term,
        "CONCENTRACAO_MAXIMA_SACADO": lambda: build_concentration(
            "CONCENTRACAO_MAXIMA_SACADO", sacado_totals, True, "sacado"
        ),
        "PRAZO_MINIMO": build_minimum_term,
        "CONCENTRACAO_MAXIMA_CEDENTE": lambda: build_concentration(
            "CONCENTRACAO_MAXIMA_CEDENTE", cedente_totals, False, "cedente"
        ),
    }

    # Ordem de execução fixa (é a ordem de "barrado_por"). Regras
    # ausentes do JSON não são executadas; as desativadas só
    # aparecem (como IGNORADA) no detalhamento.
    plan = []

    for code in RULE_ORDER:
        if code not in flags:
            continue

        if flags[code]:
            plan.append(builders[code]())
        elif full:
            plan.append(ignored(code))

    # Precificação validada uma única vez.
    pricing_params = pricing_setup(config)
    pricing_valid = not isinstance(pricing_params, dict)

    if pricing_valid:
        rate_scaled, base_days = pricing_params
        pricing_denominator = 100 * PERCENT_SCALE * base_days

    # --------------------------------------------------------
    # LAÇO PRINCIPAL
    # --------------------------------------------------------
    notes = []
    report = progress_reporter(progress, "avaliacao", len(records))
    iso_dates = {}

    # Totais do resumo, acumulados em inteiros (centavos).
    status_counts = {"APROVADO": 0, "BARRADO": 0, "PENDENTE": 0}
    rejections_by_rule = defaultdict(int)
    approved_nominal_cents = 0
    approved_discount_cents = 0
    approved_net_cents = 0

    for index, record in enumerate(records):
        report(index)

        value_cents = record["VALOR_NOMINAL_CENTAVOS"]
        fund, sacado_key, cedente_key = group_keys[index]
        fund_cents = fund_totals[fund]
        maturity_date = record["DATA_VENCIMENTO_ORIGINAL"]
        days = (maturity_date - record["DATA_AQUISICAO"]).days

        rejected = []
        unverifiable = []
        rule_results = [] if full else None

        for check in plan:
            item = check(
                record, value_cents, days, fund_cents, sacado_key,
                cedente_key,
            )

            if item is None:
                continue

            item_status = item["status"]

            if item_status == "REPROVADA":
                rejected.append(item)
            elif item_status == "NAO_VERIFICAVEL":
                unverifiable.append(item)

            if full:
                rule_results.append(item)

        # ----------------------------------------------------
        # ELEGIBILIDADE
        # ----------------------------------------------------

        if rejected:
            status = STATUS_BY_ELIGIBILITY["NAO_ELEGIVEL"]
        elif unverifiable:
            status = STATUS_BY_ELIGIBILITY["PENDENTE_VERIFICACAO"]
        else:
            status = STATUS_BY_ELIGIBILITY["ELEGIVEL"]

        status_counts[status] += 1

        note = {
            "linha": record["LINHA"],
            "id": record["NU_DOCUMENTO"],
            "status": status,
        }

        if rejected:
            note["barrado_por"] = [item["codigo"] for item in rejected]

            for item in rejected:
                rejections_by_rule[item["codigo"]] += 1

        if rejected or unverifiable:
            # dict.fromkeys remove explicações repetidas
            # (ex.: CNPJ inválido barra duas regras) mantendo a ordem.
            note["motivos"] = list(dict.fromkeys(
                explain_rule(item)
                for item in rejected + unverifiable
            ))

        maturity_iso = iso_dates.get(maturity_date)

        if maturity_iso is None:
            maturity_iso = iso_dates[maturity_date] = maturity_date.isoformat()

        note["cedente"] = record["NOME_CEDENTE"]
        note["sacado"] = record["NOME_SACADO"]
        note["doc_sacado"] = record["DOC_SACADO"]
        note["tipo_recebivel"] = record["TIPO_RECEBIVEL"]
        note["vencimento"] = maturity_iso
        note["valor_nominal"] = money_to_json(value_cents)

        # O preço só é calculado quando necessário: para as
        # aprovadas ou quando o detalhamento foi pedido.
        pricing = None
        discount_cents = None

        if full:
            pricing = calculate_pricing(record, config, pricing_params)

            if status == "APROVADO" and pricing["status"] == "CALCULADA":
                discount_cents = pricing["desconto"]["centavos"]
                net_cents = pricing["valor_liquido_estimado"]["centavos"]

        elif status == "APROVADO" and pricing_valid and days >= 0:
            # Mesmo cálculo de calculate_pricing, sem montar o dict.
            discount_cents = divide_round_half_up(
                value_cents * rate_scaled * days,
                pricing_denominator,
            )
            net_cents = value_cents - discount_cents

            if net_cents < 0:
                discount_cents = None

        if discount_cents is not None:
            note["desconto"] = money_to_json(discount_cents)
            note["valor_liquido"] = money_to_json(net_cents)

            approved_nominal_cents += value_cents
            approved_discount_cents += discount_cents
            approved_net_cents += net_cents

        if full:
            note["recebivel"] = record_to_json(record)
            note["regras"] = rule_results
            note["precificacao"] = pricing

        notes.append(note)

    report(len(records))

    return {
        "status": "success",
        "fundo": config.get("fundo"),
        "precificacao": config.get("precificacao"),
        "resumo": {
            "total_notas": len(notes),
            "aprovadas": status_counts["APROVADO"],
            "barradas": status_counts["BARRADO"],
            "pendentes": status_counts["PENDENTE"],
            "barradas_por_regra": dict(rejections_by_rule),
            "valor_nominal_total": money_to_json(total_cents),
            "valor_nominal_aprovado": money_to_json(
                approved_nominal_cents
            ),
            "desconto_total": money_to_json(approved_discount_cents),
            "total_a_receber": money_to_json(approved_net_cents),
            "total_a_receber_formatado": format_brl(approved_net_cents),
        },
        "universo_concentracao": (
            "Valor nominal total dos recebíveis válidos enviados "
            "nesta requisição, separado por fundo (DOC_FUNDO): cada "
            "grupo de CNPJ é comparado com o total do próprio fundo."
        ),
        "notas": notes,
    }


# ============================================================
# PRECIFICAÇÃO
# ============================================================

def pricing_setup(config: dict):
    """
    Valida a configuração de precificação uma única vez.

    Devolve (taxa_escalada, base_dias) quando ela é válida, ou o
    dict NAO_CALCULADA com o motivo quando não é.
    """

    pricing = config.get("precificacao")

    if not isinstance(pricing, dict):
        pricing = {}

    if pricing.get("metodo") != "DESCONTO_SIMPLES":
        return {
            "status": "NAO_CALCULADA",
            "motivo": (
                "Método de precificação ausente ou não suportado."
            ),
        }

    rate_scaled = parse_scaled_int(
        pricing.get("taxa_mensal_percentual"),
        PERCENT_DECIMALS,
    )

    base_days = pricing.get("base_dias")

    if (
        rate_scaled is None
        or type(base_days) is not int
        or base_days <= 0
    ):
        return {
            "status": "NAO_CALCULADA",
            "motivo": "Parâmetros de precificação inválidos.",
        }

    return rate_scaled, base_days


def calculate_pricing(record: dict, config: dict, setup=None) -> dict:
    """
    Calcula o desconto simples proporcional ao prazo.

    Fórmula implementada:
        desconto = valor_nominal * taxa * dias / base_dias

    A taxa é expressa em percentual e convertida para inteiro
    com PERCENT_DECIMALS casas (2% -> 20000). Todo o cálculo é
    feito com inteiros (centavos) e o arredondamento half-up é
    aplicado uma única vez, no resultado final.

    setup é o retorno de pricing_setup(config); se omitido, é
    calculado aqui.
    """

    if setup is None:
        setup = pricing_setup(config)

    if isinstance(setup, dict):
        return dict(setup)

    rate_scaled, base_days = setup
    nominal_cents = record["VALOR_NOMINAL_CENTAVOS"]

    days = (
        record["DATA_VENCIMENTO_ORIGINAL"]
        - record["DATA_AQUISICAO"]
    ).days

    if days < 0:
        return {
            "status": "NAO_CALCULADA",
            "motivo": (
                "O recebível já estava vencido na data de aquisição."
            ),
            "dias_ate_vencimento": days,
        }

    discount_cents = divide_round_half_up(
        nominal_cents * rate_scaled * days,
        100 * PERCENT_SCALE * base_days,
    )

    net_cents = nominal_cents - discount_cents

    if net_cents < 0:
        return {
            "status": "NAO_CALCULADA",
            "motivo": "O desconto excede o valor nominal.",
            "valor_nominal": money_to_json(nominal_cents),
            "desconto_centavos": discount_cents,
        }

    return {
        "status": "CALCULADA",
        "metodo": "DESCONTO_SIMPLES",
        "dias_ate_vencimento": days,
        "taxa_mensal_percentual": format_scaled(
            rate_scaled,
            PERCENT_DECIMALS,
        ),
        "base_dias": base_days,
        "valor_nominal": money_to_json(nominal_cents),
        "desconto": money_to_json(discount_cents),
        "valor_liquido_estimado": money_to_json(net_cents),
    }


# ============================================================
# RESPOSTA JSON
# ============================================================

def json_bytes(data) -> bytes:
    """
    Serializa o resultado direto com json.dumps (mesmo formato do
    JSONResponse). O resultado já é JSON puro (dict, list, str,
    int, None), então o jsonable_encoder do FastAPI, que percorre
    o resultado inteiro de novo, não é necessário.
    """

    return json.dumps(
        data,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def json_response(content: bytes) -> Response:
    return Response(content=content, media_type="application/json")


# ============================================================
# FRONT-END
# ============================================================

@app.get("/", include_in_schema=False)
async def frontend():
    """Entrega o painel (index.html na mesma pasta do main.py)."""

    if not FRONTEND_PATH.exists():
        raise HTTPException(
            status_code=404,
            detail="Coloque o index.html na mesma pasta do main.py.",
        )

    return FileResponse(FRONTEND_PATH)


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
async def health():
    return {"status": "ok"}


# ============================================================
# PARSE CSV
# ============================================================

@app.post("/parse-csv")
async def parse_csv(file: UploadFile = File(...)):
    content = await file.read()
    start = time.perf_counter()
    text = read_utf8_upload(content)
    records = parse_csv_records(text)

    log_event(
        "csv_validado",
        tamanho_bytes=len(content),
        linhas=len(records),
        duracao_ms=elapsed_ms(start),
    )

    return {
        "total": len(records),
        "recebiveis": [
            record_to_json(record)
            for record in records
        ],
        "convencao_monetaria": (
            "VALOR_NOMINAL_CENTAVOS é um inteiro em centavos."
        ),
    }


# ============================================================
# UPLOAD DAS REGRAS
# ============================================================

def parse_rules_config(content: bytes, progress=None) -> tuple:
    """
    Decodifica e valida o JSON de regras.

    Retorna (config, flags, parameters) sem alterar o estado
    global da aplicação.
    """

    try:
        config = json.loads(content.decode("utf-8-sig"))

    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="O arquivo JSON deve estar codificado em UTF-8."
        )

    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=400,
            detail=f"JSON inválido: {error}"
        )

    if not isinstance(config, dict):
        raise HTTPException(
            status_code=400,
            detail="A raiz do JSON deve ser um objeto."
        )

    validators = config.get("validadores")

    if not isinstance(validators, list):
        raise HTTPException(
            status_code=400,
            detail="O campo 'validadores' deve ser uma lista."
        )

    pricing = config.get("precificacao")

    if pricing is not None and not isinstance(pricing, dict):
        raise HTTPException(
            status_code=400,
            detail="O campo 'precificacao' deve ser um objeto ou nulo."
        )

    flags = {}
    parameters = {}
    report = progress_reporter(progress, "regras", len(validators))

    for done, validator in enumerate(validators):
        report(done)

        if not isinstance(validator, dict):
            raise HTTPException(
                status_code=400,
                detail="Cada validador deve ser um objeto."
            )

        code = validator.get("codigo")
        active = validator.get("ativo")
        params = validator.get("parametros")

        if not isinstance(code, str) or not code.strip():
            raise HTTPException(
                status_code=400,
                detail="Cada validador deve possuir um código válido."
            )

        code = code.strip()

        if type(active) is not bool:
            raise HTTPException(
                status_code=400,
                detail=f"O campo 'ativo' de {code} deve ser booleano."
            )

        if not isinstance(params, dict):
            raise HTTPException(
                status_code=400,
                detail=f"Os parâmetros de {code} devem ser um objeto."
            )

        if code in flags:
            raise HTTPException(
                status_code=400,
                detail=f"Código de validador duplicado: {code}"
            )

        if code not in SUPPORTED_VALIDATORS:
            raise HTTPException(
                status_code=400,
                detail=f"Validador sem implementação: {code}"
            )

        flags[code] = active
        parameters[code] = params

    report(len(validators))

    return config, flags, parameters


@app.post("/upload-rules")
async def upload_rules(file: UploadFile = File(...)):
    content = await file.read()
    config, flags, parameters = parse_rules_config(content)

    app.state.rules_config = config
    app.state.rule_flags = flags
    app.state.rule_parameters = parameters

    active_count = sum(flags.values())

    log_event(
        "regras_carregadas",
        tamanho_bytes=len(content),
        total_validadores=len(flags),
        validadores_ativos=sorted(code for code, on in flags.items() if on),
    )

    return {
        "status": "success",
        "fundo": config.get("fundo"),
        "total_validadores": len(flags),
        "validadores_ativos": active_count,
        "validadores_inativos": len(flags) - active_count,
        "flags": flags,
        "parametros": parameters,
        "precificacao": config.get("precificacao"),
    }


# ============================================================
# CONSULTA DAS REGRAS
# ============================================================

@app.get("/rules")
async def get_rules():
    if app.state.rules_config is None:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma configuração de regras foi carregada."
        )

    return app.state.rules_config


# ============================================================
# BASE DE SITUAÇÃO CADASTRAL (UPLOAD E CONSULTA)
# ============================================================

@app.post("/upload-situacoes")
async def upload_situations(file: UploadFile = File(...)):
    """
    Carrega o CSV DOC;SITUACAO_CADASTRAL, substitui a base atual
    e a salva em disco (pickle).
    """

    content = await file.read()
    start = time.perf_counter()
    situations = parse_situations_csv(read_utf8_upload(content))

    save_situations(situations)
    app.state.cnpj_situacoes = situations

    log_event(
        "base_situacao_carregada",
        tamanho_bytes=len(content),
        duracao_ms=elapsed_ms(start),
        **situations_summary(situations),
    )

    return {
        "status": "success",
        **situations_summary(situations),
    }


@app.get("/situacoes")
async def get_situations():
    if app.state.cnpj_situacoes is None:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma base de situação cadastral foi carregada."
        )

    return situations_summary(app.state.cnpj_situacoes)


# ============================================================
# AVALIAÇÃO DOS RECEBÍVEIS
# ============================================================

@app.post("/avaliar-recebiveis")
async def evaluate_csv(
    file: UploadFile = File(...),
    detalhes: bool = False,
):
    content = await file.read()
    text = read_utf8_upload(content)

    with gc_paused():
        start = time.perf_counter()
        records = parse_csv_records(text)
        csv_ms = elapsed_ms(start)

        start = time.perf_counter()
        result = evaluate_receivables(records, include_details=detalhes)
        evaluation_ms = elapsed_ms(start)

        start = time.perf_counter()
        body = json_bytes(result)
        json_ms = elapsed_ms(start)

    log_event(
        "avaliacao_concluida",
        modo="direto",
        detalhes=detalhes,
        tamanho_bytes=len(content),
        resposta_bytes=len(body),
        duracao_csv_ms=csv_ms,
        duracao_avaliacao_ms=evaluation_ms,
        duracao_json_ms=json_ms,
        **result_metrics(result),
    )

    return json_response(body)


# ============================================================
# AVALIAÇÃO COM BARRA DE PROGRESSO (JOBS + SSE)
#
# Fluxo para o front-end:
#   1. POST /avaliar-recebiveis/jobs      -> devolve job_id (202)
#   2. GET  /jobs/{job_id}/progresso      -> eventos SSE em tempo real
#   3. GET  /jobs/{job_id}/resultado      -> resultado final
# ============================================================

jobs = {}
jobs_lock = threading.Lock()


def create_job(stages: list) -> dict:
    """Registra um job novo e descarta jobs finalizados antigos."""

    now = time.monotonic()

    with jobs_lock:
        expired = [
            job_id
            for job_id, job in jobs.items()
            if job["finalizado_em"] is not None
            and now - job["finalizado_em"] > JOB_TTL_SECONDS
        ]

        for job_id in expired:
            del jobs[job_id]

        if expired:
            log_event("jobs_expirados_removidos", quantidade=len(expired))

        job = {
            "id": uuid.uuid4().hex,
            "status": "na_fila",
            "etapas": stages,
            "etapa": None,
            "descricao": "Aguardando início do processamento.",
            "processados": 0,
            "total": 0,
            "percentual": 0.0,
            "versao": 0,
            "finalizado_em": None,
            "resultado": None,
            "erro": None,
        }

        jobs[job["id"]] = job

    return job


def update_job(job: dict, **changes) -> None:
    with jobs_lock:
        job.update(changes)
        job["versao"] += 1


def job_progress_callback(job: dict):
    """Converte o progresso de cada etapa em percentual geral."""

    stages = job["etapas"]
    total_weight = sum(STAGE_WEIGHTS[stage] for stage in stages)

    def progress(stage: str, done: int, total: int) -> None:
        finished_weight = sum(
            STAGE_WEIGHTS[item]
            for item in stages[:stages.index(stage)]
        )

        fraction = done / total if total else 1
        percent = (
            (finished_weight + STAGE_WEIGHTS[stage] * fraction)
            * 100 / total_weight
        )

        update_job(
            job,
            status="processando",
            etapa=stage,
            descricao=STAGE_LABELS[stage],
            processados=done,
            total=total,
            percentual=round(percent, 1),
        )

    return progress


def job_snapshot(job: dict) -> dict:
    """Cópia do estado do job sem o resultado (que pode ser grande)."""

    with jobs_lock:
        return {
            "job_id": job["id"],
            "status": job["status"],
            "etapas": list(job["etapas"]),
            "etapa": job["etapa"],
            "descricao": job["descricao"],
            "processados": job["processados"],
            "total": job["total"],
            "faltam": job["total"] - job["processados"],
            "percentual": job["percentual"],
            "versao": job["versao"],
            "erro": job["erro"],
        }


def get_job(job_id: str) -> dict:
    with jobs_lock:
        job = jobs.get(job_id)

    if job is None:
        raise HTTPException(
            status_code=404,
            detail="Job não encontrado ou expirado.",
        )

    return job


def run_evaluation_job(
    job: dict,
    csv_text: str,
    rules_content: Optional[bytes],
    rules: Optional[tuple],
    situations_text: Optional[str],
    situations: Optional[dict],
    include_details: bool,
) -> None:
    """Executado em segundo plano (thread pool do FastAPI)."""

    progress = job_progress_callback(job)
    job_start = time.perf_counter()
    log_event("job_iniciado", job_id=job["id"], etapas=job["etapas"])

    try:
        with gc_paused():
            if rules_content is not None:
                rules = parse_rules_config(rules_content, progress)

            if situations_text is not None:
                situations = parse_situations_csv(situations_text, progress)

            start = time.perf_counter()
            records = parse_csv_records(csv_text, progress)
            csv_ms = elapsed_ms(start)

            start = time.perf_counter()
            result = evaluate_receivables(
                records,
                rules=rules,
                progress=progress,
                include_details=include_details,
                situations=situations,
            )
            evaluation_ms = elapsed_ms(start)

            # O JSON é gerado aqui, ainda em segundo plano, e guardado
            # pronto: GET /resultado só envia os bytes (e o job ocupa
            # bem menos memória do que o dict com 100 mil notas).
            start = time.perf_counter()
            body = json_bytes(result)
            json_ms = elapsed_ms(start)
            metrics = result_metrics(result)
            del result, records

        update_job(
            job,
            status="concluido",
            descricao="Processamento concluído.",
            percentual=100.0,
            resultado=body,
            finalizado_em=time.monotonic(),
        )

        log_event(
            "avaliacao_concluida",
            modo="job",
            job_id=job["id"],
            detalhes=include_details,
            resposta_bytes=len(body),
            duracao_csv_ms=csv_ms,
            duracao_avaliacao_ms=evaluation_ms,
            duracao_json_ms=json_ms,
            duracao_total_ms=elapsed_ms(job_start),
            **metrics,
        )

    except HTTPException as error:
        # O detalhe do erro não vai para o log (pode citar valores
        # do arquivo); erros de CSV já foram resumidos por coluna.
        log_event(
            "job_falhou",
            level=logging.WARNING,
            job_id=job["id"],
            status_code=error.status_code,
            duracao_total_ms=elapsed_ms(job_start),
        )

        update_job(
            job,
            status="erro",
            descricao="O processamento foi interrompido.",
            erro={
                "status_code": error.status_code,
                "detail": error.detail,
            },
            finalizado_em=time.monotonic(),
        )

    except Exception as error:
        log_event(
            "job_erro_inesperado",
            level=logging.ERROR,
            exc_info=sys.exc_info(),
            job_id=job["id"],
            duracao_total_ms=elapsed_ms(job_start),
        )

        update_job(
            job,
            status="erro",
            descricao="Erro inesperado no processamento.",
            erro={"status_code": 500, "detail": str(error)},
            finalizado_em=time.monotonic(),
        )


@app.post("/avaliar-recebiveis/jobs", status_code=202)
async def create_evaluation_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    regras: Optional[UploadFile] = File(None),
    situacoes: Optional[UploadFile] = File(None),
    detalhes: bool = False,
):
    """
    Inicia a avaliação em segundo plano.

    O JSON de regras e o CSV de situação cadastral são opcionais:
    se enviados, valem só neste job; caso contrário, são usados os
    carregados em POST /upload-rules e POST /upload-situacoes.
    """

    csv_text = read_utf8_upload(await file.read())
    rules_content = await regras.read() if regras is not None else None
    situations_text = (
        read_utf8_upload(await situacoes.read())
        if situacoes is not None
        else None
    )
    rules = None
    situations = None

    if rules_content is None:
        if app.state.rules_config is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Envie o JSON de regras neste pedido ou carregue-o "
                    "antes em POST /upload-rules."
                ),
            )

        # Fotografia das regras atuais: um novo upload durante o
        # processamento não altera este job.
        rules = (
            app.state.rules_config,
            app.state.rule_flags,
            app.state.rule_parameters,
        )

    if situations_text is None:
        # Mesma ideia: fotografia da base atual. Um novo upload
        # cria outro dicionário e não altera este job.
        situations = app.state.cnpj_situacoes

        if (
            situations is None
            and rules is not None
            and rules[1].get("SITUACAO_CADASTRAL_SACADO") is True
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "A regra SITUACAO_CADASTRAL_SACADO está ativa: envie "
                    "a base de situação cadastral neste pedido ou "
                    "carregue-a antes em POST /upload-situacoes."
                ),
            )

    stages = ["csv", "avaliacao"]

    if situations_text is not None:
        stages.insert(0, "situacoes")

    if rules_content is not None:
        stages.insert(0, "regras")

    job = create_job(stages)

    log_event(
        "job_criado",
        job_id=job["id"],
        etapas=stages,
        detalhes=detalhes,
        tamanho_bytes=len(csv_text.encode("utf-8")),
        regras_no_pedido=rules_content is not None,
        base_no_pedido=situations_text is not None,
    )

    background_tasks.add_task(
        run_evaluation_job,
        job,
        csv_text,
        rules_content,
        rules,
        situations_text,
        situations,
        detalhes,
    )

    return {
        "job_id": job["id"],
        "status": job["status"],
        "progresso_url": f"/jobs/{job['id']}/progresso",
        "resultado_url": f"/jobs/{job['id']}/resultado",
    }


@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Consulta pontual do progresso (alternativa ao SSE)."""

    return job_snapshot(get_job(job_id))


@app.get("/jobs/{job_id}/progresso")
async def stream_job_progress(job_id: str):
    """
    Envia o progresso por Server-Sent Events.

    Eventos: "progresso" (a cada mudança) e, ao final,
    "concluido" ou "erro".
    """

    job = get_job(job_id)

    async def events():
        last_version = -1

        while True:
            snapshot = job_snapshot(job)

            if snapshot["versao"] != last_version:
                last_version = snapshot["versao"]

                yield {
                    "event": "progresso",
                    "data": json.dumps(snapshot, ensure_ascii=False),
                }

            if snapshot["status"] in ("concluido", "erro"):
                yield {
                    "event": snapshot["status"],
                    "data": json.dumps(snapshot, ensure_ascii=False),
                }
                return

            await asyncio.sleep(PROGRESS_INTERVAL_SECONDS)

    return EventSourceResponse(events())


@app.get("/jobs/{job_id}/resultado")
async def get_job_result(job_id: str):
    job = get_job(job_id)
    snapshot = job_snapshot(job)

    if snapshot["status"] == "erro":
        raise HTTPException(
            status_code=snapshot["erro"]["status_code"],
            detail=snapshot["erro"]["detail"],
        )

    if snapshot["status"] != "concluido":
        raise HTTPException(
            status_code=409,
            detail={
                "mensagem": "O processamento ainda não terminou.",
                "progresso": snapshot,
            },
        )

    return json_response(job["resultado"])


# ============================================================
# TESTE DE DUPLICATAS
# ============================================================

@app.post("/test-duplicates")
async def test_duplicates(file: UploadFile = File(...)):
    text = read_utf8_upload(await file.read())

    try:
        reader = csv_reader_from_text(text)

        historical_keys = load_receivables()
        seen_in_upload = set()
        new_keys = set()

        total = 0
        duplicates_in_csv = 0
        duplicates_in_history = 0

        samples_in_csv = []
        samples_in_history = []
        max_examples = 10

        for row in reader:
            if is_empty_row(row):
                continue

            if None in row:
                raise ValueError(
                    "Uma linha possui campos além dos cabeçalhos."
                )

            missing_values = [
                field
                for field in EXPECTED_FIELDS
                if row.get(field) is None
            ]

            if missing_values:
                raise ValueError(
                    "Uma linha possui campos ausentes: "
                    + ", ".join(sorted(missing_values))
                )

            total += 1
            key = receivable_key(row)

            if key in seen_in_upload:
                duplicates_in_csv += 1

                if len(samples_in_csv) < max_examples:
                    samples_in_csv.append(
                        example_from_row(row)
                    )

            elif key in historical_keys:
                duplicates_in_history += 1

                if len(samples_in_history) < max_examples:
                    samples_in_history.append(
                        example_from_row(row)
                    )

            else:
                new_keys.add(key)

            seen_in_upload.add(key)

        updated_history = historical_keys | new_keys
        save_receivables(updated_history)

        log_event(
            "duplicidade_testada",
            linhas=total,
            duplicatas_no_csv=duplicates_in_csv,
            duplicatas_no_historico=duplicates_in_history,
            novas_chaves=len(new_keys),
            chaves_no_historico=len(updated_history),
        )

        return {
            "status": "success",
            "modo": "teste",
            "total_linhas_analisadas": total,
            "chaves_unicas_neste_csv": len(seen_in_upload),
            "duplicatas_no_csv": duplicates_in_csv,
            "duplicatas_no_historico_anterior": duplicates_in_history,
            "novas_chaves_registradas_no_historico_teste": len(new_keys),
            "exemplos_duplicatas_no_csv": samples_in_csv,
            "exemplos_duplicatas_no_historico": samples_in_history,
            "aviso": (
                "O histórico registra chaves analisadas para fins "
                "de teste, não recebíveis efetivamente adquiridos."
            ),
        }

    except csv.Error as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )

    except ValueError as error:
        raise HTTPException(
            status_code=400,
            detail=str(error)
        )
