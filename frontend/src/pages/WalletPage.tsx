import { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useWallet } from "@/contexts/WalletContext";
import { Button } from "@/components/ui/button";
import { Scale, ArrowLeft, Wallet, ArrowUpRight, ArrowDownRight, ChevronLeft, ChevronRight } from "lucide-react";
import { WalletIndicator } from "@/components/WalletIndicator";

type TxFilter = "" | "credit" | "debit";

interface Transaction {
  type: string;
  amount_usd: number;
  created_at: string;
  description: string;
  custo_real_apis?: number;
  markup_applied?: number;
  run_id?: string;
  balance_after?: number;
}

const PAGE_SIZE = 50;

const WalletPage = () => {
  const navigate = useNavigate();
  const { balance, refreshBalance, fetchTransactions } = useWallet();
  const [transactions, setTransactions] = useState<Transaction[]>([]);
  const [txFilter, setTxFilter] = useState<TxFilter>("");
  const [page, setPage] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);

  const loadTx = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await fetchTransactions(PAGE_SIZE, page * PAGE_SIZE, txFilter);
      setTransactions(data);
      setHasMore(data.length === PAGE_SIZE);
    } catch { /* silent */ }
    setLoading(false);
  }, [fetchTransactions, page, txFilter]);

  useEffect(() => { loadTx(); }, [loadTx]);
  useEffect(() => { refreshBalance(); }, [refreshBalance]);

  const filterOptions: { value: TxFilter; label: string }[] = [
    { value: "", label: "Todos" },
    { value: "credit", label: "Créditos" },
    { value: "debit", label: "Débitos" },
  ];

  const b = balance?.balance_usd ?? 0;
  const colorClass = b > 5 ? "text-success" : b >= 1 ? "text-warning" : "text-destructive";

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card/80 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg gradient-navy flex items-center justify-center">
              <Scale className="w-5 h-5 text-primary-foreground" />
            </div>
            <span className="text-lg font-semibold text-foreground tracking-tight">LexForum</span>
          </div>
          <WalletIndicator />
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Button variant="ghost" onClick={() => navigate("/dashboard")} className="mb-6 text-muted-foreground">
          <ArrowLeft className="w-4 h-4 mr-2" />
          Voltar ao Dashboard
        </Button>

        <div className="animate-fade-in">
          {/* Balance Card */}
          <div className="bg-card rounded-xl border border-border p-8 mb-6 text-center">
            <Wallet className={`w-10 h-10 mx-auto mb-3 ${colorClass}`} />
            <p className="text-sm text-muted-foreground mb-1">Saldo disponível</p>
            <p className={`text-4xl font-bold ${colorClass}`}>
              ${b.toFixed(2)} <span className="text-lg font-normal text-muted-foreground">USD</span>
            </p>
            <p className="text-sm text-muted-foreground mt-4">
              Contacte o administrador para carregar saldo.
            </p>
          </div>

          {/* Filters */}
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-foreground">Histórico de Transacções</h2>
            <div className="flex gap-1.5">
              {filterOptions.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => { setTxFilter(opt.value); setPage(0); }}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                    txFilter === opt.value
                      ? "bg-primary text-primary-foreground"
                      : "bg-card border border-border text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Transactions Table */}
          <div className="bg-card rounded-xl border border-border overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border bg-muted/30">
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Data/Hora</th>
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Tipo</th>
                    <th className="text-right px-4 py-3 font-medium text-muted-foreground">Valor</th>
                    <th className="text-right px-4 py-3 font-medium text-muted-foreground text-xs">Custo APIs</th>
                    <th className="text-left px-4 py-3 font-medium text-muted-foreground">Descrição</th>
                  </tr>
                </thead>
                <tbody>
                  {loading ? (
                    <tr><td colSpan={5} className="text-center py-8 text-muted-foreground">A carregar...</td></tr>
                  ) : transactions.length === 0 ? (
                    <tr><td colSpan={5} className="text-center py-8 text-muted-foreground">Sem transacções.</td></tr>
                  ) : transactions.map((tx, i) => {
                    const isCredit = tx.type === "credit";
                    return (
                      <tr key={i} className="border-b border-border last:border-0 hover:bg-muted/20">
                        <td className="px-4 py-3 text-foreground whitespace-nowrap">
                          {new Date(tx.created_at).toLocaleDateString("pt-PT", {
                            day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
                          })}
                        </td>
                        <td className="px-4 py-3">
                          <span className={`inline-flex items-center gap-1 text-xs font-semibold ${isCredit ? "text-success" : "text-destructive"}`}>
                            {isCredit ? <ArrowUpRight className="w-3.5 h-3.5" /> : <ArrowDownRight className="w-3.5 h-3.5" />}
                            {isCredit ? "Crédito" : "Débito"}
                          </span>
                        </td>
                        <td className={`px-4 py-3 text-right font-semibold ${isCredit ? "text-success" : "text-destructive"}`}>
                          {isCredit ? "+" : "-"}${tx.amount_usd.toFixed(2)}
                        </td>
                        <td className="px-4 py-3 text-right text-xs text-muted-foreground">
                          {tx.custo_real_apis != null ? `$${tx.custo_real_apis.toFixed(2)}` : "—"}
                        </td>
                        <td className="px-4 py-3 text-muted-foreground max-w-[200px] truncate">
                          {tx.description}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between mt-4">
            <Button
              variant="outline"
              size="sm"
              disabled={page === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              <ChevronLeft className="w-4 h-4 mr-1" />
              Anterior
            </Button>
            <span className="text-sm text-muted-foreground">Página {page + 1}</span>
            <Button
              variant="outline"
              size="sm"
              disabled={!hasMore}
              onClick={() => setPage((p) => p + 1)}
            >
              Seguinte
              <ChevronRight className="w-4 h-4 ml-1" />
            </Button>
          </div>
        </div>
      </main>
    </div>
  );
};

export default WalletPage;
