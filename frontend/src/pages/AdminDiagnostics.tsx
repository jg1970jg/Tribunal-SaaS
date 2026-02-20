import { useState, useEffect, useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "@/integrations/supabase/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Scale, ArrowLeft, Lock, Loader2, ChevronDown, ChevronRight, ExternalLink, Copy } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";
import type { Tables } from "@/integrations/supabase/types";
import type { Json } from "@/integrations/supabase/types";

const BACKEND_URL = "https://tribunal-saas.onrender.com";

const ERROS_TECNICOS = [
  "INTEGRITY_WARNING",
  "EXCERPT_MISMATCH",
  "RANGE_INVALID",
  "PAGE_MISMATCH",
  "ITEM_NOT_FOUND",
  "MISSING_CITATION",
  "MISSING_RATIONALE",
  "SEM_PROVA_DETERMINANTE",
  "match_ratio=",
];

interface PhaseEntry {
  modelo?: string;
  role?: string;
  fase?: string;
  sucesso?: boolean;
  latencia_ms?: number;
  tokens_usados?: number;
  completion_tokens?: number;
  prompt_tokens?: number;
  conteudo?: string;
  erro?: string | null;
  conteudo_completo_length?: number;
}

interface CostEntry {
  model: string;
  phase: string;
  cost_usd: number;
  total_tokens?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  timestamp?: string;
}

interface AnalysisData {
  sucesso?: boolean;
  erro?: string | null;
  custos?: {
    wallet?: { custo_real?: number; custo_cliente?: number; markup?: number };
    por_fase?: Record<string, number>;
    detalhado?: CostEntry[];
    custo_total_usd?: number;
    custo_cliente_usd?: number;
    budget_total_usd?: number;
  };
  tempo_total_ms?: number;
  total_tokens?: number;
  total_latencia_ms?: number;
  fase1_extracoes?: PhaseEntry[];
  fase2_auditorias?: PhaseEntry[];
  fase3_pareceres?: PhaseEntry[];
  fase3_presidente?: string;
  fase1_agregado_consolidado?: string;
  fase2_chefe_consolidado?: string;
  veredicto_final?: string;
  simbolo_final?: string;
}

function extractErrors(text: string): { type: string; line: string }[] {
  const results: { type: string; line: string }[] = [];
  text.split("\n").forEach((line) => {
    for (const keyword of ERROS_TECNICOS) {
      if (line.includes(keyword)) {
        results.push({ type: keyword, line: line.trim() });
        break;
      }
    }
  });
  return results;
}

function getAllTextContent(analysis: AnalysisData): string {
  const parts: string[] = [];
  analysis.fase1_extracoes?.forEach((e) => e.conteudo && parts.push(e.conteudo));
  analysis.fase1_agregado_consolidado && parts.push(analysis.fase1_agregado_consolidado);
  analysis.fase2_auditorias?.forEach((e) => e.conteudo && parts.push(e.conteudo));
  analysis.fase2_chefe_consolidado && parts.push(analysis.fase2_chefe_consolidado);
  analysis.fase3_pareceres?.forEach((e) => e.conteudo && parts.push(e.conteudo));
  analysis.fase3_presidente && parts.push(analysis.fase3_presidente);
  analysis.veredicto_final && parts.push(analysis.veredicto_final);
  return parts.join("\n");
}

function getAllPhases(analysis: AnalysisData): (PhaseEntry & { phaseName: string })[] {
  const results: (PhaseEntry & { phaseName: string })[] = [];
  analysis.fase1_extracoes?.forEach((e) => results.push({ ...e, phaseName: "Fase 1 — Extração" }));
  analysis.fase2_auditorias?.forEach((e) => results.push({ ...e, phaseName: "Fase 2 — Auditoria" }));
  analysis.fase3_pareceres?.forEach((e) => results.push({ ...e, phaseName: "Fase 3 — Relatoria" }));
  return results;
}

interface Suggestion {
  icon: string;
  severity: "success" | "warning" | "critical";
  title: string;
  cause: string;
  fix: string;
}

function generateSuggestions(
  errorsByType: Record<string, { type: string; line: string }[]>,
  totalErrors: number,
  costDetails: CostEntry[],
  totalCost: number,
  allPhases: (PhaseEntry & { phaseName: string })[],
): Suggestion[] {
  const suggestions: Suggestion[] = [];

  if (totalErrors === 0) {
    suggestions.push({ icon: "✅", severity: "success", title: "Análise perfeita! Sem problemas detectados.", cause: "", fix: "" });
    return suggestions;
  }

  if (totalErrors > 50) {
    suggestions.push({
      icon: "🔴", severity: "critical",
      title: `CRÍTICO: ${totalErrors} erros detectados`,
      cause: "O pipeline pode ter um problema sistémico que afeta múltiplas fases.",
      fix: "Verificar se houve alterações recentes nos prompts ou no schema de validação. Rever logs do pipeline completo.",
    });
  }

  const excerptCount = errorsByType["EXCERPT_MISMATCH"]?.length ?? 0;
  if (excerptCount > 5) {
    suggestions.push({
      icon: "⚠️", severity: "warning",
      title: `Muitos excerpts não encontrados (${excerptCount})`,
      cause: "Thresholds de matching demasiado exigentes ou offsets incorrectos após chunking.",
      fix: "Ajustar threshold no integrity.py (considerar reduzir de 0.8 para 0.6).",
    });
  }

  const citationCount = errorsByType["MISSING_CITATION"]?.length ?? 0;
  if (citationCount > 3) {
    suggestions.push({
      icon: "⚠️", severity: "warning",
      title: `Findings sem citações (${citationCount})`,
      cause: "LLM não está a incluir citations no JSON de resposta.",
      fix: "Reforçar prompt dos auditores para exigir citations em todos os findings.",
    });
  }

  const rangeCount = errorsByType["RANGE_INVALID"]?.length ?? 0;
  if (rangeCount > 3) {
    suggestions.push({
      icon: "⚠️", severity: "warning",
      title: `Ranges de caracteres inválidos (${rangeCount})`,
      cause: "LLM gera start_char > end_char ou valores fora do range do documento.",
      fix: "Adicionar validação no schema_audit.py para corrigir ou descartar ranges inválidos.",
    });
  }

  // Check for inconclusive AI
  const inconclusiveAIs = allPhases.filter((p) => p.sucesso === false);
  if (inconclusiveAIs.length > 0) {
    suggestions.push({
      icon: "🔴", severity: "critical",
      title: `${inconclusiveAIs.length} IA(s) com resultado inconclusivo`,
      cause: "Erro de parsing de confidence ou resposta truncada pelo modelo.",
      fix: "Verificar _safe_confidence e aumentar max_tokens. IAs afetadas: " + inconclusiveAIs.map((a) => a.role || a.modelo).join(", "),
    });
  }

  // Check expensive AIs (aggregate by model)
  const costByModel: Record<string, number> = {};
  costDetails.forEach((c) => { costByModel[c.model] = (costByModel[c.model] || 0) + c.cost_usd; });
  Object.entries(costByModel).forEach(([model, cost]) => {
    const pct = totalCost > 0 ? (cost / totalCost) * 100 : 0;
    if (pct > 30) {
      suggestions.push({
        icon: "💰", severity: "warning",
        title: `${model} consumiu ${pct.toFixed(0)}% do budget ($${cost.toFixed(2)})`,
        cause: "Modelo caro a ser usado em fase que poderia usar alternativa mais barata.",
        fix: `Considerar substituir ${model} por um modelo mais económico nesta fase.`,
      });
    }
  });

  // Check slow AIs (using timestamps to estimate duration per call)
  costDetails.forEach((c) => {
    // We don't have duration per call, but we can flag if there are very few calls with high cost
    // Skip this rule if no timestamp data
  });

  return suggestions;
}

const AdminLogin = ({ onAuth }: { onAuth: () => void }) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const checkAdmin = async () => {
      setLoading(true);
      setError("");
      try {
        const { data: { session } } = await supabase.auth.getSession();
        if (!session) {
          setError("Precisa estar autenticado");
          setLoading(false);
          return;
        }
        const res = await fetch(
          `${import.meta.env.VITE_SUPABASE_URL}/functions/v1/check-admin`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Authorization: `Bearer ${session.access_token}`,
            },
            body: JSON.stringify({}),
          }
        );
        if (res.ok) {
          onAuth();
        } else {
          const data = await res.json().catch(() => ({}));
          setError(data.error === "Not an admin" ? "Sem permissões de administrador" : "Erro de verificação");
        }
      } catch {
        setError("Erro ao contactar o servidor");
      }
      setLoading(false);
    };
    checkAdmin();
  }, [onAuth]);

  return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <div className="bg-card rounded-xl border border-border p-8 w-full max-w-sm space-y-4 text-center">
        <div className="flex items-center justify-center gap-3 mb-2">
          <Lock className="w-5 h-5 text-muted-foreground" />
          <h1 className="text-lg font-semibold text-foreground">Acesso Administrador</h1>
        </div>
        {loading ? (
          <div className="flex justify-center py-4">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            <span className="ml-2 text-muted-foreground">A verificar permissões...</span>
          </div>
        ) : error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : null}
      </div>
    </div>
  );
};

// --- Collapsible Section ---
const Section = ({ title, icon, defaultOpen = false, children, badge }: {
  title: string; icon: string; defaultOpen?: boolean; children: React.ReactNode; badge?: React.ReactNode;
}) => {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="bg-card rounded-xl border border-border mb-4">
      <button onClick={() => setOpen(!open)} className="w-full flex items-center justify-between px-6 py-4 text-left">
        <div className="flex items-center gap-3">
          <span className="text-lg">{icon}</span>
          <span className="font-semibold text-foreground">{title}</span>
          {badge}
        </div>
        {open ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
      </button>
      {open && <div className="px-6 pb-6">{children}</div>}
    </div>
  );
};

// --- Main Diagnostics Page ---
const AdminDiagnostics = () => {
  const navigate = useNavigate();
  const { toast } = useToast();
  const [authed, setAuthed] = useState(false);
  const [documents, setDocuments] = useState<Tables<"documents">[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedDocId, setSelectedDocId] = useState<string | null>(null);

  const fetchAllDocs = useCallback(async () => {
    setLoading(true);
    // Admin reads all documents - need service role or backend endpoint
    // For now, read user's own documents (admin is also a user)
    const { data } = await supabase
      .from("documents")
      .select("*")
      .order("created_at", { ascending: false });
    setDocuments(data || []);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (authed) fetchAllDocs();
  }, [authed, fetchAllDocs]);

  const analyzedDocs = useMemo(() => documents.filter(
    (d) => d.analysis_result && typeof d.analysis_result === "object" && !Array.isArray(d.analysis_result)
  ), [documents]);

  const selectedDoc = selectedDocId ? analyzedDocs.find((d) => d.id === selectedDocId) : null;
  const analysis = selectedDoc ? (selectedDoc.analysis_result as unknown as AnalysisData) : null;

  const allText = analysis ? getAllTextContent(analysis) : "";
  const allErrors = analysis ? extractErrors(allText) : [];
  const allPhases = analysis ? getAllPhases(analysis) : [];
  const costDetails = analysis?.custos?.detalhado || [];
  const totalCost = selectedDoc?.custo_cobrado_usd ?? analysis?.custos?.custo_cliente_usd ?? analysis?.custos?.custo_total_usd ?? costDetails.reduce((s, c) => s + c.cost_usd, 0);

  const errorsByType: Record<string, { type: string; line: string }[]> = {};
  allErrors.forEach((e) => {
    if (!errorsByType[e.type]) errorsByType[e.type] = [];
    errorsByType[e.type].push(e);
  });

  const suggestions = useMemo(() =>
    analysis ? generateSuggestions(errorsByType, allErrors.length, costDetails, totalCost, allPhases) : [],
    [analysis, errorsByType, allErrors.length, costDetails, totalCost, allPhases]
  );

  const historyChartData = useMemo(() => {
    return [...analyzedDocs].reverse().map((doc) => {
      const a = doc.analysis_result as unknown as AnalysisData;
      const text = getAllTextContent(a);
      const errs = extractErrors(text).length;
      const cost = doc.custo_cobrado_usd ?? a?.custos?.custo_cliente_usd ?? 0;
      return {
        label: new Date(doc.created_at).toLocaleDateString("pt-PT", { day: "numeric", month: "short" }),
        erros: errs,
        custo: cost,
      };
    });
  }, [analyzedDocs]);

  const docTrends = useMemo(() => {
    const trends: Record<string, string> = {};
    const sorted = [...analyzedDocs].reverse();
    sorted.forEach((doc, idx) => {
      if (idx === 0) { trends[doc.id] = "—"; return; }
      const prevA = sorted[idx - 1].analysis_result as unknown as AnalysisData;
      const currA = doc.analysis_result as unknown as AnalysisData;
      const prevErrs = extractErrors(getAllTextContent(prevA)).length;
      const currErrs = extractErrors(getAllTextContent(currA)).length;
      if (currErrs > prevErrs) trends[doc.id] = "↑";
      else if (currErrs < prevErrs) trends[doc.id] = "↓";
      else trends[doc.id] = "=";
    });
    return trends;
  }, [analyzedDocs]);

  if (!authed) return <AdminLogin onAuth={() => setAuthed(true)} />;

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card/80 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg gradient-navy flex items-center justify-center">
              <Scale className="w-5 h-5 text-primary-foreground" />
            </div>
            <span className="text-lg font-semibold text-foreground tracking-tight">Diagnóstico Técnico</span>
          </div>
          <Button variant="ghost" size="sm" onClick={() => setAuthed(false)}>
            Sair
          </Button>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Button variant="ghost" onClick={() => navigate("/dashboard")} className="mb-6 text-muted-foreground">
          <ArrowLeft className="w-4 h-4 mr-2" />
          Voltar
        </Button>

        {loading ? (
          <div className="flex items-center justify-center py-16">
            <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
          </div>
        ) : (
          <>
            {/* Document selector */}
            <Section title="Histórico de Análises" icon="📋" defaultOpen={!selectedDocId}>
              <div className="rounded-lg border border-border overflow-hidden">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="bg-muted/50">
                      <th className="text-left px-3 py-2 font-medium text-muted-foreground">Data</th>
                      <th className="text-left px-3 py-2 font-medium text-muted-foreground">Documento</th>
                      <th className="text-left px-3 py-2 font-medium text-muted-foreground">Custo</th>
                      <th className="text-left px-3 py-2 font-medium text-muted-foreground">Erros</th>
                      <th className="text-left px-3 py-2 font-medium text-muted-foreground">Tendência</th>
                      <th className="text-left px-3 py-2 font-medium text-muted-foreground">Status</th>
                      <th className="px-3 py-2"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {analyzedDocs.map((doc) => {
                      const a = doc.analysis_result as unknown as AnalysisData;
                      const text = getAllTextContent(a);
                      const errs = extractErrors(text);
                      const cost = doc.custo_cobrado_usd ?? a?.custos?.custo_cliente_usd ?? 0;
                      const success = a?.sucesso !== false;
                      const trend = docTrends[doc.id] || "—";
                      return (
                        <tr
                          key={doc.id}
                          className={`border-t border-border cursor-pointer hover:bg-muted/30 transition-colors ${selectedDocId === doc.id ? "bg-primary/5" : ""}`}
                          onClick={() => setSelectedDocId(doc.id)}
                        >
                          <td className="px-3 py-2.5 text-muted-foreground">
                            {new Date(doc.created_at).toLocaleDateString("pt-PT", { day: "numeric", month: "short", year: "numeric" })}
                          </td>
                          <td className="px-3 py-2.5 font-medium text-foreground truncate max-w-[200px]">{doc.title}</td>
                          <td className="px-3 py-2.5 text-muted-foreground">${cost.toFixed(2)}</td>
                          <td className="px-3 py-2.5">
                            {errs.length > 0 ? (
                              <span className="text-warning font-medium">{errs.length}</span>
                            ) : (
                              <span className="text-success">0</span>
                            )}
                          </td>
                          <td className="px-3 py-2.5">
                            <span className={`font-medium ${trend === "↑" ? "text-destructive" : trend === "↓" ? "text-success" : "text-muted-foreground"}`}>
                              {trend}
                            </span>
                          </td>
                          <td className="px-3 py-2.5">
                            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${success ? "text-success bg-success/10" : "text-destructive bg-destructive/10"}`}>
                              {success ? "✅ OK" : "❌ Falhou"}
                            </span>
                          </td>
                          <td className="px-3 py-2.5">
                            <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); navigate(`/document/${doc.id}`); }}>
                              <ExternalLink className="w-3.5 h-3.5" />
                            </Button>
                          </td>
                        </tr>
                      );
                    })}
                    {analyzedDocs.length === 0 && (
                      <tr><td colSpan={7} className="px-3 py-8 text-center text-muted-foreground">Nenhuma análise encontrada.</td></tr>
                    )}
                  </tbody>
                </table>
              </div>

              {/* Evolution Charts */}
              {historyChartData.length > 1 && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mt-4">
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-2">Evolução de Erros</p>
                    <div className="h-[180px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={historyChartData}>
                          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                          <XAxis dataKey="label" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} />
                          <YAxis tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} allowDecimals={false} />
                          <Tooltip
                            contentStyle={{ backgroundColor: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: "8px", fontSize: "11px" }}
                          />
                          <Bar dataKey="erros" fill="hsl(var(--warning))" radius={[3, 3, 0, 0]} name="Erros" />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-2">Evolução de Custo</p>
                    <div className="h-[180px]">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={historyChartData}>
                          <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                          <XAxis dataKey="label" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} />
                          <YAxis tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} tickFormatter={(v) => `$${v}`} />
                          <Tooltip
                            contentStyle={{ backgroundColor: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: "8px", fontSize: "11px" }}
                            formatter={(value: number) => `$${value.toFixed(2)}`}
                          />
                          <Line type="monotone" dataKey="custo" stroke="hsl(var(--accent))" strokeWidth={2} dot={{ r: 3 }} name="Custo ($)" />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                </div>
              )}
            </Section>

            {/* Selected analysis details */}
            {analysis && selectedDoc && (
              <>
                {/* Section 1: Summary */}
                <Section title={`Resumo — ${selectedDoc.title}`} icon="📊" defaultOpen>
                  <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
                    <div className="bg-muted/30 rounded-lg p-3">
                      <p className="text-xs text-muted-foreground mb-1">Status</p>
                      <p className={`text-sm font-semibold ${analysis.sucesso !== false ? "text-success" : "text-destructive"}`}>
                        {analysis.sucesso !== false ? "✅ Sucesso" : "❌ Falhou"}
                      </p>
                    </div>
                    <div className="bg-muted/30 rounded-lg p-3">
                      <p className="text-xs text-muted-foreground mb-1">Custo Total</p>
                      <p className="text-sm font-semibold text-foreground">${totalCost.toFixed(2)}</p>
                    </div>
                    <div className="bg-muted/30 rounded-lg p-3">
                      <p className="text-xs text-muted-foreground mb-1">Tempo Total</p>
                      <p className="text-sm font-semibold text-foreground">
                        {analysis.tempo_total_ms
                          ? `${Math.floor(analysis.tempo_total_ms / 60000)}m ${Math.floor((analysis.tempo_total_ms % 60000) / 1000)}s`
                          : analysis.total_latencia_ms
                            ? `${(analysis.total_latencia_ms / 1000).toFixed(0)}s`
                            : "—"}
                      </p>
                    </div>
                    <div className="bg-muted/30 rounded-lg p-3">
                      <p className="text-xs text-muted-foreground mb-1">Total Erros</p>
                      <p className={`text-sm font-semibold ${allErrors.length > 0 ? "text-warning" : "text-success"}`}>
                        {allErrors.length}
                      </p>
                    </div>
                    <div className="bg-muted/30 rounded-lg p-3">
                      <p className="text-xs text-muted-foreground mb-1">IAs Usadas</p>
                      <p className="text-sm font-semibold text-foreground">
                        {new Set(costDetails.map((c) => c.model)).size || allPhases.length}
                      </p>
                    </div>
                  </div>

                  {/* Cost per phase */}
                  {analysis.custos?.por_fase && (
                    <div className="mt-4">
                      <p className="text-xs font-medium text-muted-foreground mb-2">Custo por Fase</p>
                      <div className="flex gap-2 flex-wrap">
                        {Object.entries(analysis.custos.por_fase).map(([fase, cost]) => (
                          <span key={fase} className="bg-muted/50 rounded px-2.5 py-1 text-xs text-foreground">
                            {fase}: <strong>${cost.toFixed(4)}</strong>
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </Section>

                {/* Section 2: AI Performance Table */}
                <Section title="Performance por IA" icon="🤖"
                  badge={<span className="text-xs text-muted-foreground ml-2">({costDetails.length} chamadas)</span>}
                >
                  <div className="rounded-lg border border-border overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr className="bg-muted/50">
                          <th className="text-left px-3 py-2 font-medium text-muted-foreground">Modelo</th>
                          <th className="text-left px-3 py-2 font-medium text-muted-foreground">Fase</th>
                          <th className="text-right px-3 py-2 font-medium text-muted-foreground">Tokens</th>
                          <th className="text-right px-3 py-2 font-medium text-muted-foreground">Custo</th>
                          <th className="text-left px-3 py-2 font-medium text-muted-foreground">Timestamp</th>
                        </tr>
                      </thead>
                      <tbody>
                        {costDetails.map((c, i) => {
                          const costPct = totalCost > 0 ? (c.cost_usd / totalCost) * 100 : 0;
                          const isExpensive = costPct > 30;
                          return (
                            <tr key={i} className={`border-t border-border ${isExpensive ? "bg-warning/5" : ""}`}>
                              <td className="px-3 py-2 font-mono text-foreground">{c.model}</td>
                              <td className="px-3 py-2 text-muted-foreground">{c.phase}</td>
                              <td className="px-3 py-2 text-right text-muted-foreground">{c.total_tokens?.toLocaleString("pt-PT") || "—"}</td>
                              <td className="px-3 py-2 text-right font-medium text-foreground">
                                ${c.cost_usd.toFixed(4)}
                                {isExpensive && <span className="ml-1 text-warning">⚠️</span>}
                              </td>
                              <td className="px-3 py-2 text-muted-foreground">
                                {c.timestamp ? new Date(c.timestamp).toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "—"}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>

                  {/* Phase-level status from actual phase entries */}
                  {allPhases.length > 0 && (
                    <div className="mt-4">
                      <p className="text-xs font-medium text-muted-foreground mb-2">Status por IA (fases)</p>
                      <div className="rounded-lg border border-border overflow-x-auto">
                        <table className="w-full text-xs">
                          <thead>
                            <tr className="bg-muted/50">
                              <th className="text-left px-3 py-2 font-medium text-muted-foreground">IA</th>
                              <th className="text-left px-3 py-2 font-medium text-muted-foreground">Modelo</th>
                              <th className="text-left px-3 py-2 font-medium text-muted-foreground">Fase</th>
                              <th className="text-right px-3 py-2 font-medium text-muted-foreground">Tokens</th>
                              <th className="text-left px-3 py-2 font-medium text-muted-foreground">Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {allPhases.map((p, i) => {
                              const hasOutput = (p.tokens_usados ?? 0) > 0 && !!p.conteudo;
                              const explicitFail = p.sucesso === false && (!hasOutput);
                              const hasErrorStatus = p.erro === "failed" || p.erro === "error" || p.erro === "timeout";
                              const isFailed = hasErrorStatus || (!hasOutput && p.sucesso === false);
                              const phaseErrors = p.conteudo ? extractErrors(p.conteudo) : [];
                              const hasIntegrityErrors = phaseErrors.length > 0;

                              let status: "ok" | "warning" | "failed";
                              if (isFailed || (!hasOutput && !p.conteudo)) {
                                status = "failed";
                              } else if (hasIntegrityErrors) {
                                status = "warning";
                              } else {
                                status = "ok";
                              }

                              const statusConfig = {
                                ok: { label: "✅ OK", className: "text-success bg-success/10", rowClass: "" },
                                warning: { label: "⚠️ Com erros", className: "text-warning bg-warning/10", rowClass: "bg-warning/5" },
                                failed: { label: "❌ Falhou", className: "text-destructive bg-destructive/10", rowClass: "bg-destructive/5" },
                              };
                              const cfg = statusConfig[status];

                              return (
                                <tr key={i} className={`border-t border-border ${cfg.rowClass}`}>
                                  <td className="px-3 py-2 text-foreground">{p.role || "—"}</td>
                                  <td className="px-3 py-2 font-mono text-muted-foreground">{p.modelo || "—"}</td>
                                  <td className="px-3 py-2 text-muted-foreground">{p.phaseName}</td>
                                  <td className="px-3 py-2 text-right text-muted-foreground">{p.tokens_usados?.toLocaleString("pt-PT") || "—"}</td>
                                  <td className="px-3 py-2">
                                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${cfg.className}`}>
                                      {cfg.label}
                                    </span>
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </Section>

                {/* Section 3: All Errors Grouped */}
                <Section title="Erros Técnicos" icon="🐛"
                  badge={
                    <span className={`text-xs ml-2 px-2 py-0.5 rounded-full font-medium ${
                      allErrors.length > 0 ? "text-warning bg-warning/10" : "text-success bg-success/10"
                    }`}>
                      {allErrors.length} {allErrors.length === 1 ? "erro" : "erros"}
                    </span>
                  }
                >
                  {allErrors.length === 0 ? (
                    <p className="text-sm text-success">✅ Nenhum erro técnico detetado nesta análise.</p>
                  ) : (
                    <div className="space-y-4">
                      {Object.entries(errorsByType)
                        .sort(([, a], [, b]) => b.length - a.length)
                        .map(([type, errors]) => (
                          <div key={type}>
                            <div className="flex items-center gap-2 mb-2">
                              <span className="text-xs font-mono font-bold text-warning">{type}</span>
                              <span className="text-xs text-muted-foreground">({errors.length} ocorrência{errors.length !== 1 ? "s" : ""})</span>
                            </div>
                            <div className="space-y-1 max-h-[200px] overflow-y-auto">
                              {errors.map((e, i) => (
                                <div key={i} className="bg-warning/5 rounded px-3 py-1.5 text-xs font-mono text-muted-foreground border-l-2 border-warning/30 break-all">
                                  {e.line}
                                </div>
                              ))}
                            </div>
                          </div>
                        ))}
                    </div>
                  )}
                </Section>

                {/* Section 4: Auto Fix Suggestions */}
                <Section title="Sugestões de Fix" icon="💡"
                  badge={<span className="text-xs text-muted-foreground ml-2">({suggestions.length})</span>}
                  defaultOpen
                >
                  {suggestions.length === 0 ? (
                    <p className="text-sm text-muted-foreground">Selecione uma análise para ver sugestões.</p>
                  ) : (
                    <div className="space-y-3">
                      {suggestions.map((s, i) => {
                        const borderColor = s.severity === "critical" ? "border-destructive/50" : s.severity === "warning" ? "border-warning/50" : "border-success/50";
                        const bgColor = s.severity === "critical" ? "bg-destructive/5" : s.severity === "warning" ? "bg-warning/5" : "bg-success/5";
                        const fullText = `${s.icon} ${s.title}${s.cause ? `\nCausa: ${s.cause}` : ""}${s.fix ? `\nFix: ${s.fix}` : ""}`;

                        return (
                          <div key={i} className={`rounded-lg border-l-4 ${borderColor} ${bgColor} p-4`}>
                            <div className="flex items-start justify-between gap-3">
                              <div className="flex-1 min-w-0">
                                <p className="text-sm font-medium text-foreground mb-1">
                                  {s.icon} {s.title}
                                </p>
                                {s.cause && (
                                  <p className="text-xs text-muted-foreground mb-1">
                                    <strong>Causa:</strong> {s.cause}
                                  </p>
                                )}
                                {s.fix && (
                                  <p className="text-xs text-muted-foreground">
                                    <strong>Fix:</strong> {s.fix}
                                  </p>
                                )}
                              </div>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="shrink-0 text-muted-foreground"
                                onClick={() => {
                                  navigator.clipboard.writeText(fullText);
                                  toast({ title: "Copiado para o clipboard" });
                                }}
                              >
                                <Copy className="w-3.5 h-3.5 mr-1" />
                                <span className="text-xs">Copiar</span>
                              </Button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </Section>
              </>
            )}
          </>
        )}
      </main>
    </div>
  );
};

export default AdminDiagnostics;
