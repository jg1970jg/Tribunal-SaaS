import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "@/integrations/supabase/client";
import { Button } from "@/components/ui/button";
import { Scale, Plus, LogOut, FileText, Clock, AlertTriangle, CheckCircle, Search, Settings, ShieldCheck, Loader2 } from "lucide-react";
import { Input } from "@/components/ui/input";
import { UploadModal } from "@/components/UploadModal";
import { DocumentCard } from "@/components/DocumentCard";
import { WalletIndicator } from "@/components/WalletIndicator";
import { useWallet } from "@/contexts/WalletContext";
import { useToast } from "@/hooks/use-toast";
import type { Tables } from "@/integrations/supabase/types";
import type { Json } from "@/integrations/supabase/types";

const BACKEND_URL = "https://tribunal-saas.onrender.com";

type Document = Tables<"documents">;

function getAnalysisField(result: Json | null, field: string): string | number | null {
  if (!result || typeof result !== "object" || Array.isArray(result)) return null;
  const val = (result as Record<string, Json | undefined>)[field];
  if (val === undefined || val === null) return null;
  if (typeof val === "string" || typeof val === "number") return val;
  return null;
}

type VerdictFilter = "all" | "aprovado" | "rejeitado" | "atencao";

const Dashboard = () => {
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [showUpload, setShowUpload] = useState(false);
  const [search, setSearch] = useState("");
  const [userName, setUserName] = useState("");
  const [verdictFilter, setVerdictFilter] = useState<VerdictFilter>("all");
  const [isAdmin, setIsAdmin] = useState(false);
  const [resumingDocId, setResumingDocId] = useState<string | null>(null);
  const navigate = useNavigate();
  const { balance } = useWallet();
  const { toast } = useToast();

  useEffect(() => {
    const checkAuth = async () => {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        navigate("/auth");
        return;
      }
      setUserName(session.user.user_metadata?.full_name || session.user.email || "");
      fetchDocuments();

      // Check admin role
      const { data: roleData } = await supabase
        .from("user_roles")
        .select("role")
        .eq("user_id", session.user.id)
        .eq("role", "admin")
        .maybeSingle();
      setIsAdmin(!!roleData);
    };
    checkAuth();
  }, [navigate]);

  const fetchDocuments = async () => {
    setLoading(false);
    const { data } = await supabase
      .from("documents")
      .select("*")
      .order("created_at", { ascending: false });
    if (data) setDocuments(data);
  };

  const handleLogout = async () => {
    await supabase.auth.signOut();
    navigate("/auth");
  };

  const handleResume = async (documentId: string) => {
    setResumingDocId(documentId);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        navigate("/auth");
        return;
      }

      const response = await fetch(`${BACKEND_URL}/analyze/resume/${documentId}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
        },
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: "Erro desconhecido" }));
        throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
      }

      toast({
        title: "Analise retomada",
        description: "A analise foi concluida com sucesso.",
      });

      // Refresh e navegar para o documento
      await fetchDocuments();
      navigate(`/document/${documentId}`);
    } catch (err: any) {
      toast({
        title: "Erro ao retomar",
        description: err.message || "Nao foi possivel retomar a analise.",
        variant: "destructive",
      });
    } finally {
      setResumingDocId(null);
    }
  };

  const handleAbandon = async (documentId: string) => {
    if (!confirm("Tem a certeza que deseja abandonar esta analise? Os creditos bloqueados serao devolvidos.")) {
      return;
    }

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) {
        navigate("/auth");
        return;
      }

      const response = await fetch(`${BACKEND_URL}/analyze/abandon/${documentId}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${session.access_token}`,
        },
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: "Erro desconhecido" }));
        throw new Error(typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail));
      }

      toast({
        title: "Analise abandonada",
        description: "Os creditos bloqueados foram devolvidos.",
      });

      await fetchDocuments();
    } catch (err: any) {
      toast({
        title: "Erro ao abandonar",
        description: err.message || "Nao foi possivel abandonar a analise.",
        variant: "destructive",
      });
    }
  };

  const filteredDocs = documents.filter((d) => {
    const matchesSearch =
      d.title.toLowerCase().includes(search.toLowerCase()) ||
      d.file_name.toLowerCase().includes(search.toLowerCase());
    if (!matchesSearch) return false;
    if (verdictFilter === "all") return true;
    const statusFinal = getAnalysisField(d.analysis_result, "status_final") as string | null;
    return statusFinal === verdictFilter;
  });

  const interruptedCount = documents.filter((d) => d.status === "interrupted").length;

  const stats = {
    total: documents.length,
    completed: documents.filter((d) => d.status === "completed").length,
    pending: documents.filter((d) => d.status === "pending" || d.status === "analyzing").length,
    highRisk: documents.filter((d) => d.risk_level === "high").length,
  };

  const verdictOptions: { value: VerdictFilter; label: string }[] = [
    { value: "all", label: "Todos" },
    { value: "aprovado", label: "Procedente ✅" },
    { value: "rejeitado", label: "Improcedente ❌" },
    { value: "atencao", label: "Inconclusivo ⚠️" },
  ];

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b border-border bg-card/80 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg gradient-navy flex items-center justify-center">
              <Scale className="w-5 h-5 text-primary-foreground" />
            </div>
            <span className="text-lg font-semibold text-foreground tracking-tight">LexForum</span>
          </div>
          <div className="flex items-center gap-2">
            <WalletIndicator />
            <span className="text-sm text-muted-foreground hidden sm:block">{userName}</span>
            {isAdmin && (
              <>
                <Button variant="ghost" size="sm" onClick={() => navigate("/admin")} className="text-muted-foreground" title="Administração">
                  <ShieldCheck className="w-4 h-4" />
                </Button>
                <Button variant="ghost" size="sm" onClick={() => navigate("/admin/diagnostics")} className="text-muted-foreground" title="Diagnóstico Técnico">
                  🔧
                </Button>
              </>
            )}
            <Button variant="ghost" size="sm" onClick={() => navigate("/settings")} className="text-muted-foreground">
              <Settings className="w-4 h-4" />
            </Button>
            <Button variant="ghost" size="sm" onClick={handleLogout} className="text-muted-foreground">
              <LogOut className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        {/* Welcome & CTA */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8 animate-fade-in">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">Dashboard</h1>
            <p className="text-muted-foreground mt-1">Gerencie e analise os seus documentos jurídicos.</p>
          </div>
          <Button onClick={() => setShowUpload(true)} className="h-11 px-6 font-medium shadow-lg shadow-primary/20">
            <Plus className="w-4 h-4 mr-2" />
            Nova Análise
          </Button>
        </div>

        {/* Interrupted banner */}
        {interruptedCount > 0 && (
          <div className="mb-6 p-4 rounded-xl border border-orange-200 bg-orange-50 dark:border-orange-800 dark:bg-orange-900/20 animate-fade-in">
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 text-orange-600 dark:text-orange-400 shrink-0" />
              <p className="text-sm font-medium text-orange-800 dark:text-orange-300">
                {interruptedCount} analise(s) interrompida(s) — pode retomar ou abandonar abaixo.
              </p>
            </div>
          </div>
        )}

        {/* Stats */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          {[
            { label: "Total", value: stats.total, icon: FileText, color: "text-foreground" },
            { label: "Concluídos", value: stats.completed, icon: CheckCircle, color: "text-success" },
            { label: "Em análise", value: stats.pending, icon: Clock, color: "text-warning" },
            { label: "Risco alto", value: stats.highRisk, icon: AlertTriangle, color: "text-destructive" },
          ].map((stat) => (
            <div key={stat.label} className="bg-card rounded-xl border border-border p-5 animate-fade-in">
              <div className="flex items-center justify-between mb-3">
                <stat.icon className={`w-5 h-5 ${stat.color}`} />
              </div>
              <p className="text-2xl font-semibold text-foreground">{stat.value}</p>
              <p className="text-sm text-muted-foreground mt-1">{stat.label}</p>
            </div>
          ))}
        </div>

        {/* Search + Filter */}
        <div className="flex flex-col sm:flex-row gap-3 mb-6">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input
              placeholder="Pesquisar documentos..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-10 h-11 bg-card border-border"
            />
          </div>
          <div className="flex gap-1.5">
            {verdictOptions.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setVerdictFilter(opt.value)}
                className={`px-3 py-2 rounded-lg text-xs font-medium transition-colors whitespace-nowrap ${
                  verdictFilter === opt.value
                    ? "bg-primary text-primary-foreground"
                    : "bg-card border border-border text-muted-foreground hover:text-foreground"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* Resuming overlay */}
        {resumingDocId && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
            <div className="bg-card rounded-xl border border-border p-8 text-center shadow-xl max-w-sm">
              <Loader2 className="w-10 h-10 text-accent mx-auto mb-4 animate-spin" />
              <h3 className="text-lg font-semibold text-foreground mb-2">A retomar analise...</h3>
              <p className="text-sm text-muted-foreground">
                A retomar a partir da ultima fase completa. Isto pode demorar alguns minutos.
              </p>
            </div>
          </div>
        )}

        {/* Documents */}
        {loading ? (
          <div className="flex justify-center py-20">
            <div className="w-8 h-8 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
          </div>
        ) : filteredDocs.length === 0 ? (
          <div className="text-center py-20 animate-fade-in">
            <FileText className="w-12 h-12 text-muted-foreground/40 mx-auto mb-4" />
            <h3 className="text-lg font-medium text-foreground mb-2">Sem documentos</h3>
            <p className="text-muted-foreground mb-6">
              Comece por carregar o seu primeiro documento para análise.
            </p>
            <Button onClick={() => setShowUpload(true)} variant="outline">
              <Plus className="w-4 h-4 mr-2" />
              Carregar documento
            </Button>
          </div>
        ) : (
          <div className="grid gap-3">
            {filteredDocs.map((doc) => (
              <DocumentCard
                key={doc.id}
                document={doc}
                onClick={() => navigate(`/document/${doc.id}`)}
                onResume={doc.status === "interrupted" ? () => handleResume(doc.id) : undefined}
                onAbandon={doc.status === "interrupted" ? () => handleAbandon(doc.id) : undefined}
              />
            ))}
          </div>
        )}
      </main>

      <UploadModal open={showUpload} onClose={() => setShowUpload(false)} onSuccess={fetchDocuments} />
    </div>
  );
};

export default Dashboard;
