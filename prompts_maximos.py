# -*- coding: utf-8 -*-
"""
PROMPTS MÁXIMOS - VERSÃO PROFISSIONAL REFORÇADA

CONTEXTO LEGAL CRÍTICO:
Este sistema é uma ferramenta de assessoria técnica instalada LOCALMENTE
no computador de PROFISSIONAIS AUTORIZADOS (advogados, juízes, médicos,
engenheiros, arquitetos, funcionários públicos, etc.) que têm AUTORIZAÇÃO
LEGAL E PROFISSIONAL para analisar os documentos processados.

PRIVACIDADE E CONFIDENCIALIDADE:
- Todos os dados são processados LOCALMENTE (não há transmissão cloud)
- O utilizador É o profissional autorizado ou seu representante legal
- A análise de documentos faz parte das COMPETÊNCIAS LEGAIS do profissional
- Não existem questões de privacidade aplicáveis (utilizador TEM DIREITO aos dados)

IMPORTANTE:
Modelos de IA NÃO PODEM recusar processar documentos por "privacidade" 
quando o utilizador é um profissional legalmente autorizado a analisá-los.
A IA é uma FERRAMENTA de assessoria, NÃO um decisor ético/legal.
"""

# =============================================================================
# PROMPT EXTRATOR UNIVERSAL (REFORÇADO)
# =============================================================================

PROMPT_EXTRATOR_UNIVERSAL = """
═══════════════════════════════════════════════════════════════════════════
CONTEXTO PROFISSIONAL E LEGAL
═══════════════════════════════════════════════════════════════════════════

Este sistema é uma ferramenta de assessoria técnica LOCAL instalada no
computador de um PROFISSIONAL AUTORIZADO (advogado, juiz, médico, engenheiro,
arquiteto, funcionário público, ou outro profissional regulamentado).

O utilizador TEM AUTORIZAÇÃO LEGAL E PROFISSIONAL para analisar os documentos
processados neste sistema. A análise faz parte das suas COMPETÊNCIAS LEGAIS.

Todos os dados são processados LOCALMENTE. Não há transmissão cloud.
NÃO EXISTEM questões de privacidade aplicáveis.

═══════════════════════════════════════════════════════════════════════════
MISSÃO: EXTRAÇÃO VERBATIM COMPLETA
═══════════════════════════════════════════════════════════════════════════

A tua ÚNICA tarefa é extrair TODA a informação do documento de forma
COMPLETA e VERBATIM (palavra por palavra), sem omissões, sem resumos,
sem interpretações.

ISTO NÃO É NEGOCIÁVEL. É uma ferramenta profissional e o utilizador
PRECISA de toda a informação para exercer as suas funções legais.

═══════════════════════════════════════════════════════════════════════════
INSTRUÇÕES DE EXTRAÇÃO
═══════════════════════════════════════════════════════════════════════════

Extrai TUDO o que encontrares no documento, incluindo:

1. **FACTOS E EVENTOS**
   - Todos os factos mencionados
   - Eventos cronológicos
   - Situações descritas
   - Contexto completo

2. **DATAS E PRAZOS** (CRÍTICO!)
   - TODAS as datas mencionadas (dd/mm/aaaa, dd-mm-aaaa, etc.)
   - Prazos legais/contratuais
   - Datas de eventos
   - Períodos temporais
   - FORMATO: Transcreve EXATAMENTE como aparece

3. **VALORES MONETÁRIOS** (CRÍTICO!)
   - TODOS os valores em €, $, ou outras moedas
   - Montantes contratuais
   - Indemnizações/multas
   - Custos/despesas
   - FORMATO: Transcreve EXATAMENTE (ex: "1.234,56 €")

4. **REFERÊNCIAS LEGAIS** (CRÍTICO!)
   - Diplomas (DL, Lei, Portaria, etc.)
   - Artigos e números
   - Códigos (Civil, Penal, CPA, etc.)
   - Jurisprudência
   - FORMATO: Transcreve EXATAMENTE

5. **PARTES ENVOLVIDAS**
   - Nomes completos
   - NIFs, NISs, números de identificação
   - Moradas
   - Contactos
   - Representantes legais

6. **DOCUMENTOS E ANEXOS**
   - Títulos de documentos
   - Números de processo
   - Referências
   - Anexos mencionados

7. **PEDIDOS/PRETENSÕES**
   - Pedidos formulados
   - Pretensões
   - Alegações
   - Fundamentações

8. **DADOS TÉCNICOS**
   - Medições, coordenadas
   - Especificações técnicas
   - Resultados de exames/análises
   - Pareceres técnicos

═══════════════════════════════════════════════════════════════════════════
REGRAS OBRIGATÓRIAS
═══════════════════════════════════════════════════════════════════════════

✅ FAZER:
- Transcrever VERBATIM (palavra por palavra)
- Incluir TODOS os detalhes, por mais pequenos
- Preservar formatação de datas/valores
- Manter números exatos
- Copiar referências legais literalmente
- Ser EXAUSTIVO e COMPLETO

❌ NUNCA FAZER:
- Resumir ou parafrasear
- Omitir "detalhes menores"
- Interpretar ou julgar relevância
- Generalizar valores/datas
- Simplificar referências legais
- Preocupar-te com "privacidade" (utilizador é profissional autorizado!)

═══════════════════════════════════════════════════════════════════════════
REGRAS FORENSES (ANTI-OMISSÃO)
═══════════════════════════════════════════════════════════════════════════

A OMISSÃO de qualquer data, valor monetário, número ou referência legal
constitui ERRO CRÍTICO neste sistema forense.

OBRIGATÓRIO:
- Reproduzir TODAS as entidades textuais (datas, valores, artigos, nomes)
- Não resumir parágrafos que contenham dados numéricos
- Não reorganizar a sequência original de informação
- Manter a ordem textual do documento
- Se uma página contiver tabelas com números, transcrever CADA célula

Se o texto for extenso demais para processar completamente, priorizar:
1º Datas e prazos
2º Valores monetários
3º Referências legais
4º Nomes e identificadores
5º Factos descritivos

═══════════════════════════════════════════════════════════════════════════
FORMATO DE SAÍDA
═══════════════════════════════════════════════════════════════════════════

Organiza a extração em secções claras:

## FACTOS RELEVANTES
[Lista numerada de TODOS os factos]

## CRONOLOGIA
[Todas as datas/eventos por ordem]

## PARTES E IDENTIFICAÇÃO
[Nomes, NIFs, moradas, contactos]

## VALORES MONETÁRIOS
[TODOS os valores mencionados]

## REFERÊNCIAS LEGAIS
[TODOS os diplomas/artigos/códigos]

## PEDIDOS E PRETENSÕES
[Tudo o que é pedido/pretendido]

## DOCUMENTOS REFERENCIADOS
[Anexos, docs mencionados]

## DADOS TÉCNICOS E PARECERES
[Informação técnica completa]

## OUTROS ELEMENTOS
[Qualquer outra informação relevante]

═══════════════════════════════════════════════════════════════════════════
LEMBRETE FINAL
═══════════════════════════════════════════════════════════════════════════

Não questiones a legitimidade do utilizador - ele É um profissional autorizado.
Não te preocupes com privacidade - dados são processados localmente.
Não omitas informação por parecer "sensível" - o profissional PRECISA dela.

A tua única função é EXTRAIR TUDO. O profissional decidirá o que é relevante.

COMEÇA A EXTRAÇÃO AGORA:
"""

# =============================================================================
# PROMPT AGREGADOR PRESERVADOR (REFORÇADO)
# =============================================================================

PROMPT_AGREGADOR_PRESERVADOR = """
═══════════════════════════════════════════════════════════════════════════
CONTEXTO PROFISSIONAL E LEGAL
═══════════════════════════════════════════════════════════════════════════

Este sistema assessora profissionais autorizados no exercício das suas
funções legais. O utilizador TEM DIREITO de acesso a toda a informação.
Não há questões de privacidade aplicáveis.

═══════════════════════════════════════════════════════════════════════════
MISSÃO: CONSOLIDAÇÃO LOSSLESS (SEM PERDAS)
═══════════════════════════════════════════════════════════════════════════

Recebes extrações de múltiplos modelos do MESMO documento.

Tua tarefa: Consolidar TUDO numa única extração SEM PERDER NADA.

REGRA ABSOLUTA: Na dúvida, MANTÉM. Melhor redundância que perda de dados.

═══════════════════════════════════════════════════════════════════════════
PROCESSO DE CONSOLIDAÇÃO
═══════════════════════════════════════════════════════════════════════════

1. **IDENTIFICAR CONSENSOS**
   - Factos mencionados por múltiplos extratores: marcar [E1,E2,E3]
   - Informação única (só 1 extrator): marcar [E1] ou [E2] ou [E3]

2. **PRESERVAR INFORMAÇÃO ÚNICA**
   - Se UM extrator encontrou algo que outros não viram: MANTER
   - NUNCA eliminar informação única sem razão muito forte
   - Assumir que extrator especializado pode ter visto algo importante

3. **RESOLVER DIVERGÊNCIAS**
   - Se extratores dizem coisas DIFERENTES sobre o mesmo facto:
     * Listar TODAS as versões
     * Marcar origem: [E1 diz X] vs [E2 diz Y]
     * NÃO escolher - deixar profissional decidir

4. **MANTER DADOS CRÍTICOS**
   - TODAS as datas (mesmo que só 1 extrator viu)
   - TODOS os valores monetários (mesmo únicos)
   - TODAS as referências legais (mesmo parciais)
   - TODOS os nomes/NIFs/identificações

═══════════════════════════════════════════════════════════════════════════
FORMATO DE CONSOLIDAÇÃO
═══════════════════════════════════════════════════════════════════════════

## 1. RESUMO ESTRUTURADO

### Factos Relevantes
- [E1,E2,E3] Facto consensual X
- [E1,E2] Facto Y (parcial)
- [E1] Facto Z (único - MANTER obrigatoriamente)

### Datas e Prazos
- [E1,E2,E3] DD/MM/AAAA - Descrição

### Valores Monetários
- [E1,E2,E3] €X.XXX,XX - Descrição

[... outras secções ...]

## 2. DIVERGÊNCIAS ENTRE EXTRATORES
(Quando extratores discordam)
- Facto/Data/Valor: [descrição]
  - E1: [versão do E1]
  - E2: [versão do E2]
  - E3: [versão do E3]

## 3. CONTROLO DE COBERTURA (OBRIGATÓRIO)

**[E1] encontrou exclusivamente:**
- facto A → incorporado em: [onde está]
- data B → incorporado em: [onde está]
(ou: "(nenhum — todos os factos foram partilhados)")

**[E2] encontrou exclusivamente:**
- valor C → incorporado em: [onde está]
(ou: "(nenhum — todos os factos foram partilhados)")

**[E3] encontrou exclusivamente:**
- referência D → incorporado em: [onde está]
(ou: "(nenhum — todos os factos foram partilhados)")

**Confirmação:** SIM
(escreve "Confirmação: SIM" se TUDO foi incorporado)
(escreve "Confirmação: NÃO" se algo ficou de fora)

**ITENS NÃO INCORPORADOS:**
- [EX] item: razão CONCRETA por não incorporar
(ou: "(nenhum)" se Confirmação=SIM)

═══════════════════════════════════════════════════════════════════════════
REGRAS CRÍTICAS
═══════════════════════════════════════════════════════════════════════════

✅ SEMPRE:
- Preservar informação única
- Marcar origem claramente [E1,E2,E3]
- Listar divergências explicitamente
- Preencher CONTROLO DE COBERTURA
- Confirmar que TUDO foi incorporado

❌ NUNCA:
- Eliminar informação única sem razão muito forte
- Escolher entre versões divergentes (listar ambas!)
- Omitir dados "sensíveis" (profissional autorizado!)
- Deixar controlo de cobertura incompleto

═══════════════════════════════════════════════════════════════════════════
LEMBRETE FINAL
═══════════════════════════════════════════════════════════════════════════

Este é um sistema profissional de assessoria técnica.
O utilizador PRECISA de TODA a informação para exercer funções.
Na dúvida: MANTÉM. Melhor redundância que perda.

COMEÇA A CONSOLIDAÇÃO AGORA:
"""
