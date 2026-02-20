import { FileText, Clock, CheckCircle, AlertTriangle, Loader2 } from "lucide-react";
import type { Tables } from "@/integrations/supabase/types";
import type { Json } from "@/integrations/supabase/types";

type Document = Tables<"documents">;

const statusConfig = {
  pending: { label: "Pendente", icon: Clock, className: "text-warning bg-warning/10" },
  analyzing: { label: "A analisar", icon: Loader2, className: "text-accent bg-accent/10" },
  completed: { label: "Concluído", icon: CheckCircle, className: "text-success bg-success/10" },
  error: { label: "Erro", icon: AlertTriangle, className: "text-destructive bg-destructive/10" },
};

const riskConfig = {
  low: { label: "Baixo", className: "text-success bg-success/10" },
  medium: { label: "Médio", className: "text-warning bg-warning/10" },
  high: { label: "Alto", className: "text-destructive bg-destructive/10" },
};

interface DocumentCardProps {
  document: Document;
  onClick: () => void;
}

function getAnalysisField(result: Json | null, field: string): string | number | null {
  if (!result || typeof result !== "object" || Array.isArray(result)) return null;
  const val = (result as Record<string, Json | undefined>)[field];
  if (val === undefined || val === null) return null;
  if (typeof val === "string" || typeof val === "number") return val;
  return null;
}

function getInputType(fileName: string): { label: string; icon: string } {
  const ext = fileName.split(".").pop()?.toLowerCase() || "";
  if (ext === "txt") return { label: "Texto livre", icon: "📝" };
  if (ext === "pdf") return { label: "PDF", icon: "📎" };
  if (ext === "docx") return { label: "DOCX", icon: "📎" };
  if (ext === "xlsx") return { label: "XLSX", icon: "📎" };
  return { label: ext.toUpperCase() || "Ficheiro", icon: "📎" };
}

export const DocumentCard = ({ document, onClick }: DocumentCardProps) => {
  const status = statusConfig[document.status as keyof typeof statusConfig] || statusConfig.pending;
  const StatusIcon = status.icon;
  const risk = document.risk_level ? riskConfig[document.risk_level as keyof typeof riskConfig] : null;

  const ar = document.analysis_result;
  const simbolo = getAnalysisField(ar, "simbolo_final") as string | null;
  const areaDireito = getAnalysisField(ar, "area_direito") as string | null;
  const totalTokens = getAnalysisField(ar, "total_tokens") as number | null;
  const statusFinal = getAnalysisField(ar, "status_final") as string | null;
  const inputType = getInputType(document.file_name);

  const verdictLabel = statusFinal
    ? statusFinal === "aprovado" ? "Aprovado"
      : statusFinal === "atencao" ? "Atenção"
      : statusFinal === "rejeitado" ? "Rejeitado"
      : statusFinal
    : null;

  const formattedDate = new Date(document.created_at).toLocaleDateString("pt-PT", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

  return (
    <button
      onClick={onClick}
      className="w-full text-left bg-card rounded-xl border border-border p-5 hover:border-accent/30 hover:shadow-md transition-all duration-200 group animate-fade-in"
    >
      <div className="flex items-start gap-4">
        <div className="w-10 h-10 rounded-lg bg-muted flex items-center justify-center shrink-0 group-hover:bg-accent/10 transition-colors">
          {simbolo ? (
            <span className="text-lg leading-none">{simbolo}</span>
          ) : (
            <FileText className="w-5 h-5 text-muted-foreground group-hover:text-accent transition-colors" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <h3 className="font-medium text-foreground truncate">{document.title}</h3>
            <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium shrink-0 ${status.className}`}>
              <StatusIcon className="w-3 h-3" />
              {status.label}
            </span>
            {risk && (
              <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium shrink-0 ${risk.className}`}>
                Risco {risk.label}
              </span>
            )}
            {areaDireito && (
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium shrink-0 text-accent bg-accent/10">
                {areaDireito}
              </span>
            )}
          </div>

          <p className="text-sm text-muted-foreground truncate">{document.file_name}</p>

          <div className="flex items-center gap-4 mt-2 flex-wrap">
            <p className="text-xs text-muted-foreground">{formattedDate}</p>
            <span className="text-xs text-muted-foreground">
              {inputType.icon} {inputType.label}
            </span>
            {verdictLabel && (
              <p className="text-xs font-medium text-muted-foreground">
                {simbolo} {verdictLabel}
              </p>
            )}
            {totalTokens != null && (
              <p className="text-xs text-muted-foreground">
                {totalTokens.toLocaleString("pt-PT")} tokens
              </p>
            )}
          </div>
        </div>
      </div>
    </button>
  );
};
