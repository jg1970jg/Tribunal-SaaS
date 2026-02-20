import { Wallet } from "lucide-react";
import { useWallet } from "@/contexts/WalletContext";
import { useNavigate } from "react-router-dom";

export const WalletIndicator = () => {
  const { balance, loading } = useWallet();
  const navigate = useNavigate();

  if (loading || !balance) return null;

  const b = balance.balance_usd;
  const colorClass = b > 5 ? "text-success" : b >= 1 ? "text-warning" : "text-destructive";

  return (
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
  );
};
