"""cluster_sincronyzer.py

Processa e reconcilia clusters de coredumps entre o estado armazenado no banco e um
resultado novo (CSV) produzido por um algoritmo de clusterização externo.

Fluxo resumido:
1. Extrai clusters atuais do banco.
2. Lê CSV (coredump_path, temp_cluster_id) e traduz para IDs reais.
3. Calcula mapeamentos (evoluções / novos / removidos) via similaridade (Jaccard).
4. Aplica mutações (delete / insert / update) mantendo IDs de clusters evoluídos.
5. Gera sumário do processo.

Variáveis de ambiente relevantes: (nenhuma diretamente aqui ainda; limiar poderá virar ENV futura)

TODO: validar formato do CSV antes de processar (headers, colunas).
TODO: mover limiar de similaridade para configuração externa (env ou DB).
"""

from __future__ import annotations

import csv
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, MutableMapping, Set, Tuple

logger = logging.getLogger("backend.cluster_sincronyzer")

# ---------------------------------------------------------------------------
# Constantes configuráveis
# ---------------------------------------------------------------------------
LIMIAR_SIMILARIDADE: float = 0.7  # Threshold de similaridade para reconciliação
CLUSTER_NOME_PREFIXO: str = "Cluster"
CLUSTER_NOME_VAZIO_PREFIXO: str = "Cluster_Vazio"
CLUSTER_NOME_FALLBACK_PREFIXO: str = "Cluster_Inesperado"
TEST_CSV_PATH: Path = Path("db") / "damicore" / "clusters.csv"

# ---------------------------------------------------------------------------
# Imports de módulos internos (db_manager, jaccard)
# ---------------------------------------------------------------------------
try:  # Import explícito para clareza e evitar poluir namespace
    from db_manager import (
        get_clustered_coredumps,
        get_cluster_name,
        unassign_cluster_from_coredumps,
        delete_cluster,
        add_cluster,
        assign_cluster_to_coredump,
        list_all_coredumps,
        get_coredump_info_by_id,
        create_database,
    )
except ImportError as exc:  # pragma: no cover - erro crítico de ambiente
    logger.exception("Módulo db_manager não encontrado.")
    raise SystemExit(1) from exc

try:
    from cluster_reconciler import reconciliar_clusters_misto
  # Função central de matching
except ImportError as exc:  # pragma: no cover
    logger.exception("Módulo cluster_reconciler não encontrado.")
    raise SystemExit(1) from exc


# ---------------------------------------------------------------------------
# Funções utilitárias principais
# ---------------------------------------------------------------------------
def extrair_clusters_do_db() -> Dict[int, Set[int]]:
    """Retorna clusters atuais do banco no formato {cluster_id: {coredump_ids}}."""
    clustered_dumps: Iterable[Tuple[int, int]] = get_clustered_coredumps()
    clusters_antigos: Dict[int, Set[int]] = defaultdict(set)
    for coredump_id, cluster_id in clustered_dumps:
        clusters_antigos[cluster_id].add(coredump_id)
    logger.debug("Clusters extraídos do DB: %s", clusters_antigos)
    return dict(clusters_antigos)


def gerar_nome_cluster_de_arquivo(coredump_id: int) -> str:
    """Gera nome para novo cluster usando path do coredump (fallback com timestamp)."""
    logger.debug("Gerando nome para cluster a partir do coredump_id=%s", coredump_id)
    try:
        info = get_coredump_info_by_id(coredump_id)  # Esperado: tuple onde index 0 = raw_dump_path
    except Exception:  # pragma: no cover - proteção extra
        logger.exception("Falha ao obter info do coredump id=%s", coredump_id)
        info = None

    # Formato esperado (ex): (raw_dump_path, ...)
    if info and info[0]:  # type: ignore[index]
        caminho = Path(info[0])  # type: ignore[index]
        nome_descritivo = f"{CLUSTER_NOME_PREFIXO}_{caminho.stem}"
        return nome_descritivo

    timestamp = int(time.time())
    return f"{CLUSTER_NOME_FALLBACK_PREFIXO}_{timestamp}"


def aplicar_resultados_reconciliacao(
    mapeamento: Dict[int, Dict[str, any]],
    novos: Iterable[str],
    desaparecidos: Iterable[int],
    fusoes: List[Dict[str, any]],  # <-- NOVO PARÂMETRO
    coredumps_no_novo_resultado: Dict[int, str],
    clusters_novos: Dict[str, Set[int]],
) -> None:
    """Aplica mutações no banco: lida com fusões, remove, cria e reatribui clusters."""
    logger.info("Aplicando resultados da reconciliação com tratamento de fusões")

    # Inicializa conjuntos para rastrear IDs envolvidos em fusões
    ids_antigos_em_fusao = set()
    ids_novos_de_fusao = set()
    ids_para_remover_de_fusoes = []

    # Processa fusões: define sobrevivente e marca clusters a remover
    for fusao in fusoes:
        antigos_ids = fusao['antigos']
        novo_temp_id = fusao['novo_cluster']
        
        if not antigos_ids:
            continue

        sobrevivente_id = min(antigos_ids)  # Escolhe menor ID como sobrevivente
        para_remover_ids = [aid for aid in antigos_ids if aid != sobrevivente_id]
        
        mapeamento[sobrevivente_id] = {
            'novo_id': novo_temp_id,
            'tipo': 'fusao'
        }
        logger.info(
            "Fusão detectada: Cluster %s sobrevive e evolui para temp_id %s. Clusters %s serão removidos.",
            sobrevivente_id, novo_temp_id, para_remover_ids
        )
        
        ids_antigos_em_fusao.update(antigos_ids)
        ids_novos_de_fusao.add(novo_temp_id)
        ids_para_remover_de_fusoes.extend(para_remover_ids)

    # Determina clusters a remover (desaparecidos e não sobreviventes de fusão)
    clusters_a_remover = (set(desaparecidos) - ids_antigos_em_fusao).union(set(ids_para_remover_de_fusoes))
    # Determina clusters a criar (novos que não vieram de fusão)
    clusters_a_criar = set(novos) - ids_novos_de_fusao

    # Remove clusters antigos do banco
    for id_antigo in clusters_a_remover:
        try:
            nome_cluster = get_cluster_name(id_antigo)
            logger.info("Cluster removido id=%s nome=%s", id_antigo, nome_cluster)
            unassign_cluster_from_coredumps(id_antigo)
            delete_cluster(id_antigo)
        except Exception:
            logger.exception("Falha ao remover cluster (id=%s)", id_antigo)

    # Cria clusters novos no banco e mapeia temp_id para db_id
    mapa_novo_temp_para_db: Dict[str, int] = {}
    for id_novo_temp in clusters_a_criar:
        coredumps_neste_cluster = clusters_novos.get(id_novo_temp, set())
        if not coredumps_neste_cluster:
            novo_nome = f"{CLUSTER_NOME_VAZIO_PREFIXO}_{id_novo_temp}_{int(time.time())}"
        else:
            id_representante = next(iter(coredumps_neste_cluster))
            novo_nome = gerar_nome_cluster_de_arquivo(id_representante)
        
        novo_db_id = add_cluster(novo_nome)
        mapa_novo_temp_para_db[id_novo_temp] = novo_db_id
        logger.debug("Cluster novo criado temp_id=%s db_id=%s nome=%s", id_novo_temp, novo_db_id, novo_nome)

    # Prepara mapeamento final de temp_id para db_id (inclui fusões/evoluções)
    mapa_final_temp_para_db: Dict[str, int] = dict(mapa_novo_temp_para_db)
    for id_antigo_db, info in mapeamento.items():
        id_novo_temp = info["novo_id"]
        
        # Se houve divisão, pode ser lista de novos IDs (não implementado)
        if isinstance(id_novo_temp, list):
            for sub_id in id_novo_temp:
                if sub_id not in mapa_final_temp_para_db:
                    logger.warning("ID de cluster de divisão '%s' não foi pré-criado. Tratando como novo.", sub_id)
                    # Lógica de criação de cluster aqui, similar ao PASSO 2...
                    pass # Adicionar lógica de criação se necessário
            continue

        mapa_final_temp_para_db[str(id_novo_temp)] = id_antigo_db
        nome_antigo = get_cluster_name(id_antigo_db)
        logger.debug(
            "Mapeamento de identidade: temp_id=%s herda db_id=%s (nome=%s, tipo=%s)",
            id_novo_temp, id_antigo_db, nome_antigo, info.get('tipo', 'evolucao')
        )

    # Reatribui cada coredump ao cluster correto no banco
    for coredump_id, novo_cluster_temp_id in coredumps_no_novo_resultado.items():
        db_cluster_id = mapa_final_temp_para_db.get(str(novo_cluster_temp_id))
        if db_cluster_id is None:
            logger.warning(
                "Coredump %s refere-se a cluster temporário não mapeado: %s",
                coredump_id, novo_cluster_temp_id
            )
            continue
        try:
            assign_cluster_to_coredump(int(coredump_id), db_cluster_id)
        except Exception:
            logger.exception(
                "Falha ao atribuir coredump id=%s ao cluster id=%s",
                coredump_id, db_cluster_id
            )


def carregar_e_traduzir_clusters_novos(
    caminho_csv: str | Path,
) -> Tuple[Dict[str, Set[int]], Dict[int, str]]:
    """Lê CSV (path, temp_cluster_id) e mapeia para IDs de coredump do banco.

    Retorna (clusters_novos, coredumps_para_cluster).
    """
    caminho = Path(caminho_csv)
    logger.info("Carregando novos clusters de %s", caminho)

    todos_os_coredumps = list_all_coredumps()
    if not todos_os_coredumps:
        logger.warning("Nenhum coredump presente no banco para mapear.")
        return {}, {}

    # Formato esperado de cada linha de list_all_coredumps: (id, mac, fw_id, cluster_id, path, ...)
    try:
        path_para_id_map: Dict[str, int] = {
            Path(row[5]).name: int(row[0]) for row in todos_os_coredumps if row and len(row) > 5
        }
    except Exception:  # pragma: no cover
        logger.exception("Falha ao construir mapa path->id a partir dos coredumps.")
        return {}, {}
    logger.debug("Mapa tradução path->id: %s", path_para_id_map)

    clusters_novos: Dict[str, Set[int]] = defaultdict(set)
    coredumps_para_cluster: Dict[int, str] = {}

    if not caminho.exists():
        logger.error("Arquivo de novos clusters não encontrado: %s", caminho)
        return {}, {}

    try:
        with caminho.open("r", newline="", encoding="utf-8") as f:
            leitor_csv = csv.reader(f)
            for linha in leitor_csv:
                if len(linha) < 2:
                    logger.warning("Linha CSV ignorada (esperado 2 colunas): %s", linha)
                    continue
                coredump_name_raw, novo_cluster_temp_id_raw = linha[0], linha[1]
                coredump_name = coredump_name_raw.strip()
                novo_cluster_temp_id = novo_cluster_temp_id_raw.strip()
                coredump_id_numerico = path_para_id_map.get(coredump_name)
                if coredump_id_numerico is None:
                    logger.warning(
                        "Path de coredump do CSV não encontrado no DB e será ignorado: %s",
                        coredump_name,
                    )
                    continue
                clusters_novos[novo_cluster_temp_id].add(coredump_id_numerico)
                coredumps_para_cluster[coredump_id_numerico] = novo_cluster_temp_id
    except Exception:  # pragma: no cover - erro inesperado de IO/parsing
        logger.exception("Erro ao ler/traduzir CSV de clusters: %s", caminho)
        return {}, {}

    return dict(clusters_novos), coredumps_para_cluster


def processar_reconciliacao(
    caminho_csv_novo: str | Path,
    similaridade_threshold: float = LIMIAR_SIMILARIDADE,
) -> Dict[str, int | str]:
    """Executa reconciliação completa e retorna sumário (status + contagens)."""
    logger.info(
        "Iniciando reconciliação de clusters arquivo=%s limiar=%.2f",
        caminho_csv_novo,
        similaridade_threshold,
    )
    create_database()  # Garante estrutura

    clusters_antigos_db = extrair_clusters_do_db()
    if not clusters_antigos_db:
        logger.info("Não há clusters prévios registrados.")
    else:
        for cluster_id, coredumps in clusters_antigos_db.items():
            logger.debug(
                "Cluster existente id=%s nome=%s tamanho=%s",
                cluster_id,
                get_cluster_name(cluster_id),
                len(coredumps),
            )

    clusters_novos, coredumps_no_novo_resultado = carregar_e_traduzir_clusters_novos(caminho_csv_novo)
    if not clusters_novos:
        logger.warning("Nenhum cluster novo válido carregado; encerrando.")
        return {"status": "finalizado", "mensagem": "Nenhum dado novo para processar."}

    mapeamento, novos, desaparecidos, fusoes = reconciliar_clusters_misto(
        clusters_antigos_db,
        clusters_novos,
        similaridade_threshold,
    )
    logger.debug(
        "Resultado reconciliação: mapeados=%s novos=%s removidos=%s fusões=%s",
        len(mapeamento),
        len(novos),
        len(desaparecidos),
        len(fusoes),
    )

    aplicar_resultados_reconciliacao(
        mapeamento,
        novos,
        desaparecidos,
        fusoes,
        coredumps_no_novo_resultado,
        clusters_novos,
    )

    clusters_finais_db = extrair_clusters_do_db()
    for cluster_id, coredumps in clusters_finais_db.items():
        logger.debug(
            "Cluster final id=%s nome=%s tamanho=%s",
            cluster_id,
            get_cluster_name(cluster_id),
            len(coredumps),
        )

    sumario: Dict[str, int | str] = {
        "status": "sucesso",
        "clusters_mapeados": len(mapeamento),
        "clusters_novos_criados": len(novos),
        "clusters_removidos": len(desaparecidos),
    }
    logger.info("Reconciliação concluída: %s", sumario)
    return sumario


def _main() -> None:  # Isola efeitos colaterais de execução direta
    """Ponto de entrada CLI simples para teste manual."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Executando cluster_sincronyzer em modo de teste")
    if TEST_CSV_PATH.exists():
        resultado = processar_reconciliacao(TEST_CSV_PATH)
        logger.info("Resultado: %s", resultado)
    else:
        logger.warning("Arquivo de teste não encontrado: %s", TEST_CSV_PATH)


if __name__ == "__main__":  # Execução direta
    _main()


