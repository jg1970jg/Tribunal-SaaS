# -*- coding: utf-8 -*-
"""
PIPELINE PERGUNTAS ADICIONAIS - VERSÃO ACUMULATIVA
═══════════════════════════════════════════════════════════════════════════

NOVO: Sistema ACUMULATIVO que mantém TODO o histórico!
- ✅ Carrega TODAS perguntas anteriores
- ✅ Carrega TODOS documentos anexados
- ✅ Contexto NUNCA se perde
- ✅ Projeto DINÂMICO que cresce

Este módulo é COMPLETAMENTE INDEPENDENTE do pipeline principal!
"""

import json
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# CLASSES DE DADOS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ResultadoAuditor:
    """Resultado de 1 auditor."""
    auditor_id: str
    modelo: str
    conteudo: str
    tokens_usados: int
    latencia_ms: int


@dataclass
class ResultadoJuiz:
    """Resultado de 1 juiz."""
    juiz_id: str
    modelo: str
    conteudo: str
    tokens_usados: int
    latencia_ms: int


@dataclass
class RespostaPergunta:
    """Resposta completa a uma pergunta."""
    pergunta: str
    timestamp: str
    
    # Fase 2
    auditores: List[ResultadoAuditor]
    auditoria_consolidada: str
    
    # Fase 3
    juizes: List[ResultadoJuiz]
    
    # Fase 4
    resposta_final: str
    
    # Metadados
    tokens_total: int
    tempo_total_ms: int
    custo_estimado: float
    sucesso: bool
    erro: Optional[str] = None
    
    # ← NOVO: Documentos anexados
    documentos_anexados: List[str] = None


# ═══════════════════════════════════════════════════════════════════════════
# FUNÇÕES AUXILIARES
# ═══════════════════════════════════════════════════════════════════════════

def carregar_fase1_existente(run_id: str, output_dir: Path) -> str:
    """
    Carrega resultado Fase 1 (extração) já processado.
    
    NÃO executa pipeline! SÓ lê ficheiro existente.
    """
    # CASO ESPECIAL: Ficheiros soltos
    if run_id == "__FICHEIROS_SOLTOS__":
        nomes_possiveis = [
            "fase1_agregada.md",
            "fase1_agregado.md",
            "fase1_agregado_consolidado.md"
        ]
        
        for nome in nomes_possiveis:
            filepath = output_dir / nome
            if filepath.exists():
                logger.info(f"✓ Fase 1 encontrada (solta): {nome}")
                with open(filepath, 'r', encoding='utf-8') as f:
                    return f.read()
        
        raise FileNotFoundError(
            f"Fase 1 não encontrada em {output_dir}/\n"
            f"Ficheiros procurados: {nomes_possiveis}"
        )
    
    # CASO NORMAL: Análise organizada em pasta
    analise_dir = output_dir / run_id
    
    if not analise_dir.exists():
        raise FileNotFoundError(f"Análise não encontrada: {analise_dir}")
    
    # Procurar ficheiro Fase 1
    nomes_possiveis = [
        "fase1_agregada.md",
        "fase1_agregado.md",
        "fase1_agregado_consolidado.md"
    ]
    
    for nome in nomes_possiveis:
        filepath = analise_dir / nome
        if filepath.exists():
            logger.info(f"✓ Fase 1 encontrada: {nome}")
            with open(filepath, 'r', encoding='utf-8') as f:
                return f.read()
    
    raise FileNotFoundError(
        f"Fase 1 não encontrada em {analise_dir}/\n"
        f"Ficheiros procurados: {nomes_possiveis}"
    )


def carregar_historico_perguntas(run_id: str, output_dir: Path) -> List[Dict]:
    """
    ← NOVA FUNÇÃO!
    
    Carrega TODAS as perguntas anteriores (histórico completo).
    
    Returns:
        Lista de dicts: [
            {
                'numero': 1,
                'pergunta': '...',
                'resposta_final': '...',
                'timestamp': '...'
            },
            ...
        ]
    """
    historico = []
    
    # Determinar pasta perguntas
    if run_id == "__FICHEIROS_SOLTOS__":
        perguntas_dir = output_dir / "perguntas"
    else:
        perguntas_dir = output_dir / run_id / "perguntas"
    
    if not perguntas_dir.exists():
        logger.info("✓ Nenhuma pergunta anterior (primeira pergunta)")
        return []
    
    # Carregar todos os JSONs
    json_files = sorted(perguntas_dir.glob("pergunta_*.json"))
    
    for json_file in json_files:
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            
            # Carregar resposta_final (pode estar no JSON ou no .md)
            resposta_final = metadata.get('resposta_final', '')
            
            # Se não tiver no JSON, tentar carregar do .md
            if not resposta_final:
                md_file = json_file.parent / f"pergunta_{metadata['numero']}_decisao.md"
                if md_file.exists():
                    with open(md_file, 'r', encoding='utf-8') as f:
                        resposta_final = f.read()
            
            historico.append({
                'numero': metadata['numero'],
                'pergunta': metadata['pergunta'],
                'resposta_final': resposta_final,
                'timestamp': metadata['timestamp'],
                'documentos': metadata.get('documentos_anexados', [])
            })
        
        except Exception as e:
            logger.warning(f"⚠️ Erro ao carregar {json_file.name}: {e}")
            continue
    
    logger.info(f"✓ Histórico carregado: {len(historico)} perguntas anteriores")
    return historico


def carregar_documentos_anexados(run_id: str, output_dir: Path) -> Dict[str, str]:
    """
    ← NOVA FUNÇÃO!
    
    Carrega TODOS os documentos anexados ao projeto.
    
    Returns:
        Dict: {
            'minuta_carta.docx': 'texto extraído...',
            'comprovativo.pdf': 'texto extraído...'
        }
    """
    documentos = {}
    
    # Determinar pasta documentos
    if run_id == "__FICHEIROS_SOLTOS__":
        docs_dir = output_dir / "perguntas" / "documentos_anexados"
    else:
        docs_dir = output_dir / run_id / "perguntas" / "documentos_anexados"
    
    if not docs_dir.exists():
        logger.info("✓ Nenhum documento anexado")
        return {}
    
    # Carregar todos os .txt (extraídos)
    txt_files = list(docs_dir.glob("*_extraido.txt"))
    
    for txt_file in txt_files:
        try:
            # Nome original (remover _extraido.txt)
            nome_original = txt_file.stem.replace('_extraido', '') + txt_file.suffix.replace('.txt', '')
            
            with open(txt_file, 'r', encoding='utf-8') as f:
                texto = f.read()
            
            documentos[nome_original] = texto
            logger.info(f"✓ Documento carregado: {nome_original} ({len(texto)} chars)")
        
        except Exception as e:
            logger.warning(f"⚠️ Erro ao carregar {txt_file.name}: {e}")
            continue
    
    logger.info(f"✓ Total documentos anexados: {len(documentos)}")
    return documentos


def estimar_custo(tokens: int, modelo_mix: str = "mixed") -> float:
    """Estima custo baseado em tokens."""
    # Custo médio por 1K tokens
    if modelo_mix == "premium":
        return tokens * 0.00001  # $0.01 por 1K
    elif modelo_mix == "economico":
        return tokens * 0.000001  # $0.001 por 1K
    else:
        return tokens * 0.00001  # Mixed


# ═══════════════════════════════════════════════════════════════════════════
# FASE 2: AUDITORES (MODIFICADO PARA CONTEXTO ACUMULATIVO)
# ═══════════════════════════════════════════════════════════════════════════

def executar_fase2_auditores(
    fase1_extracao: str,
    pergunta: str,
    auditor_models: List[Dict],
    llm_client,
    historico_perguntas: List[Dict] = None,  # ← NOVO!
    documentos_anexados: Dict[str, str] = None  # ← NOVO!
) -> Tuple[List[ResultadoAuditor], str]:
    """
    Executa Fase 2: 3 Auditores + Chefe consolidador.
    
    ← MODIFICADO: Agora inclui histórico e documentos no prompt!
    
    Args:
        fase1_extracao: Análise original (Fase 1)
        pergunta: Pergunta atual
        auditor_models: Lista configs auditores
        llm_client: Cliente LLM
        historico_perguntas: Perguntas anteriores ← NOVO!
        documentos_anexados: Documentos adicionados ← NOVO!
    
    Returns:
        (List[ResultadoAuditor], str): (3 auditorias, consolidada)
    """
    logger.info("=== FASE 2: Iniciando auditoria (perguntas) ===")
    
    # ← NOVO: Construir seção histórico
    secao_historico = ""
    if historico_perguntas and len(historico_perguntas) > 0:
        secao_historico = "\n═══════════════════════════════════════════════════════════════\n"
        secao_historico += "HISTÓRICO DE PERGUNTAS ANTERIORES (CONTEXTO ACUMULADO):\n"
        secao_historico += "═══════════════════════════════════════════════════════════════\n\n"
        
        for item in historico_perguntas:
            secao_historico += f"### PERGUNTA #{item['numero']} ({item['timestamp']})\n\n"
            secao_historico += f"**Pergunta:** {item['pergunta']}\n\n"
            secao_historico += f"**Resposta/Decisão:**\n{item['resposta_final']}\n\n"
            secao_historico += "───────────────────────────────────────────────────────────────\n\n"
    
    # ← NOVO: Construir seção documentos
    secao_documentos = ""
    if documentos_anexados and len(documentos_anexados) > 0:
        secao_documentos = "\n═══════════════════════════════════════════════════════════════\n"
        secao_documentos += "DOCUMENTOS ADICIONADOS AO PROJETO:\n"
        secao_documentos += "═══════════════════════════════════════════════════════════════\n\n"
        
        for nome_doc, texto_doc in documentos_anexados.items():
            secao_documentos += f"### 📄 {nome_doc}\n\n"
            secao_documentos += f"{texto_doc}\n\n"
            secao_documentos += "───────────────────────────────────────────────────────────────\n\n"
    
    auditores_resultados = []
    
    # Executar 3 auditores
    for i, auditor_config in enumerate(auditor_models, 1):
        if isinstance(auditor_config, str):
            modelo = auditor_config
        elif isinstance(auditor_config, dict):
            modelo = auditor_config.get('model', auditor_config.get('nome', 'unknown'))
        else:
            modelo = str(auditor_config)
        
        logger.info(f"Auditor {i}/{len(auditor_models)}: {modelo}")
        
        # ← MODIFICADO: Prompt agora inclui TUDO!
        prompt = f"""Você é um AUDITOR JURÍDICO experiente.

═══════════════════════════════════════════════════════════════
ANÁLISE ORIGINAL (Fase 1 - Extração Inicial):
═══════════════════════════════════════════════════════════════

{fase1_extracao}
{secao_historico}{secao_documentos}
═══════════════════════════════════════════════════════════════
PERGUNTA ATUAL DO UTILIZADOR:
═══════════════════════════════════════════════════════════════

{pergunta}

═══════════════════════════════════════════════════════════════
SUA MISSÃO COMO AUDITOR:
═══════════════════════════════════════════════════════════════

Analise TODO o contexto acima (análise original + histórico de perguntas anteriores + documentos anexados) e identifique:

1. **ELEMENTOS RELEVANTES** para responder esta pergunta
   - Da análise original
   - Das respostas anteriores
   - Dos documentos anexados
   - Factos, diplomas legais, jurisprudência, datas, prazos

2. **LACUNAS** - Informação útil mas não presente
   - Elementos em falta
   - Dados não extraídos
   - Contexto adicional necessário

3. **INCONSISTÊNCIAS** - Contradições ou problemas
   - Entre análise e documentos
   - Entre perguntas anteriores
   - Factos que não batem certo

4. **ELEMENTOS ADICIONAIS** - Sugestões
   - Legislação adicional aplicável
   - Jurisprudência relevante
   - Aspectos a aprofundar

FORMATO DA AUDITORIA:

## Elementos Relevantes Identificados
[lista elementos úteis de TODA informação disponível]

## Lacunas Detectadas
[lista informação em falta]

## Inconsistências (se houver)
[lista problemas detectados]

## Elementos Adicionais a Considerar
[sugestões]

IMPORTANTE: Considere TODO o contexto acumulado (análise + histórico + documentos)!
"""
        
        inicio = time.time()
        
        try:
            resposta = llm_client.chat_simple(
                model=modelo,
                prompt=prompt,
                temperature=0.3,
                max_tokens=4000
            )
            
            latencia = int((time.time() - inicio) * 1000)
            
            auditores_resultados.append(ResultadoAuditor(
                auditor_id=f"A{i}",
                modelo=modelo,
                conteudo=resposta.content,
                tokens_usados=resposta.total_tokens,
                latencia_ms=latencia
            ))
            
            logger.info(f"✓ Auditor {i} concluído ({latencia}ms)")
        
        except Exception as e:
            logger.error(f"✗ Erro Auditor {i}: {e}")
            auditores_resultados.append(ResultadoAuditor(
                auditor_id=f"A{i}",
                modelo=modelo,
                conteudo=f"[ERRO: {e}]",
                tokens_usados=0,
                latencia_ms=0
            ))
    
    # Chefe consolida
    logger.info("Chefe consolidando auditorias...")
    
    from src.config import CHEFE_MODEL
    
    prompt_chefe = f"""Você é o CHEFE DOS AUDITORES.

Recebeu 3 auditorias sobre a seguinte pergunta:

**PERGUNTA:** {pergunta}

═══════════════════════════════════════════════════════════════
AUDITORIA 1 ({auditores_resultados[0].modelo}):
═══════════════════════════════════════════════════════════════

{auditores_resultados[0].conteudo}

═══════════════════════════════════════════════════════════════
AUDITORIA 2 ({auditores_resultados[1].modelo}):
═══════════════════════════════════════════════════════════════

{auditores_resultados[1].conteudo}

═══════════════════════════════════════════════════════════════
AUDITORIA 3 ({auditores_resultados[2].modelo}):
═══════════════════════════════════════════════════════════════

{auditores_resultados[2].conteudo}

═══════════════════════════════════════════════════════════════
SUA MISSÃO COMO CHEFE:
═══════════════════════════════════════════════════════════════

Consolide as 3 auditorias numa SÍNTESE ÚNICA:

1. **ELEMENTOS RELEVANTES CONSOLIDADOS** - Todos elementos importantes identificados
2. **LACUNAS CONSOLIDADAS** - Todas lacunas detectadas
3. **INCONSISTÊNCIAS CONSOLIDADAS** - Todos problemas encontrados
4. **ELEMENTOS ADICIONAIS CONSOLIDADOS** - Todas sugestões

FORMATO:

## Elementos Relevantes (Consolidado)
[síntese de tudo identificado pelos 3 auditores]

## Lacunas (Consolidado)
[síntese de todas lacunas]

## Inconsistências (Consolidado)
[síntese de problemas]

## Elementos Adicionais (Consolidado)
[síntese de sugestões]

SÍNTESE CONSOLIDADA:
"""
    
    inicio = time.time()
    
    try:
        resposta_chefe = llm_client.chat_simple(
            model=CHEFE_MODEL,
            prompt=prompt_chefe,
            temperature=0.2,
            max_tokens=3000
        )
        
        latencia_chefe = int((time.time() - inicio) * 1000)
        auditoria_consolidada = resposta_chefe.content
        
        logger.info(f"✓ Chefe concluído ({latencia_chefe}ms)")
    
    except Exception as e:
        logger.error(f"✗ Erro Chefe: {e}")
        auditoria_consolidada = "[ERRO NA CONSOLIDAÇÃO]"
    
    return auditores_resultados, auditoria_consolidada


# ═══════════════════════════════════════════════════════════════════════════
# FASE 3: JUÍZES (mantém igual)
# ═══════════════════════════════════════════════════════════════════════════

def executar_fase3_juizes(
    fase1_extracao: str,
    auditoria_consolidada: str,
    pergunta: str,
    juiz_models: List[Dict],
    llm_client,
    historico_perguntas: List[Dict] = None,
    documentos_anexados: Dict[str, str] = None
) -> List[ResultadoJuiz]:
    """
    Executa Fase 3: 3 Juízes analisam.

    Inclui contexto COMPLETO: análise original + histórico Q&A + documentos anexados.
    """
    logger.info("=== FASE 3: Iniciando relatoria (perguntas) ===")

    # Construir seção histórico (mesmo padrão dos auditores)
    secao_historico = ""
    if historico_perguntas and len(historico_perguntas) > 0:
        secao_historico = "\n═══════════════════════════════════════════════════════════════\n"
        secao_historico += "HISTÓRICO DE PERGUNTAS ANTERIORES (CONTEXTO ACUMULADO):\n"
        secao_historico += "═══════════════════════════════════════════════════════════════\n\n"

        for item in historico_perguntas:
            secao_historico += f"### PERGUNTA #{item['numero']} ({item['timestamp']})\n\n"
            secao_historico += f"**Pergunta:** {item['pergunta']}\n\n"
            secao_historico += f"**Resposta/Decisão:**\n{item['resposta_final']}\n\n"
            secao_historico += "───────────────────────────────────────────────────────────────\n\n"

    # Construir seção documentos (mesmo padrão dos auditores)
    secao_documentos = ""
    if documentos_anexados and len(documentos_anexados) > 0:
        secao_documentos = "\n═══════════════════════════════════════════════════════════════\n"
        secao_documentos += "DOCUMENTOS ADICIONADOS AO PROJETO:\n"
        secao_documentos += "═══════════════════════════════════════════════════════════════\n\n"

        for nome_doc, texto_doc in documentos_anexados.items():
            secao_documentos += f"### 📄 {nome_doc}\n\n"
            secao_documentos += f"{texto_doc}\n\n"
            secao_documentos += "───────────────────────────────────────────────────────────────\n\n"

    juizes_resultados = []

    for i, juiz_config in enumerate(juiz_models, 1):
        if isinstance(juiz_config, str):
            modelo = juiz_config
        elif isinstance(juiz_config, dict):
            modelo = juiz_config.get('model', juiz_config.get('nome', 'unknown'))
        else:
            modelo = str(juiz_config)

        logger.info(f"Juiz {i}/{len(juiz_models)}: {modelo}")

        prompt = f"""Você é um RELATOR ESPECIALISTA.

═══════════════════════════════════════════════════════════════
EXTRAÇÃO (Fase 1 - Análise Original):
═══════════════════════════════════════════════════════════════

{fase1_extracao}
{secao_historico}{secao_documentos}
═══════════════════════════════════════════════════════════════
AUDITORIA CONSOLIDADA (Fase 2):
═══════════════════════════════════════════════════════════════

{auditoria_consolidada}

═══════════════════════════════════════════════════════════════
PERGUNTA ATUAL:
═══════════════════════════════════════════════════════════════

{pergunta}

═══════════════════════════════════════════════════════════════
SUA MISSÃO COMO RELATOR:
═══════════════════════════════════════════════════════════════

Analise TODO o contexto acima (análise original + histórico de perguntas anteriores + documentos anexados + auditoria) e produza PARECER JURÍDICO fundamentado:

## Enquadramento Legal
[diplomas aplicáveis, artigos específicos - cite dos documentos quando relevante]

## Análise de Factos
[factos relevantes da extração E dos documentos anexados]

## Fundamentação Jurídica
[argumentação legal, considerando respostas anteriores se relevantes]

## Conclusão
[resposta clara à pergunta]

IMPORTANTE: Considere TODO o contexto acumulado (análise original + histórico de perguntas + documentos anexados)! NÃO peça informação que já consta dos documentos ou das respostas anteriores.

PARECER:
"""
        
        inicio = time.time()
        
        try:
            resposta = llm_client.chat_simple(
                model=modelo,
                prompt=prompt,
                temperature=0.2,
                max_tokens=4000
            )
            
            latencia = int((time.time() - inicio) * 1000)
            
            juizes_resultados.append(ResultadoJuiz(
                juiz_id=f"J{i}",
                modelo=modelo,
                conteudo=resposta.content,
                tokens_usados=resposta.total_tokens,
                latencia_ms=latencia
            ))
            
            logger.info(f"✓ Juiz {i} concluído ({latencia}ms)")
        
        except Exception as e:
            logger.error(f"✗ Erro Juiz {i}: {e}")
            juizes_resultados.append(ResultadoJuiz(
                juiz_id=f"J{i}",
                modelo=modelo,
                conteudo=f"[ERRO: {e}]",
                tokens_usados=0,
                latencia_ms=0
            ))
    
    return juizes_resultados


# ═══════════════════════════════════════════════════════════════════════════
# FASE 4: CONSELHEIRO-MOR
# ═══════════════════════════════════════════════════════════════════════════

def executar_fase4_presidente(
    pergunta: str,
    juizes_resultados: List[ResultadoJuiz],
    presidente_model: str,
    llm_client,
    historico_perguntas: List[Dict] = None,
    documentos_anexados: Dict[str, str] = None,
    fase1_extracao: str = ""
) -> Tuple[str, int, int]:
    """
    Executa Fase 4: Conselheiro-Mor sintetiza.

    Inclui contexto COMPLETO para decisão informada.
    """
    logger.info("=== FASE 4: Conselheiro-Mor decidindo (perguntas) ===")

    # Construir seção de contexto acumulado para o Conselheiro-Mor
    secao_contexto = ""
    if fase1_extracao:
        secao_contexto += "\n═══════════════════════════════════════════════════════════════\n"
        secao_contexto += "ANÁLISE ORIGINAL (Fase 1):\n"
        secao_contexto += "═══════════════════════════════════════════════════════════════\n\n"
        secao_contexto += f"{fase1_extracao}\n\n"

    if historico_perguntas and len(historico_perguntas) > 0:
        secao_contexto += "═══════════════════════════════════════════════════════════════\n"
        secao_contexto += "HISTÓRICO DE PERGUNTAS ANTERIORES:\n"
        secao_contexto += "═══════════════════════════════════════════════════════════════\n\n"
        for item in historico_perguntas:
            secao_contexto += f"### PERGUNTA #{item['numero']} ({item['timestamp']})\n\n"
            secao_contexto += f"**Pergunta:** {item['pergunta']}\n\n"
            secao_contexto += f"**Resposta/Decisão:**\n{item['resposta_final']}\n\n"
            secao_contexto += "───────────────────────────────────────────────────────────────\n\n"

    if documentos_anexados and len(documentos_anexados) > 0:
        secao_contexto += "═══════════════════════════════════════════════════════════════\n"
        secao_contexto += "DOCUMENTOS ADICIONADOS AO PROJETO:\n"
        secao_contexto += "═══════════════════════════════════════════════════════════════\n\n"
        for nome_doc, texto_doc in documentos_anexados.items():
            secao_contexto += f"### 📄 {nome_doc}\n\n"
            secao_contexto += f"{texto_doc}\n\n"
            secao_contexto += "───────────────────────────────────────────────────────────────\n\n"

    # Construir pareceres dos juízes
    pareceres_juizes = ""
    for j, resultado in enumerate(juizes_resultados, 1):
        pareceres_juizes += f"### RELATOR {j} ({resultado.modelo}):\n\n"
        pareceres_juizes += f"{resultado.conteudo}\n\n"
        pareceres_juizes += "───────────────────────────────────────────────────────────────\n\n"

    prompt = f"""Você é o CONSELHEIRO-MOR do LexForum.
{secao_contexto}
═══════════════════════════════════════════════════════════════
PERGUNTA DO UTILIZADOR:
═══════════════════════════════════════════════════════════════

{pergunta}

═══════════════════════════════════════════════════════════════
PARECERES DOS RELATORES:
═══════════════════════════════════════════════════════════════

{pareceres_juizes}

═══════════════════════════════════════════════════════════════
SUA MISSÃO COMO CONSELHEIRO-MOR:
═══════════════════════════════════════════════════════════════

Considerando TODO o contexto (análise original + histórico + documentos + pareceres dos relatores), sintetize numa RESPOSTA FINAL:

## Consensos entre Relatores
[pontos acordados]

## Divergências (se houver)
[diferentes perspectivas]

## Verificação de Citações Legais
[validar diplomas/artigos - confirme nos documentos originais]

## Resposta Final
[síntese clara respondendo à pergunta, referenciando documentos e histórico quando relevante]

IMPORTANTE: NÃO peça informação que já consta dos documentos ou das respostas anteriores!

DECISÃO FINAL:
"""
    
    inicio = time.time()
    
    try:
        resposta = llm_client.chat_simple(
            model=presidente_model,
            prompt=prompt,
            temperature=0.1,
            max_tokens=5000
        )
        
        latencia = int((time.time() - inicio) * 1000)
        
        logger.info(f"✓ Presidente concluído ({latencia}ms)")
        
        return resposta.content, resposta.total_tokens, latencia
    
    except Exception as e:
        logger.error(f"✗ Erro Presidente: {e}")
        return f"[ERRO: {e}]", 0, 0


# ═══════════════════════════════════════════════════════════════════════════
# PIPELINE COMPLETO (MODIFICADO)
# ═══════════════════════════════════════════════════════════════════════════

def processar_pergunta_adicional(
    run_id: str,
    output_dir: Path,
    pergunta: str,
    auditor_models: List[Dict],
    juiz_models: List[Dict],
    presidente_model: str,
    llm_client,
    documentos_novos: List[Tuple[str, str]] = None  # ← NOVO! [(nome, texto), ...]
) -> RespostaPergunta:
    """
    Processa pergunta adicional sobre análise existente.
    
    ← MODIFICADO: Agora carrega histórico e documentos!
    
    Args:
        run_id: ID da análise original
        output_dir: Pasta outputs
        pergunta: Pergunta do utilizador
        auditor_models: Lista configs auditores
        juiz_models: Lista configs juízes
        presidente_model: Modelo presidente
        llm_client: Cliente LLM
        documentos_novos: Novos documentos anexados ← NOVO!
    
    Returns:
        RespostaPergunta completa
    """
    tempo_inicio = time.time()
    
    try:
        logger.info("\n" + "="*70)
        logger.info("PROCESSANDO PERGUNTA ADICIONAL - Pipeline Completo")
        logger.info(f"Run ID: {run_id}")
        logger.info(f"Pergunta: {pergunta[:100]}...")
        logger.info("="*70)
        
        # ═══════════════════════════════════════════════════════════
        # 1. Carregar Fase 1 (análise original - SEMPRE presente)
        # ═══════════════════════════════════════════════════════════
        
        fase1_extracao = carregar_fase1_existente(run_id, output_dir)
        logger.info(f"✓ Fase 1 carregada ({len(fase1_extracao):,} chars)")
        
        # ═══════════════════════════════════════════════════════════
        # 2. ← NOVO: Carregar histórico perguntas anteriores
        # ═══════════════════════════════════════════════════════════
        
        historico_perguntas = carregar_historico_perguntas(run_id, output_dir)
        
        # ═══════════════════════════════════════════════════════════
        # 3. ← NOVO: Carregar documentos anexados (existentes)
        # ═══════════════════════════════════════════════════════════
        
        documentos_anexados = carregar_documentos_anexados(run_id, output_dir)
        
        # ═══════════════════════════════════════════════════════════
        # 4. ← NOVO: Adicionar documentos novos (se houver)
        # ═══════════════════════════════════════════════════════════
        
        nomes_docs_novos = []
        
        if documentos_novos:
            for nome_doc, texto_doc in documentos_novos:
                documentos_anexados[nome_doc] = texto_doc
                nomes_docs_novos.append(nome_doc)
                logger.info(f"✓ Documento novo anexado: {nome_doc}")
        
        # ═══════════════════════════════════════════════════════════
        # 5. FASE 2: Auditores (COM CONTEXTO ACUMULATIVO!)
        # ═══════════════════════════════════════════════════════════
        
        auditores_resultados, auditoria_consolidada = executar_fase2_auditores(
            fase1_extracao=fase1_extracao,
            pergunta=pergunta,
            auditor_models=auditor_models,
            llm_client=llm_client,
            historico_perguntas=historico_perguntas,  # ← NOVO!
            documentos_anexados=documentos_anexados  # ← NOVO!
        )
        
        logger.info(f"✓ Fase 2 concluída ({len(auditoria_consolidada):,} chars)")
        
        # ═══════════════════════════════════════════════════════════
        # 6. FASE 3: Juízes
        # ═══════════════════════════════════════════════════════════
        
        juizes_resultados = executar_fase3_juizes(
            fase1_extracao=fase1_extracao,
            auditoria_consolidada=auditoria_consolidada,
            pergunta=pergunta,
            juiz_models=juiz_models,
            llm_client=llm_client,
            historico_perguntas=historico_perguntas,
            documentos_anexados=documentos_anexados
        )
        
        logger.info(f"✓ Fase 3 concluída (3 pareceres)")
        
        # ═══════════════════════════════════════════════════════════
        # 7. FASE 4: Presidente
        # ═══════════════════════════════════════════════════════════
        
        resposta_final, tokens_presidente, latencia_presidente = executar_fase4_presidente(
            pergunta=pergunta,
            juizes_resultados=juizes_resultados,
            presidente_model=presidente_model,
            llm_client=llm_client,
            historico_perguntas=historico_perguntas,
            documentos_anexados=documentos_anexados,
            fase1_extracao=fase1_extracao
        )
        
        logger.info(f"✓ Fase 4 concluída ({len(resposta_final):,} chars)")
        
        # ═══════════════════════════════════════════════════════════
        # 8. Calcular totais
        # ═══════════════════════════════════════════════════════════
        
        tokens_total = (
            sum(a.tokens_usados for a in auditores_resultados) +
            sum(j.tokens_usados for j in juizes_resultados) +
            tokens_presidente
        )
        
        tempo_total_ms = int((time.time() - tempo_inicio) * 1000)
        custo_estimado = estimar_custo(tokens_total, "mixed")
        
        logger.info(f"\n{'='*70}")
        logger.info(f"✓ PERGUNTA PROCESSADA COM SUCESSO!")
        logger.info(f"  Tempo total: {tempo_total_ms/1000:.1f}s")
        logger.info(f"  Tokens total: {tokens_total:,}")
        logger.info(f"  Custo estimado: ${custo_estimado:.4f}")
        logger.info(f"{'='*70}\n")
        
        # ═══════════════════════════════════════════════════════════
        # 9. Retornar resultado completo
        # ═══════════════════════════════════════════════════════════
        
        return RespostaPergunta(
            pergunta=pergunta,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            auditores=auditores_resultados,
            auditoria_consolidada=auditoria_consolidada,
            juizes=juizes_resultados,
            resposta_final=resposta_final,
            tokens_total=tokens_total,
            tempo_total_ms=tempo_total_ms,
            custo_estimado=custo_estimado,
            sucesso=True,
            erro=None,
            documentos_anexados=nomes_docs_novos  # ← NOVO!
        )
        
    except Exception as e:
        logger.error(f"\n{'='*70}")
        logger.error(f"✗ ERRO AO PROCESSAR PERGUNTA!")
        logger.error(f"  Erro: {e}")
        logger.error(f"{'='*70}\n", exc_info=True)
        
        return RespostaPergunta(
            pergunta=pergunta,
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            auditores=[],
            auditoria_consolidada="",
            juizes=[],
            resposta_final="",
            tokens_total=0,
            tempo_total_ms=0,
            custo_estimado=0.0,
            sucesso=False,
            erro=str(e),
            documentos_anexados=[]
        )
