import { Wallet, Zap } from "lucide-react";
import { useWallet } from "@/contexts/WalletContext";
import { useNavigate } from "react-router-dom";

export const WalletIndicator = () => {
  const { balance, loading, isAdmin, externalBalances } = useWallet();
  const navigate = useNavigate();

  if (loading || !balance) return null;

  const b = balance.balance_usd;
  const colorClass = b > 5 ? "text-success" : b >= 1 ? "text-warning" : "text-destructive";

  const orBalance = externalBalances?.openrouter?.balance_usd;
  const orColor = orBalance != null
    ? orBalance > 10 ? "text-success" : orBalance >= 2 ? "text-warning" : "text-destructive"
    : "";

  return (
    <div className="flex items-center gap-1.5">
      <button
        onClick={() => navigate("/wallet")}
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border hover:bg-muted/50 transition-colors"
        title="Ver carteira"
      >
        <Wallet className={`w-4 h-4 ${colorClass}`} />
        <span className={`text-sm font-semibold ${colorClass}`}>
          ${b.toFixed(2)}
        </span>
      </button>
      {isAdmin && orBalance != null && (
        <button
          onClick={() => navigate("/wallet")}
          className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border border-border hover:bg-muted/50 transition-colors"
          title="Saldo OpenRouter"
        >
          <Zap className={`w-4 h-4 ${orColor}`} />
          <span className={`text-sm font-semibold ${orColor}`}>
            ${orBalance.toFixed(2)}
          </span>
        </button>
      )}
    </div>
  );
};
