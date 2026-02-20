import { useState, useRef } from "react";
import { supabase } from "@/integrations/supabase/client";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Upload, FileText, X, Loader2 } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

const BACKEND_URL = "https://tribunal-saas.onrender.com";
const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".xlsx", ".txt"];
const ACCEPTED_MIME = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/plain",
];

interface AddDocumentModalProps {
  open: boolean;
  onClose: () => void;
  documentId: string;
  onSuccess: () => void;
}

const isAcceptedFile = (file: File) => {
  const ext = "." + file.name.split(".").pop()?.toLowerCase();
  return ACCEPTED_EXTENSIONS.includes(ext) || ACCEPTED_MIME.includes(file.type);
};

export const AddDocumentModal = ({ open, onClose, documentId, onSuccess }: AddDocumentModalProps) => {
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const { toast } = useToast();

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(e.type === "dragenter" || e.type === "dragover");
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragActive(false);
    const f = e.dataTransfer.files?.[0];
    if (f && isAcceptedFile(f)) setFile(f);
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f && isAcceptedFile(f)) setFile(f);
    e.target.value = "";
  };

  const handleSubmit = async () => {
    if (!file) return;
    setLoading(true);
    try {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) throw new Error("Sessão expirada");

      const formData = new FormData();
      formData.append("file", file);
      formData.append("document_id", documentId);

      const response = await fetch(`${BACKEND_URL}/analyze/add`, {
        method: "POST",
        headers: { Authorization: `Bearer ${session.access_token}` },
        body: formData,
      });

      if (!response.ok) {
        const errText = await response.text();
        throw new Error(errText || response.statusText);
      }

      toast({ title: "Documento adicionado", description: `"${file.name}" adicionado ao projecto.` });
      setFile(null);
      onSuccess();
      onClose();
    } catch (error: any) {
      toast({ title: "Erro", description: error.message, variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Adicionar Documento ao Projecto</DialogTitle>
        </DialogHeader>

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
            onChange={handleFileSelect}
            className="hidden"
          />
          {file ? (
            <div className="flex items-center justify-between gap-3 text-left" onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center gap-2 min-w-0">
                <FileText className="w-5 h-5 text-accent shrink-0" />
                <div className="min-w-0">
                  <p className="font-medium text-foreground text-sm truncate">{file.name}</p>
                  <p className="text-xs text-muted-foreground">{(file.size / 1024 / 1024).toFixed(2)} MB</p>
                </div>
              </div>
              <button onClick={() => setFile(null)} className="p-1 rounded-full hover:bg-muted shrink-0">
                <X className="w-4 h-4 text-muted-foreground" />
              </button>
            </div>
          ) : (
            <>
              <Upload className="w-10 h-10 text-muted-foreground/50 mx-auto mb-3" />
              <p className="text-sm font-medium text-foreground mb-1">Arraste um ficheiro ou clique para selecionar</p>
              <p className="text-xs text-muted-foreground">PDF, DOCX, XLSX, TXT — 20MB máximo</p>
            </>
          )}
        </div>

        <div className="flex justify-end gap-3 pt-2">
          <Button variant="outline" onClick={onClose} disabled={loading}>Cancelar</Button>
          <Button onClick={handleSubmit} disabled={!file || loading}>
            {loading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Upload className="w-4 h-4 mr-2" />}
            {loading ? "A processar..." : "Adicionar"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
};
