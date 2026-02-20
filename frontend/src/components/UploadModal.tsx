import { useState, useRef, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { supabase } from "@/integrations/supabase/client";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Upload, FileText, X, Loader2, Type } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { useWallet } from "@/contexts/WalletContext";
import { TierSelectStep, type TierLevel } from "./TierSelectStep";

const BACKEND_URL = "https://tribunal-saas.onrender.com";
const MAX_FILES = 10;
const MIN_TEXT_LENGTH = 50;
const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".xlsx", ".txt"];
const ACCEPTED_MIME = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/plain",
];

interface UploadModalProps {
  open: boolean;
  onClose: () => void;
  onSuccess: () => void;
}

const PHASES = [
  { threshold: 0, label: "Extração (7 modelos de IA a analisar o documento...)" },
  { threshold: 120, label: "Verificação (4 verificadores a cruzar dados...)" },
  { threshold: 420, label: "Análise (3 analistas a avaliar o documento...)" },
  { threshold: 900, label: "Consolidação (a cruzar conclusões...)" },
  { threshold: 1200, label: "Redação do Relatório (curadoria profissional...)" },
  { threshold: 1500, label: "A finalizar... (por favor aguarde)" },
];

function getTimeEstimate(tier: string): { label: string; min: number; max: number } {
  if (tier === "gold") return { label: "20-25 minutos", min: 20, max: 25 };
  // Both bronze and silver share the same estimate
  return { label: "15-20 minutos", min: 15, max: 20 };
}

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

type InputMode = "file" | "text";
type ModalStep = "input" | "tier";

const LoadingProgress = ({ currentFile, totalFiles, totalSize, tier }: { currentFile: number; totalFiles: number; totalSize: number; tier: string }) => {
  const [elapsed, setElapsed] = useState(0);
  const estimate = getTimeEstimate(tier);

  useEffect(() => {
    const interval = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(interval);
  }, []);

  const elapsedMins = Math.floor(elapsed / 60);
  const mins = String(elapsedMins).padStart(2, "0");
  const secs = String(elapsed % 60).padStart(2, "0");

  const currentPhaseIndex = [...PHASES].reverse().findIndex((p) => elapsed >= p.threshold);
  const activeIndex = currentPhaseIndex === -1 ? 0 : PHASES.length - 1 - currentPhaseIndex;

  return (
    <div className="flex flex-col items-center gap-4 py-4">
      <Loader2 className="w-8 h-8 text-accent animate-spin" />
      {totalFiles > 1 && (
        <p className="text-sm font-semibold text-foreground">
          A analisar ficheiro {currentFile} de {totalFiles}...
        </p>
      )}
      <p className="text-sm font-medium text-foreground">A analisar documento...</p>
      <p className="text-xs text-muted-foreground">
        ⏱️ Tempo estimado: {estimate.label} ({totalFiles} ficheiro(s), tamanho total: {formatFileSize(totalSize)})
      </p>
      <p className="text-sm font-mono text-foreground">
        ⏱️ A decorrer há {elapsedMins > 0 ? `${elapsedMins} minuto${elapsedMins > 1 ? "s" : ""} e ` : ""}{elapsed % 60}s
      </p>
      <div className="w-full space-y-1.5 text-left">
        {PHASES.map((phase, i) => {
          const icon = i < activeIndex ? "✅" : i === activeIndex ? "🔄" : "⬜";
          return (
            <p
              key={i}
              className={`text-xs ${i <= activeIndex ? "text-foreground" : "text-muted-foreground/50"}`}
            >
              {icon} {phase.label}
            </p>
          );
        })}
      </div>
    </div>
  );
};

const isAcceptedFile = (file: File) => {
  const ext = "." + file.name.split(".").pop()?.toLowerCase();
  return ACCEPTED_EXTENSIONS.includes(ext) || ACCEPTED_MIME.includes(file.type);
};

export const UploadModal = ({ open, onClose, onSuccess }: UploadModalProps) => {
  const [mode, setMode] = useState<InputMode>("file");
  const [step, setStep] = useState<ModalStep>("input");
  const [files, setFiles] = useState<File[]>([]);
  const [freeText, setFreeText] = useState("");
  const [title, setTitle] = useState("");
  const [perguntas, setPerguntas] = useState("");
  const [loading, setLoading] = useState(false);
  const [selectedTier, setSelectedTier] = useState<TierLevel>("bronze");
  const [currentFileIndex, setCurrentFileIndex] = useState(0);
  const [dragActive, setDragActive] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const { toast } = useToast();
  const navigate = useNavigate();
  const { setInsufficientFunds, refreshBalance } = useWallet();

  const addFiles = (newFiles: FileList | File[]) => {
    const accepted = Array.from(newFiles).filter(isAcceptedFile);
    setFiles((prev) => {
      const combined = [...prev, ...accepted].slice(0, MAX_FILES);
      if (!title && combined.length > 0) {
        setTitle(combined[0].name.replace(/\.[^.]+$/, ""));
      }
      return combined;
    });
  };

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  };

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(e.type === "dragenter" || e.type === "dragover");
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files);
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files?.length) addFiles(e.target.files);
    e.target.value = "";
  };

  const getFilesToAnalyze = (): File[] => {
    if (mode === "file") return files;
    const blob = new Blob([freeText], { type: "text/plain" });
    return [new File([blob], `${title}.txt`, { type: "text/plain" })];
  };

  const isSubmitDisabled = () => {
    if (!title) return true;
    if (mode === "file") return files.length === 0;
    return freeText.trim().length < MIN_TEXT_LENGTH;
  };

  const handleProceedToTier = () => {
    if (isSubmitDisabled()) return;
    setStep("tier");
  };

  const handleTierSelect = async (tier: TierLevel) => {
    setSelectedTier(tier);
    await handleSubmit(tier);
  };

  const handleSubmit = async (tier: TierLevel) => {
    if (isSubmitDisabled()) return;
    setLoading(true);

    const filesToAnalyze = getFilesToAnalyze();

    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error("Sessão expirada");

      let lastDocId: string | null = null;

      for (let i = 0; i < filesToAnalyze.length; i++) {
        setCurrentFileIndex(i + 1);
        const file = filesToAnalyze[i];
        const docTitle = filesToAnalyze.length === 1 ? title : `${title} (${i + 1})`;

        const formData = new FormData();
        formData.append("file", file);
        formData.append("tier", tier);
        formData.append("titulo", docTitle);

        try {
          const extractKey = (m: string) => m.replace("openai/", "");
          const cm = localStorage.getItem("chefe_model") || "openai/gpt-5.2";
          const pm = localStorage.getItem("presidente_model") || "openai/gpt-5.2";
          const acm = localStorage.getItem("auditor_claude_model") || "sonnet-4.5";
          const jcm = localStorage.getItem("juiz_claude_model") || "sonnet-4.5";
          formData.append("chefe_model_key", extractKey(cm));
          formData.append("presidente_model_key", extractKey(pm));
          formData.append("auditor_claude_model", acm);
          formData.append("juiz_claude_model", jcm);
        } catch {}

        const perguntasLimpas = perguntas.split("\n").filter(p => p.trim()).join("---");
        if (perguntasLimpas) {
          formData.append("perguntas_raw", perguntasLimpas);
        }

        const response = await fetch(`${BACKEND_URL}/analyze`, {
          method: "POST",
          headers: { Authorization: `Bearer ${session.access_token}` },
          body: formData,
        });

        if (!response.ok) {
          if (response.status === 402) {
            const errData = await response.json();
            setInsufficientFunds({
              saldo_atual: errData.saldo_atual ?? 0,
              saldo_necessario: errData.saldo_necessario ?? 0,
              moeda: errData.moeda ?? "USD",
            });
            setLoading(false);
            return;
          }
          const errText = await response.text();
          throw new Error(`Erro no ficheiro "${file.name}": ${errText || response.statusText}`);
        }

        const analysisResult = await response.json();

        const riskMap: Record<string, string> = { "✅": "low", "⚠": "medium", "⚠️": "medium", "❌": "high" };
        const riskLevel = riskMap[analysisResult.simbolo_final] || "medium";

        const { data: doc, error: insertError } = await supabase.from("documents").insert({
          title: docTitle,
          file_name: file.name,
          user_id: session.user.id,
          status: "completed",
          summary: analysisResult.fase3_presidente || analysisResult.veredicto_final || null,
          risk_level: riskLevel,
          key_points: [],
        }).select().single();

        if (insertError) throw insertError;

        await supabase.from("documents")
          .update({ analysis_result: analysisResult })
          .eq("id", doc.id);

        lastDocId = doc.id;
      }

      await refreshBalance();
      toast({ title: "Análise concluída", description: `${filesToAnalyze.length} documento(s) analisado(s) com sucesso.` });
      setFiles([]);
      setFreeText("");
      setTitle("");
      setPerguntas("");
      setStep("input");
      onSuccess();
      onClose();

      if (lastDocId) navigate(`/document/${lastDocId}`);
    } catch (error: any) {
      toast({ title: "Erro na análise", description: error.message, variant: "destructive" });
    } finally {
      setLoading(false);
      setCurrentFileIndex(0);
    }
  };

  const handleClose = () => {
    setStep("input");
    onClose();
  };

  const totalFiles = mode === "file" ? files.length : 1;
  const totalSize = mode === "file"
    ? files.reduce((sum, f) => sum + f.size, 0)
    : new Blob([freeText]).size;

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className={step === "tier" ? "sm:max-w-2xl" : "sm:max-w-lg"}>
        <DialogHeader>
          <DialogTitle className="text-xl">
            {step === "tier" ? "Escolha o nível de análise" : "Nova Análise"}
          </DialogTitle>
        </DialogHeader>

        {step === "tier" ? (
          loading ? (
            <LoadingProgress currentFile={currentFileIndex} totalFiles={totalFiles} totalSize={totalSize} tier={selectedTier} />
          ) : (
            <TierSelectStep
              onSelect={handleTierSelect}
              onBack={() => setStep("input")}
            />
          )
        ) : (
          <div className="space-y-5 mt-2">
            {/* Mode Toggle */}
            <div className="flex rounded-lg border border-border overflow-hidden">
              <button
                type="button"
                onClick={() => setMode("file")}
                className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                  mode === "file"
                    ? "bg-primary text-primary-foreground"
                    : "bg-card text-muted-foreground hover:text-foreground"
                }`}
              >
                <Upload className="w-4 h-4" />
                Upload Ficheiro
              </button>
              <button
                type="button"
                onClick={() => setMode("text")}
                className={`flex-1 flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors ${
                  mode === "text"
                    ? "bg-primary text-primary-foreground"
                    : "bg-card text-muted-foreground hover:text-foreground"
                }`}
              >
                <Type className="w-4 h-4" />
                Texto Livre
              </button>
            </div>

            <div className="space-y-2">
              <Label>Título do documento</Label>
              <Input
                placeholder="Ex: Contrato de Prestação de Serviços"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="h-11"
              />
            </div>

            {mode === "file" ? (
              <div
                className={`relative border-2 border-dashed rounded-xl p-8 text-center transition-colors cursor-pointer ${
                  dragActive ? "border-accent bg-accent/5" : "border-border hover:border-accent/40"
                }`}
                onDragEnter={handleDrag}
                onDragLeave={handleDrag}
                onDragOver={handleDrag}
                onDrop={handleDrop}
                onClick={() => inputRef.current?.click()}
              >
                <input
                  ref={inputRef}
                  type="file"
                  accept=".pdf,.docx,.xlsx,.txt"
                  multiple
                  onChange={handleFileSelect}
                  className="hidden"
                />
                {files.length > 0 ? (
                  <div className="space-y-2" onClick={(e) => e.stopPropagation()}>
                    {files.map((f, i) => (
                      <div key={i} className="flex items-center justify-between gap-3 text-left">
                        <div className="flex items-center gap-2 min-w-0">
                          <FileText className="w-5 h-5 text-accent shrink-0" />
                          <div className="min-w-0">
                            <p className="font-medium text-foreground text-sm truncate">{f.name}</p>
                            <p className="text-xs text-muted-foreground">
                              {(f.size / 1024 / 1024).toFixed(2)} MB
                            </p>
                          </div>
                        </div>
                        <button
                          onClick={() => removeFile(i)}
                          className="p-1 rounded-full hover:bg-muted shrink-0"
                        >
                          <X className="w-4 h-4 text-muted-foreground" />
                        </button>
                      </div>
                    ))}
                    {files.length < MAX_FILES && (
                      <p
                        className="text-xs text-accent cursor-pointer hover:underline mt-2"
                        onClick={(e) => { e.stopPropagation(); inputRef.current?.click(); }}
                      >
                        + Adicionar mais ficheiros ({files.length}/{MAX_FILES})
                      </p>
                    )}
                  </div>
                ) : (
                  <>
                    <Upload className="w-10 h-10 text-muted-foreground/50 mx-auto mb-3" />
                    <p className="text-sm font-medium text-foreground mb-1">
                      Arraste ficheiros ou clique para selecionar
                    </p>
                    <p className="text-xs text-muted-foreground">
                      PDF, DOCX, XLSX, TXT — até {MAX_FILES} ficheiros, 20MB cada
                    </p>
                  </>
                )}
              </div>
            ) : (
              <div className="space-y-2">
                <Label>Texto a analisar</Label>
                <textarea
                  className="flex min-h-[160px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                  placeholder="Cole ou escreva o texto a analisar..."
                  value={freeText}
                  onChange={(e) => setFreeText(e.target.value)}
                  rows={8}
                />
                <p className={`text-xs ${freeText.trim().length < MIN_TEXT_LENGTH ? "text-muted-foreground" : "text-success"}`}>
                  {freeText.trim().length}/{MIN_TEXT_LENGTH} caracteres mínimos
                </p>
              </div>
            )}

            <div className="space-y-2">
              <Label>Perguntas (opcional)</Label>
              <p className="text-xs text-muted-foreground">
                Faça perguntas específicas sobre o documento. O sistema irá responder.
              </p>
              <textarea
                className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                placeholder={"Ex: O contrato é válido?\nQuais são os riscos principais?\nOs prazos estão correctos?"}
                value={perguntas}
                onChange={(e) => setPerguntas(e.target.value)}
                rows={3}
              />
            </div>

            {/* Time estimate before submit */}
            {totalSize > 0 && (
              <div className="rounded-lg bg-muted/30 border border-border px-4 py-3">
                <p className="text-xs text-muted-foreground">
                  ⏱️ Tempo estimado: <span className="font-medium text-foreground">15-20 minutos</span>
                  {" "}({totalFiles} ficheiro(s), tamanho total: {formatFileSize(totalSize)})
                </p>
              </div>
            )}

            <div className="flex justify-end gap-3 pt-2">
              <Button variant="outline" onClick={handleClose}>Cancelar</Button>
              <Button onClick={handleProceedToTier} disabled={isSubmitDisabled()}>
                {mode === "file" ? (
                  <Upload className="w-4 h-4 mr-2" />
                ) : (
                  <Type className="w-4 h-4 mr-2" />
                )}
                {mode === "file"
                  ? `Iniciar Análise${files.length > 1 ? ` (${files.length})` : ""}`
                  : "Analisar Texto"
                }
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
};
