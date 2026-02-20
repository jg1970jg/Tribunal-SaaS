import { useWallet } from "@/contexts/WalletContext";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { AlertTriangle } from "lucide-react";

export const InsufficientFundsModal = () => {
  const { insufficientFunds, setInsufficientFunds } = useWallet();

  if (!insufficientFunds) return null;

  return (
    <Dialog open={!!insufficientFunds} onOpenChange={() => setInsufficientFunds(null)}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <AlertTriangle className="w-5 h-5 text-destructive" />
            Saldo insuficiente
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3 py-2">
          <p className="text-sm text-muted-foreground">
            Saldo insuficiente para esta análise.
          </p>
          <div className="grid grid-cols-2 gap-3">
            <div className="bg-muted/50 rounded-lg p-3">
              <p className="text-xs text-muted-foreground mb-1">Saldo actual</p>
              <p className="text-lg font-semibold text-destructive">
                ${insufficientFunds.saldo_atual.toFixed(2)}
              </p>
            </div>
            <div className="bg-muted/50 rounded-lg p-3">
              <p className="text-xs text-muted-foreground mb-1">Custo estimado</p>
              <p className="text-lg font-semibold text-foreground">
                ~${insufficientFunds.saldo_necessario.toFixed(2)}
              </p>
            </div>
          </div>
          <p className="text-sm text-muted-foreground">
            Contacte o administrador para carregar saldo.
          </p>
        </div>
        <DialogFooter>
          <Button onClick={() => setInsufficientFunds(null)}>OK</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
