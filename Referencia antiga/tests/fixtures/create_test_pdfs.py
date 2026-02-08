# -*- coding: utf-8 -*-
"""
Script para criar PDFs de teste para verificação E2E.
- pdf_texto_normal.pdf: PDF com texto digital (3 páginas)
- pdf_scan_legivel.pdf: PDF simulando scan legível (3 páginas, algumas com OCR hint)
- pdf_scan_mau.pdf: PDF simulando scan de má qualidade (3 páginas, com problemas)
"""

from fpdf import FPDF
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent


def create_texto_normal_pdf():
    """PDF com texto digital normal - sem problemas de OCR."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Página 1 - Cabeçalho e partes
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "CONTRATO DE ARRENDAMENTO PARA HABITACAO", ln=True, align="C")
    pdf.ln(10)

    pdf.set_font("Helvetica", "", 12)
    pdf.multi_cell(0, 8, """Entre:

SENHORIO:
Nome: Joao Antonio Marques da Silva
NIF: 123456789
Morada: Rua das Flores, n. 123, 4050-120 Porto

ARRENDATARIO:
Nome: Maria Jose Ferreira dos Santos
NIF: 987654321
Morada: Avenida da Liberdade, n. 456, 1250-096 Lisboa

E celebrado o presente contrato de arrendamento para habitacao, nos termos do artigo 1022. e seguintes do Codigo Civil e da Lei n. 6/2006 de 27 de Fevereiro (NRAU).""")

    # Página 2 - Cláusulas
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "CLAUSULAS DO CONTRATO", ln=True)
    pdf.ln(5)

    pdf.set_font("Helvetica", "", 12)
    pdf.multi_cell(0, 8, """CLAUSULA PRIMEIRA - OBJETO
O Senhorio da de arrendamento ao Arrendatario a fracao autonoma designada pela letra "B", correspondente ao 1. andar direito do predio urbano sito na Rua do Almada, n. 789, 4050-037 Porto.

CLAUSULA SEGUNDA - DURACAO
O presente contrato e celebrado pelo prazo de 5 (cinco) anos, com inicio em 01/01/2024 e termo em 31/12/2028.

CLAUSULA TERCEIRA - RENDA
1. A renda mensal e fixada em 750,00 EUR (setecentos e cinquenta euros).
2. O pagamento deve ser feito ate ao dia 8 de cada mes.

CLAUSULA QUARTA - CAUCAO
O Arrendatario entrega ao Senhorio a quantia de 1.500,00 EUR a titulo de caucao.""")

    # Página 3 - Assinaturas
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "ASSINATURAS", ln=True)
    pdf.ln(10)

    pdf.set_font("Helvetica", "", 12)
    pdf.multi_cell(0, 8, """Porto, 15 de Dezembro de 2023

O Senhorio: ___________________________
(Joao Antonio Marques da Silva)

O Arrendatario: ___________________________
(Maria Jose Ferreira dos Santos)

Testemunhas:
1. Ana Paula Rodrigues (BI: 11111111)
2. Manuel Antonio Costa (BI: 22222222)""")

    output_path = OUTPUT_DIR / "pdf_texto_normal.pdf"
    pdf.output(str(output_path))
    print(f"Criado: {output_path} ({output_path.stat().st_size} bytes)")
    return output_path


def create_scan_legivel_pdf():
    """PDF simulando scan legível - algumas páginas com hint de OCR necessário."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Página 1 - Normal
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "SENTENCA JUDICIAL", ln=True, align="C")
    pdf.ln(10)

    pdf.set_font("Helvetica", "", 12)
    pdf.multi_cell(0, 8, """Processo n. 1234/23.5TBPRT
Tribunal Judicial da Comarca do Porto
1. Juizo Local Civel

AUTOR: Jose Manuel Ferreira Santos
NIPC: 501234567

REU: Empresa ABC, Lda.
NIPC: 509876543

I - RELATORIO
O Autor intentou a presente acao declarativa contra a Re, pedindo:
a) A condenacao da Re no pagamento de 15.000,00 EUR
b) Juros de mora desde a citacao""")

    # Página 2 - Simulação de scan (com espaços irregulares, hints de OCR)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 11)  # Ligeiramente diferente
    # Texto com algumas "imperfeições" típicas de scan
    pdf.multi_cell(0, 8, """II - FUNDAMENTACAO DE FACTO

Factos Provados:
1.  O Autor celebrou contrato com a Re em 15/03/2023.
2.  O valor acordado foi de 25.000,00 EUR.
3.  A Re pagou apenas 10.000,00 EUR ate 30/06/2023.
4.  Permanece em divida o montante de 15.000,00 EUR.

Factos Nao Provados:
a)  Que a Re tenha comunicado impossibilidade de pagamento.
b)  Que existisse acordo de mora consentida.

[PAGINA DIGITALIZADA - OCR APLICADO]""")

    # Página 3 - Decisão
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "III - DECISAO", ln=True)
    pdf.ln(5)

    pdf.set_font("Helvetica", "", 12)
    pdf.multi_cell(0, 8, """Pelo exposto, julgo a acao PROCEDENTE e, em consequencia:

1. Condeno a Re Empresa ABC, Lda. a pagar ao Autor a quantia de 15.000,00 EUR.

2. Condeno a Re no pagamento de juros de mora, a taxa legal, desde a citacao ate integral pagamento.

3. Custas pela Re.

Registe e notifique.

Porto, 20 de Janeiro de 2024

O Juiz de Direito,
Dr. Antonio Manuel Pereira""")

    output_path = OUTPUT_DIR / "pdf_scan_legivel.pdf"
    pdf.output(str(output_path))
    print(f"Criado: {output_path} ({output_path.stat().st_size} bytes)")
    return output_path


def create_scan_mau_pdf():
    """PDF simulando scan de má qualidade - páginas com problemas evidentes."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Página 1 - Parcialmente legível
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "DOCUMENTO COM PROBLEMAS DE DIGITALIZACAO", ln=True, align="C")
    pdf.ln(10)

    pdf.set_font("Helvetica", "", 10)  # Fonte menor, mais difícil
    pdf.multi_cell(0, 7, """NOTA: Este documento simula um scan de ma qualidade.

Informacoes parcialmente visiveis:
- Data: ??/??/2023
- Valor: ???.00 EUR
- Partes: [ILEGIVEL]

[AVISO: PAGINA DEGRADADA - QUALIDADE INSUFICIENTE]""")

    # Página 2 - Muito pouco texto (simula página quase em branco ou muito degradada)
    pdf.add_page()
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 6, """[SCAN COM BAIXA QUALIDADE]

... texto parcialmente ilegivel ...

Apenas alguns fragmentos visiveis:
"contrato" ... "pagamento" ... "2024"

[PAGINA REQUER OCR AVANCADO]""")

    # Página 3 - Página com mais conteúdo mas "problemática"
    pdf.add_page()
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 8, """CLAUSULAS FINAIS (parcialmente legiveis)

1. Jurisdicao: Tribunal do Porto
2. Valor do litigio: [ILEGIVEL] EUR
3. Data de assinatura: provavelmente Dezembro de 2023

ASSINATURAS:
[ASSINATURA ILEGIVEL]
[ASSINATURA ILEGIVEL]

Testemunha: Nome ilegivel
Documento: [NUMERO NAO VISIVEL]

[FIM DO DOCUMENTO - QUALIDADE SCAN: BAIXA]""")

    output_path = OUTPUT_DIR / "pdf_scan_mau.pdf"
    pdf.output(str(output_path))
    print(f"Criado: {output_path} ({output_path.stat().st_size} bytes)")
    return output_path


if __name__ == "__main__":
    print("Criando PDFs de teste...\n")
    create_texto_normal_pdf()
    create_scan_legivel_pdf()
    create_scan_mau_pdf()
    print("\nTodos os PDFs criados com sucesso!")
