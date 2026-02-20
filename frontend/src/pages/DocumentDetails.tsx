import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { supabase } from "@/integrations/supabase/client";
import { Button } from "@/components/ui/button";
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion";
import { ArrowLeft, FileText, Clock, CheckCircle, AlertTriangle, Loader2, Scale, Trash2, Gavel, Search, ShieldCheck, BookOpen, BarChart3, FileDown, FileType, MessageCircleQuestion, Send, Pencil, Copy, Paperclip, Plus } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";
import type { Tables } from "@/integrations/supabase/types";
import { useToast } from "@/hooks/use-toast";
import MarkdownContent from "@/components/MarkdownContent";
import { AddDocumentModal } from "@/components/AddDocumentModal";
import { WalletIndicator } from "@/components/WalletIndicator";
import { AnalysisCostBadge } from "@/components/AnalysisCostBadge";
import { useWallet } from "@/contexts/WalletContext";

type Document = Tables<"documents">;

interface AnalysisResult {
  run_id?: string;
  area_direito?: string;
  documento?: {
    num_pages?: number;
    metadata?: {
      pages_ok?: number;
      pages_suspeita?: number;
      pages_sem_texto?: number;
      pages_problematic?: number;
      extractor?: string;
      scanned_pages?: Record<string, string>;
    };
    [key: string]: unknown;
  };
  documento_texto?: string;
  texto_original?: string;
  total_chars?: number;
  total_words?: number;
  fase1_extracoes?: Array<{ fase?: string; modelo?: string; role?: string; conteudo?: string }>;
  fase1_agregado_consolidado?: string;
  fase2_auditorias?: Array<{ fase?: string; modelo?: string; role?: string; conteudo?: string }>;
  fase2_chefe_consolidado?: string;
  fase3_pareceres?: Array<{ fase?: string; modelo?: string; role?: string; conteudo?: string }>;
  fase3_presidente?: string;
  verificacoes_legais?: Array<{
    diploma?: string;
    artigo?: string;
    texto_original?: string;
    texto_normalizado?: string;
    existe?: boolean;
    texto_encontrado?: string;
    fonte?: string;
    status?: string;
    simbolo?: string;
    aplicabilidade?: string;
    timestamp?: string;
    hash_texto?: string;
    mensagem?: string;
    // Temporal verification fields (optional)
    versao_data_factos?: string;
    versao_actual?: string;
    existe_data_factos?: boolean;
    existe_actual?: boolean;
    artigo_alterado?: boolean;
    [key: string]: unknown;
  }>;
  veredicto_final?: string;
  simbolo_final?: string;
  status_final?: string;
  total_tokens?: number;
  total_latencia_ms?: number;
  sucesso?: boolean;
  perguntas_utilizador?: string[];
  respostas_juizes_qa?: Array<{ pergunta?: string; juiz?: string; resposta?: string; [key: string]: unknown }>;
  respostas_finais_qa?: Array<{ pergunta?: string; resposta?: string; [key: string]: unknown }> | string[];
  qa_history?: Array<{
    question: string;
    answer: string;
    timestamp?: string;
    individual_responses?: Array<{ model: string; response: string }>;
    respostas_individuais?: Array<{ model: string; response: string }>;
  }>;
  documentos_adicionais?: Array<{
    filename: string;
    text?: string;
    num_chars?: number;
    added_at?: string;
    [key: string]: unknown;
  }>;
}

const statusConfig = {
  pending: { label: "Pendente", icon: Clock, className: "text-warning bg-warning/10" },
  analyzing: { label: "A analisar", icon: Loader2, className: "text-accent bg-accent/10" },
  completed: { label: "Concluído", icon: CheckCircle, className: "text-success bg-success/10" },
  error: { label: "Erro", icon: AlertTriangle, className: "text-destructive bg-destructive/10" },
  interrupted: { label: "Interrompido", icon: AlertTriangle, className: "text-orange-600 bg-orange-100" },
  abandoned: { label: "Abandonado", icon: AlertTriangle, className: "text-muted-foreground bg-muted" },
};

const DocumentDetails = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [document, setDocument] = useState<Document | null>(null);
  const [loading, setLoading] = useState(true);
  const { toast } = useToast();
  const [askQuestion, setAskQuestion] = useState("");
  const [askLoading, setAskLoading] = useState(false);
  const qaEndRef = useRef<HTMLDivElement>(null);

  // Inline title editing
  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState("");
  const titleInputRef = useRef<HTMLInputElement>(null);

  // Delete confirmation
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);

  // Add document modal
  const [showAddDocModal, setShowAddDocModal] = useState(false);

  const reloadDocument = useCallback(async () => {
    const { data } = await supabase
      .from("documents")
      .select("*")
      .eq("id", id!)
      .maybeSingle();
    if (data) setDocument(data);
  }, [id]);

  useEffect(() => {
    const fetchDocument = async () => {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) { navigate("/auth"); return; }
      await reloadDocument();
      setLoading(false);
    };
    fetchDocument();
  }, [id, navigate, reloadDocument]);

  useEffect(() => {
    if (editingTitle && titleInputRef.current) {
      titleInputRef.current.focus();
      titleInputRef.current.select();
    }
  }, [editingTitle]);

  const handleTitleSave = async () => {
    if (!document || !titleDraft.trim() || titleDraft.trim() === document.title) {
      setEditingTitle(false);
      return;
    }
    const newTitle = titleDraft.trim();
    const { error } = await supabase.from("documents").update({ title: newTitle }).eq("id", document.id);
    if (error) {
      toast({ title: "Erro", description: error.message, variant: "destructive" });
    } else {
      setDocument({ ...document, title: newTitle });
      toast({ title: "Título atualizado" });
    }
    setEditingTitle(false);
  };

  const handleDelete = async () => {
    if (!document) return;
    const { error } = await supabase.from("documents").delete().eq("id", document.id);
    if (error) {
      toast({ title: "Erro", description: error.message, variant: "destructive" });
    } else {
      toast({ title: "Análise eliminada" });
      navigate("/dashboard");
    }
  };

  const handleExport = async (format: "pdf" | "docx") => {
    if (!document) return;
    try {
      const { data: { session } } = await supabase.auth.getSession();
      const response = await fetch(`https://tribunal-saas.onrender.com/export/${format}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(session?.access_token ? { "Authorization": `Bearer ${session.access_token}` } : {}),
        },
        body: JSON.stringify({ analysis_result: (document as any).analysis_result }),
      });
      if (!response.ok) throw new Error("Erro ao exportar");
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = window.document.createElement("a");
      a.href = url;
      a.download = `relatorio_${document.title}.${format}`;
      a.click();
      window.URL.revokeObjectURL(url);
    } catch {
      toast({ title: "Erro", description: "Não foi possível exportar o relatório.", variant: "destructive" });
    }
  };

  const handleAskQuestion = async () => {
    if (!document || !askQuestion.trim()) return;
    setAskLoading(true);
    const analysis = (document as any).analysis_result as AnalysisResult | null;
    try {
      const { data: { session: askSession } } = await supabase.auth.getSession();
      const response = await fetch("https://tribunal-saas.onrender.com/ask", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(askSession?.access_token ? { "Authorization": `Bearer ${askSession.access_token}` } : {}),
        },
        body: JSON.stringify({
          question: askQuestion.trim(),
          analysis_result: (document as any).analysis_result,
          document_id: document.id,
          previous_qa: analysis?.qa_history?.map(qa => ({
            question: qa.question,
            answer: qa.answer,
          })) || [],
        }),
      });
      if (!response.ok) throw new Error("Erro ao perguntar");
      setAskQuestion("");
      // Reload document to get updated qa_history from backend
      await reloadDocument();
      setTimeout(() => qaEndRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
    } catch {
      toast({ title: "Erro", description: "Não foi possível obter resposta.", variant: "destructive" });
    } finally {
      setAskLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="w-8 h-8 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    );
  }

  if (!document) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="text-center animate-fade-in">
          <FileText className="w-12 h-12 text-muted-foreground/40 mx-auto mb-4" />
          <h2 className="text-lg font-medium text-foreground mb-2">Documento não encontrado</h2>
          <Button variant="outline" onClick={() => navigate("/dashboard")}>Voltar ao dashboard</Button>
        </div>
      </div>
    );
  }

  const status = statusConfig[document.status as keyof typeof statusConfig] || statusConfig.pending;
  const StatusIcon = status.icon;
  const analysis = (document as any).analysis_result as AnalysisResult | null;

  const originalText = analysis?.documento_texto || analysis?.texto_original || null;
  const totalChars = analysis?.total_chars || (originalText ? originalText.length : null);
  const totalWords = analysis?.total_words || (originalText ? originalText.split(/\s+/).filter(Boolean).length : null);
  const numPages = analysis?.documento?.num_pages || null;

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card/80 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg gradient-navy flex items-center justify-center">
              <Scale className="w-5 h-5 text-primary-foreground" />
            </div>
            <span className="text-lg font-semibold text-foreground tracking-tight">LexForum</span>
          </div>
          <WalletIndicator />
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Button variant="ghost" onClick={() => navigate("/dashboard")} className="mb-6 text-muted-foreground">
          <ArrowLeft className="w-4 h-4 mr-2" />
          Voltar
        </Button>

        <div className="animate-fade-in">
          {/* Title */}
          <div className="flex items-start justify-between mb-6">
            <div>
              <div className="flex items-center gap-3 mb-2">
                {editingTitle ? (
                  <Input
                    ref={titleInputRef}
                    value={titleDraft}
                    onChange={(e) => setTitleDraft(e.target.value)}
                    onBlur={handleTitleSave}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleTitleSave();
                      if (e.key === "Escape") setEditingTitle(false);
                    }}
                    className="text-2xl font-semibold h-auto py-1 px-2 w-[400px]"
                  />
                ) : (
                  <button
                    className="flex items-center gap-2 group cursor-pointer text-left"
                    onClick={() => { setTitleDraft(document.title); setEditingTitle(true); }}
                  >
                    <h1 className="text-2xl font-semibold text-foreground">{document.title}</h1>
                    <Pencil className="w-4 h-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
                  </button>
                )}
                <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium ${status.className}`}>
                  <StatusIcon className="w-3.5 h-3.5" />
                  {status.label}
                </span>
              </div>
              <p className="text-muted-foreground">{document.file_name}</p>
              <p className="text-sm text-muted-foreground mt-1">
                Carregado a {new Date(document.created_at).toLocaleDateString("pt-PT", {
                  day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit",
                })}
              </p>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              {analysis && (
                <>
                  <Button variant="outline" size="sm" onClick={() => setShowAddDocModal(true)}>
                    <Plus className="w-4 h-4 mr-2" />
                    📎 Adicionar Documento
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => handleExport("pdf")}>
                    <FileDown className="w-4 h-4 mr-2" />
                    Exportar PDF
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => handleExport("docx")}>
                    <FileType className="w-4 h-4 mr-2" />
                    Exportar Word
                  </Button>
                </>
              )}
              <Button variant="outline" size="sm" onClick={() => setShowDeleteDialog(true)} className="text-destructive hover:text-destructive">
                <Trash2 className="w-4 h-4 mr-2" />
                Eliminar
              </Button>
            </div>
          </div>

          {/* Delete Confirmation Dialog */}
          <Dialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
            <DialogContent className="sm:max-w-md">
              <DialogHeader>
                <DialogTitle>Eliminar análise</DialogTitle>
                <DialogDescription>
                  Tem a certeza que deseja eliminar esta análise? Esta ação é irreversível.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter className="gap-2 sm:gap-0">
                <Button variant="outline" onClick={() => setShowDeleteDialog(false)}>Cancelar</Button>
                <Button variant="destructive" onClick={() => { setShowDeleteDialog(false); handleDelete(); }}>
                  Eliminar definitivamente
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>

          {/* Verdict Banner */}
          {analysis?.veredicto_final && (
            <div className="bg-card rounded-xl border border-border p-6 mb-6">
              <div className="flex items-center gap-3 mb-3">
                <span className="text-3xl">{analysis.simbolo_final || "⚖️"}</span>
                <div>
                  <h2 className="text-lg font-semibold text-foreground">Conclusão</h2>
                  {analysis.area_direito && (
                    <span className="text-xs font-medium text-accent bg-accent/10 px-2 py-0.5 rounded-full">
                      {analysis.area_direito}
                    </span>
                  )}
                </div>
              </div>
              <MarkdownContent content={analysis.veredicto_final} />
              {analysis.status_final && (
                <p className="mt-3 text-sm font-medium text-muted-foreground">
                  Estado: {analysis.status_final}
                </p>
              )}
            </div>
          )}

          {/* Cost/Time Stats Bar */}
          {analysis && (
            <div className="flex items-center gap-4 text-xs text-muted-foreground px-1 py-2 mb-4 flex-wrap">
              {(document.custo_cobrado_usd != null || (analysis as any)?.custos?.custo_cliente_usd != null) && (
                <span>💰 Custo: ${(document.custo_cobrado_usd ?? (analysis as any).custos.custo_cliente_usd as number).toFixed(2)}</span>
              )}
              {(analysis as any)?.tempo_total_ms != null && (
                <span>⏱️ Tempo: {Math.floor((analysis as any).tempo_total_ms / 60000)}m {Math.floor(((analysis as any).tempo_total_ms % 60000) / 1000)}s</span>
              )}
              {(analysis as any)?.custos?.custo_total_usd != null && (analysis as any)?.custos?.budget_total_usd != null && (
                <span>📊 Budget: {(((analysis as any).custos.custo_total_usd / (analysis as any).custos.budget_total_usd) * 100).toFixed(0)}%</span>
              )}
            </div>
          )}

          {/* Analysis Cost Badge */}
          {analysis && (analysis as any).custo_analise != null && (
            <div className="mb-6">
              <AnalysisCostBadge
                cost={(analysis as any).custo_analise}
                balanceAfter={(analysis as any).saldo_apos}
              />
            </div>
          )}

          {/* No analysis fallback */}
          {!analysis && (
            <div className="bg-card rounded-xl border border-border p-6 mb-6">
              <h2 className="text-lg font-semibold text-foreground mb-3">Resumo da Análise</h2>
              {document.summary ? (
                <MarkdownContent content={document.summary} />
              ) : (
                <p className="text-muted-foreground italic">A análise ainda não foi concluída. Os resultados serão apresentados aqui assim que estiverem disponíveis.</p>
              )}
            </div>
          )}

          {/* Original Document Section */}
          {analysis && (
            <div className="bg-card rounded-xl border border-border p-6 mb-6">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-3">
                  <Paperclip className="w-5 h-5 text-muted-foreground" />
                  <h2 className="text-lg font-semibold text-foreground">Documento Original</h2>
                </div>
                {originalText && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      navigator.clipboard.writeText(originalText);
                      toast({ title: "Texto copiado para o clipboard" });
                    }}
                  >
                    <Copy className="w-4 h-4 mr-2" />
                    Copiar texto
                  </Button>
                )}
              </div>

              <div className="flex items-center gap-4 flex-wrap text-xs text-muted-foreground mb-3">
                <span>📎 Ficheiro: {document.file_name}</span>
                {totalChars != null && <span>{totalChars.toLocaleString("pt-PT")} caracteres</span>}
                {totalWords != null && <span>{totalWords.toLocaleString("pt-PT")} palavras</span>}
                {numPages != null && <span>{numPages} página{numPages !== 1 ? "s" : ""}</span>}
              </div>

              {originalText ? (
                <div className="bg-muted/30 rounded-lg p-4 max-h-[300px] overflow-y-auto">
                  <pre className="text-sm text-foreground whitespace-pre-wrap font-sans">{originalText}</pre>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground italic">Texto original não disponível na análise.</p>
              )}

              {/* Additional Documents */}
              {analysis.documentos_adicionais && analysis.documentos_adicionais.length > 0 && (
                <div className="mt-4">
                  <h3 className="text-sm font-semibold text-foreground mb-2">
                    Documentos Adicionais ({analysis.documentos_adicionais.length})
                  </h3>
                  <Accordion type="multiple" className="space-y-2">
                    {analysis.documentos_adicionais.map((doc, i) => (
                      <AccordionItem key={i} value={`addoc-${i}`} className="border border-border rounded-lg px-4">
                        <AccordionTrigger className="hover:no-underline py-3">
                          <div className="flex items-center gap-3 text-sm">
                            <span>📎</span>
                            <span className="font-medium text-foreground">{doc.filename}</span>
                            {doc.added_at && (
                              <span className="text-xs text-muted-foreground">
                                {new Date(doc.added_at).toLocaleDateString("pt-PT", { day: "numeric", month: "short", year: "numeric" })}
                              </span>
                            )}
                            {doc.num_chars != null && (
                              <span className="text-xs text-muted-foreground">{doc.num_chars.toLocaleString("pt-PT")} caracteres</span>
                            )}
                          </div>
                        </AccordionTrigger>
                        <AccordionContent>
                          {doc.text ? (
                            <div className="bg-muted/30 rounded-lg p-3 max-h-[200px] overflow-y-auto">
                              <pre className="text-sm text-foreground whitespace-pre-wrap font-sans">{doc.text}</pre>
                            </div>
                          ) : (
                            <p className="text-sm text-muted-foreground italic">Texto não disponível.</p>
                          )}
                        </AccordionContent>
                      </AccordionItem>
                    ))}
                  </Accordion>
                </div>
              )}
            </div>
          )}

          {/* Document Pages Info */}
          {analysis && (() => {
            const doc = analysis.documento;
            const meta = doc?.metadata;
            const docPages = doc?.num_pages;
            const problematic = (meta?.pages_problematic ?? 0) + (meta?.pages_sem_texto ?? 0) + (meta?.pages_suspeita ?? 0);
            const hasProblems = problematic > 0;

            if (!docPages && !hasProblems) return null;

            return (
              <div className={`rounded-xl border p-4 mb-6 ${hasProblems ? "bg-warning/10 border-warning/30" : "bg-muted/30 border-border"}`}>
                <div className="flex items-start gap-3">
                  <span className="text-lg">{hasProblems ? "⚠️" : "📄"}</span>
                  <div className="space-y-1">
                    {docPages && (
                      <p className="text-sm font-medium text-foreground">
                        Documento: {docPages} página{docPages !== 1 ? "s" : ""} analisada{docPages !== 1 ? "s" : ""}
                        {meta?.pages_ok != null && ` · ${meta.pages_ok} com texto`}
                      </p>
                    )}
                    {hasProblems && (
                      <>
                        <p className="text-sm text-warning">
                          Foram detetadas {problematic} página{problematic !== 1 ? "s" : ""} com possíveis problemas de extração. Os resultados dessas páginas podem estar incompletos.
                        </p>
                        <p className="text-xs text-muted-foreground">
                          💡 Para documentos com páginas digitalizadas (scanned), recomendamos o upload em formato com texto selecionável.
                        </p>
                      </>
                    )}
                  </div>
                </div>
              </div>
            );
          })()}

          {/* Phases Accordion */}
          {analysis && (
            <Accordion type="multiple" defaultValue={["parecer"]} className="space-y-3">
              {/* Relatório de Análise (conteúdo principal) */}
              {analysis.fase3_presidente && (
                <AccordionItem value="parecer" className="bg-card rounded-xl border border-border px-6">
                  <AccordionTrigger className="hover:no-underline">
                    <div className="flex items-center gap-3">
                      <BookOpen className="w-5 h-5 text-success" />
                      <span className="font-semibold text-foreground">Relatório de Análise</span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent>
                    <MarkdownContent content={analysis.fase3_presidente} />
                  </AccordionContent>
                </AccordionItem>
              )}

              {/* Detalhes Técnicos (fases internas — colapsado por defeito) */}
              {(analysis.fase1_agregado_consolidado || analysis.fase2_chefe_consolidado || (analysis.fase3_pareceres && analysis.fase3_pareceres.length > 0)) && (
                <AccordionItem value="detalhes_tecnicos" className="bg-card rounded-xl border border-border px-6">
                  <AccordionTrigger className="hover:no-underline">
                    <div className="flex items-center gap-3">
                      <Search className="w-5 h-5 text-muted-foreground" />
                      <span className="font-semibold text-foreground">Detalhes Técnicos</span>
                      <span className="text-xs text-muted-foreground">(Extração, Verificação, Análise)</span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent>
                    <Accordion type="multiple" className="space-y-3">
                      {/* Phase 1 */}
                      {analysis.fase1_agregado_consolidado && (
                        <AccordionItem value="fase1" className="border border-border rounded-lg px-4">
                          <AccordionTrigger className="hover:no-underline py-3">
                            <div className="flex items-center gap-3">
                              <Search className="w-4 h-4 text-accent" />
                              <span className="text-sm font-medium text-foreground">Extração</span>
                            </div>
                          </AccordionTrigger>
                          <AccordionContent>
                            <MarkdownContent content={analysis.fase1_agregado_consolidado} />
                            {analysis.fase1_extracoes && analysis.fase1_extracoes.length > 0 && (
                              <details className="mt-4">
                                <summary className="text-xs font-medium text-muted-foreground cursor-pointer hover:text-foreground">
                                  Ver extrações individuais ({analysis.fase1_extracoes.length})
                                </summary>
                                <div className="mt-2 space-y-3">
                                  {analysis.fase1_extracoes.map((ext, i) => (
                                    <div key={i} className="border border-border rounded-lg p-4">
                                      <p className="text-xs font-medium text-accent mb-2">
                                        {ext.role || `Extrator ${i + 1}`} {ext.modelo && `· ${ext.modelo}`}
                                      </p>
                                      {ext.conteudo && <MarkdownContent content={ext.conteudo} />}
                                    </div>
                                  ))}
                                </div>
                              </details>
                            )}
                          </AccordionContent>
                        </AccordionItem>
                      )}

                      {/* Phase 2 */}
                      {analysis.fase2_chefe_consolidado && (
                        <AccordionItem value="fase2" className="border border-border rounded-lg px-4">
                          <AccordionTrigger className="hover:no-underline py-3">
                            <div className="flex items-center gap-3">
                              <ShieldCheck className="w-4 h-4 text-warning" />
                              <span className="text-sm font-medium text-foreground">Verificação</span>
                            </div>
                          </AccordionTrigger>
                          <AccordionContent>
                            <MarkdownContent content={analysis.fase2_chefe_consolidado} />
                            {analysis.fase2_auditorias && analysis.fase2_auditorias.length > 0 && (
                              <details className="mt-4">
                                <summary className="text-xs font-medium text-muted-foreground cursor-pointer hover:text-foreground">
                                  Ver auditorias individuais ({analysis.fase2_auditorias.length})
                                </summary>
                                <div className="mt-2 space-y-3">
                                  {analysis.fase2_auditorias.map((aud, i) => (
                                    <div key={i} className="border border-border rounded-lg p-4">
                                      <p className="text-xs font-medium text-warning mb-2">
                                        {aud.role || `Verificador ${i + 1}`} {aud.modelo && `· ${aud.modelo}`}
                                      </p>
                                      {aud.conteudo && <MarkdownContent content={aud.conteudo} />}
                                    </div>
                                  ))}
                                </div>
                              </details>
                            )}
                          </AccordionContent>
                        </AccordionItem>
                      )}

                      {/* Phase 3 */}
                      {analysis.fase3_pareceres && analysis.fase3_pareceres.length > 0 && (
                        <AccordionItem value="fase3" className="border border-border rounded-lg px-4">
                          <AccordionTrigger className="hover:no-underline py-3">
                            <div className="flex items-center gap-3">
                              <Gavel className="w-4 h-4 text-primary" />
                              <span className="text-sm font-medium text-foreground">Análise</span>
                            </div>
                          </AccordionTrigger>
                          <AccordionContent>
                            <div className="space-y-4">
                              {analysis.fase3_pareceres.map((parecer, i) => (
                                <div key={i} className="border border-border rounded-lg p-4">
                                  <p className="text-xs font-medium text-primary mb-2">
                                    {parecer.role || `Analista ${i + 1}`} {parecer.modelo && `· ${parecer.modelo}`}
                                  </p>
                                  {parecer.conteudo && <MarkdownContent content={parecer.conteudo} />}
                                </div>
                              ))}
                            </div>
                          </AccordionContent>
                        </AccordionItem>
                      )}
                    </Accordion>
                  </AccordionContent>
                </AccordionItem>
              )}

              {/* Legal Checks */}
              {analysis.verificacoes_legais && analysis.verificacoes_legais.length > 0 && (() => {
                const vList = analysis.verificacoes_legais!;
                const countAprovado = vList.filter(v => v.status === "confirmado" || v.status === "aprovado").length;
                const countRejeitado = vList.filter(v => v.status === "rejeitado" || v.status === "erro" || v.status === "nao_encontrado").length;
                const countAtencao = vList.filter(v => v.status === "atencao").length;
                const countAlterado = vList.filter(v => v.artigo_alterado === true).length;
                const hasTemporalData = vList.some(v => v.versao_data_factos !== undefined || v.versao_actual !== undefined);

                return (
                  <AccordionItem value="legal" className="bg-card rounded-xl border border-border px-6">
                    <AccordionTrigger className="hover:no-underline">
                      <div className="flex items-center gap-3">
                        <ShieldCheck className="w-5 h-5 text-accent" />
                        <span className="font-semibold text-foreground">📋 Verificações Legais (DRE)</span>
                      </div>
                    </AccordionTrigger>
                    <AccordionContent>
                      <p className="text-xs text-muted-foreground mb-3">
                        Verificação automática de artigos citados no Diário da República Eletrónico
                      </p>

                      {/* Summary metrics */}
                      <div className="flex items-center gap-3 flex-wrap mb-4">
                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-success/10 text-success">
                          ✓ Aprovadas: {countAprovado}
                        </span>
                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-destructive/10 text-destructive">
                          ✗ Rejeitadas: {countRejeitado}
                        </span>
                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-warning/10 text-warning">
                          ⚠ Atenção: {countAtencao}
                        </span>
                        {countAlterado > 0 && (
                          <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-bold bg-orange-500/15 text-orange-600 dark:text-orange-400">
                            ⚠ Diplomas alterados: {countAlterado}
                          </span>
                        )}
                      </div>

                      <div className="rounded-lg border border-border overflow-hidden">
                        <table className="w-full text-sm">
                          <thead>
                            <tr className="bg-muted/50">
                              <th className="text-left px-3 py-2 font-medium text-muted-foreground w-12"></th>
                              <th className="text-left px-3 py-2 font-medium text-muted-foreground">Artigo</th>
                              <th className="text-left px-3 py-2 font-medium text-muted-foreground">Diploma</th>
                              <th className="text-left px-3 py-2 font-medium text-muted-foreground">Estado</th>
                            </tr>
                          </thead>
                          <tbody>
                            {vList.map((v, i) => {
                              const vStatus = String(v.status || "");
                              const rowBg = vStatus === "confirmado" || vStatus === "aprovado"
                                ? "bg-success/10"
                                : vStatus === "atencao"
                                  ? "bg-warning/10"
                                  : vStatus === "erro" || vStatus === "nao_encontrado" || vStatus === "rejeitado"
                                    ? "bg-destructive/10"
                                    : "";

                              const hasTemporal = v.versao_data_factos !== undefined || v.versao_actual !== undefined;
                              const possiblyRevoked = v.existe_data_factos === true && v.existe_actual === false;

                              return (
                                <tr key={i} className={`border-t border-border ${rowBg}`}>
                                  <td className="px-3 py-2.5 text-center text-base align-top">{String(v.simbolo || "—")}</td>
                                  <td className="px-3 py-2.5 font-medium text-foreground align-top">Art. {String(v.artigo || "—")}º</td>
                                  <td className="px-3 py-2.5 text-muted-foreground align-top">{String(v.diploma || "—")}</td>
                                  <td className="px-3 py-2.5 text-muted-foreground align-top">
                                    <div>{String(v.mensagem || "—")}</div>

                                    {/* Temporal verification block */}
                                    {hasTemporal && (
                                      <div className="mt-2 p-2.5 rounded-md border border-dashed border-border bg-muted/30 space-y-1.5">
                                        {v.versao_data_factos !== undefined && (
                                          <div className="flex items-start gap-1.5 text-xs">
                                            <span>📅</span>
                                            <span>
                                              À data dos factos: {v.versao_data_factos} —{" "}
                                              <span className={v.existe_data_factos ? "text-success font-medium" : "text-destructive font-medium"}>
                                                {v.existe_data_factos ? "✓ EXISTE" : "✗ NÃO ENCONTRADO"}
                                              </span>
                                            </span>
                                          </div>
                                        )}
                                        {v.versao_actual !== undefined && (
                                          <div className="flex items-start gap-1.5 text-xs">
                                            <span>📜</span>
                                            <span>
                                              Versão actual: {v.versao_actual} —{" "}
                                              <span className={v.existe_actual ? "text-success font-medium" : "text-destructive font-medium"}>
                                                {v.existe_actual ? "✓ EXISTE" : "✗ NÃO ENCONTRADO"}
                                              </span>
                                            </span>
                                          </div>
                                        )}
                                        {v.artigo_alterado === true && (
                                          <div className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-bold bg-orange-500/15 text-orange-600 dark:text-orange-400 mt-1">
                                            ⚠ DIPLOMA ALTERADO entre as duas datas
                                          </div>
                                        )}
                                        {possiblyRevoked && (
                                          <div className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-bold bg-destructive/15 text-destructive mt-1">
                                            ⚠ ARTIGO POSSIVELMENTE REVOGADO ou renumerado
                                          </div>
                                        )}
                                      </div>
                                    )}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </AccordionContent>
                  </AccordionItem>
                );
              })()}

              {/* Stats */}
              {(analysis.total_tokens || analysis.total_latencia_ms || analysis.run_id) && (
                <AccordionItem value="stats" className="bg-card rounded-xl border border-border px-6">
                  <AccordionTrigger className="hover:no-underline">
                    <div className="flex items-center gap-3">
                      <BarChart3 className="w-5 h-5 text-muted-foreground" />
                      <span className="font-semibold text-foreground">Estatísticas</span>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent>
                    {(() => {
                      const costUsd = analysis.total_tokens != null ? analysis.total_tokens * 0.000003 : null;
                      let budgetLimit: number | null = null;
                      try { budgetLimit = parseFloat(localStorage.getItem("max_budget_usd") || ""); } catch {}
                      const overBudget = costUsd != null && budgetLimit != null && !isNaN(budgetLimit) && costUsd > budgetLimit;

                      return (
                        <>
                          <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
                            {analysis.run_id && (
                              <div className="bg-muted/50 rounded-lg p-3">
                                <p className="text-xs text-muted-foreground mb-1">Run ID</p>
                                <p className="text-sm font-mono text-foreground truncate">{analysis.run_id}</p>
                              </div>
                            )}
                            {analysis.total_tokens != null && (
                              <div className="bg-muted/50 rounded-lg p-3">
                                <p className="text-xs text-muted-foreground mb-1">Tokens Usados</p>
                                <p className="text-sm font-semibold text-foreground">{analysis.total_tokens.toLocaleString("pt-PT")}</p>
                              </div>
                            )}
                            {costUsd != null && (
                              <div className="bg-muted/50 rounded-lg p-3">
                                <p className="text-xs text-muted-foreground mb-1">Custo Estimado</p>
                                <p className="text-sm font-semibold text-foreground">${costUsd.toFixed(4)}</p>
                              </div>
                            )}
                            {analysis.total_latencia_ms != null && (
                              <div className="bg-muted/50 rounded-lg p-3">
                                <p className="text-xs text-muted-foreground mb-1">Latência Total</p>
                                <p className="text-sm font-semibold text-foreground">{(analysis.total_latencia_ms / 1000).toFixed(1)}s</p>
                              </div>
                            )}
                          </div>
                          {overBudget && (
                            <div className="mt-3 rounded-lg bg-warning/10 border border-warning/30 p-3 flex items-start gap-2">
                              <span className="text-sm">⚠️</span>
                              <p className="text-sm text-warning">
                                O custo estimado (${costUsd!.toFixed(4)}) excedeu o budget configurado (${budgetLimit!.toFixed(2)}).
                              </p>
                            </div>
                          )}
                        </>
                      );
                    })()}
                  </AccordionContent>
                </AccordionItem>
              )}
            </Accordion>
          )}

          {/* Q&A Section */}
          {analysis?.perguntas_utilizador && analysis.perguntas_utilizador.length > 0 && (
            <div className="mt-6 bg-card rounded-xl border border-border p-6">
              <div className="flex items-center gap-3 mb-4">
                <MessageCircleQuestion className="w-5 h-5 text-accent" />
                <h2 className="text-lg font-semibold text-foreground">Perguntas & Respostas</h2>
              </div>
              <div className="space-y-4">
                {analysis.perguntas_utilizador.map((pergunta, i) => {
                  const respostaFinal = analysis.respostas_finais_qa?.[i];
                  const respostaText = typeof respostaFinal === "string"
                    ? respostaFinal
                    : (respostaFinal as any)?.resposta || null;

                  const respostasJuizes = analysis.respostas_juizes_qa?.filter(
                    (r) => r.pergunta === pergunta || (r as any).pergunta_index === i
                  );

                  return (
                    <div key={i} className="border border-border rounded-lg p-4">
                      <h3 className="text-sm font-semibold text-foreground mb-2">
                        ❓ {pergunta}
                      </h3>
                      {respostaText ? (
                        <MarkdownContent content={respostaText} />
                      ) : (
                        <p className="text-sm text-muted-foreground italic">Sem resposta consolidada.</p>
                      )}
                      {respostasJuizes && respostasJuizes.length > 0 && (
                        <details className="mt-3">
                          <summary className="text-xs font-medium text-muted-foreground cursor-pointer hover:text-foreground">
                            Ver respostas individuais dos analistas ({respostasJuizes.length})
                          </summary>
                          <div className="mt-2 space-y-2">
                            {respostasJuizes.map((r, j) => (
                              <div key={j} className="border border-border rounded-lg p-3">
                                <p className="text-xs font-medium text-primary mb-1">
                                  {r.juiz || `Analista ${j + 1}`}
                                </p>
                                {r.resposta && <MarkdownContent content={r.resposta} />}
                              </div>
                            ))}
                          </div>
                        </details>
                      )}
                    </div>
                  );
                })}
              </div>
              <p className="text-xs text-muted-foreground mt-4">
                💡 Para fazer novas perguntas, use a secção abaixo.
              </p>
            </div>
          )}

          {/* Ask Question Section */}
          {analysis && (
            <div className="mt-6 bg-muted/30 rounded-xl border border-border p-6">
              <div className="flex items-center gap-3 mb-4">
                <MessageCircleQuestion className="w-5 h-5 text-primary" />
                <h2 className="text-lg font-semibold text-foreground">Fazer Pergunta</h2>
              </div>

              {/* Show qa_history from analysis_result */}
              {analysis.qa_history && analysis.qa_history.length > 0 && (
                <div className="space-y-3 mb-4">
                  {analysis.qa_history.map((qa, i) => {
                    const individual = qa.individual_responses || qa.respostas_individuais || [];
                    return (
                      <div key={i} className="border border-border rounded-lg bg-card p-4">
                        <div className="flex items-center justify-between mb-2">
                          <h3 className="text-sm font-semibold text-foreground">❓ {qa.question}</h3>
                          {qa.timestamp && (
                            <span className="text-xs text-muted-foreground">
                              {new Date(qa.timestamp).toLocaleDateString("pt-PT", {
                                day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
                              })}
                            </span>
                          )}
                        </div>
                        <MarkdownContent content={qa.answer} />
                        {individual.length > 0 && (
                          <Accordion type="single" collapsible className="mt-3">
                            <AccordionItem value={`qa-ind-${i}`} className="border-0">
                              <AccordionTrigger className="py-1 hover:no-underline text-xs font-medium text-muted-foreground">
                                Ver respostas individuais ({individual.length})
                              </AccordionTrigger>
                              <AccordionContent>
                                <div className="space-y-2 mt-1">
                                  {individual.map((r, j) => (
                                    <div key={j} className="border border-border rounded-lg p-3">
                                      <p className="text-xs font-medium text-primary mb-1">{r.model}</p>
                                      <MarkdownContent content={r.response} />
                                    </div>
                                  ))}
                                </div>
                              </AccordionContent>
                            </AccordionItem>
                          </Accordion>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}

              <div className="flex gap-2">
                <Input
                  value={askQuestion}
                  onChange={(e) => setAskQuestion(e.target.value)}
                  placeholder="Escreva uma pergunta sobre esta análise..."
                  disabled={askLoading}
                  onKeyDown={(e) => e.key === "Enter" && !askLoading && askQuestion.trim() && handleAskQuestion()}
                  className="flex-1"
                />
                <Button
                  onClick={handleAskQuestion}
                  disabled={askLoading || !askQuestion.trim()}
                  size="sm"
                >
                  {askLoading ? (
                    <Loader2 className="w-4 h-4 animate-spin mr-2" />
                  ) : (
                    <Send className="w-4 h-4 mr-2" />
                  )}
                  {askLoading ? "A processar..." : "Enviar"}
                </Button>
              </div>
              {askLoading && (
                <p className="text-xs text-muted-foreground mt-2 animate-pulse">
                  ⏳ A consultar os juízes... isto pode demorar 30-60 segundos.
                </p>
              )}
              <div ref={qaEndRef} />
            </div>
          )}

          {/* Add Document Modal */}
          {document && (
            <AddDocumentModal
              open={showAddDocModal}
              onClose={() => setShowAddDocModal(false)}
              documentId={document.id}
              onSuccess={reloadDocument}
            />
          )}
        </div>
      </main>
    </div>
  );
};

export default DocumentDetails;
