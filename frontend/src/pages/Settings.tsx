import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { ArrowLeft, Scale } from "lucide-react";
import { WalletIndicator } from "@/components/WalletIndicator";

const Settings = () => {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card/80 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg gradient-navy flex items-center justify-center">
              <Scale className="w-5 h-5 text-primary-foreground" />
            </div>
            <span className="text-lg font-semibold text-foreground tracking-tight">LexForum</span>
          </div>
          <WalletIndicator />
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Button variant="ghost" onClick={() => navigate("/dashboard")} className="mb-6 text-muted-foreground">
          <ArrowLeft className="w-4 h-4 mr-2" />
          Voltar
        </Button>

        <h1 className="text-2xl font-semibold text-foreground mb-2">Definições</h1>
        <p className="text-muted-foreground mb-8">
          Configurações da sua conta e preferências.
        </p>

        <div className="rounded-lg border border-border bg-card p-6">
          <p className="text-sm text-muted-foreground">
            O nível de análise (Standard, Premium ou Elite) é escolhido no momento de iniciar cada análise.
          </p>
        </div>
      </main>
    </div>
  );
};

export default Settings;
