import { createContext, useContext, useState, useEffect, useCallback, useRef, ReactNode } from "react";
import { supabase } from "@/integrations/supabase/client";
import { toast } from "sonner";

const BACKEND_URL = "https://tribunal-saas.onrender.com";

interface WalletBalance {
  balance_usd: number;
  moeda: string;
  markup_multiplier: number;
}

interface WalletTransaction {
  type: string;
  amount_usd: number;
  created_at: string;
  description: string;
  custo_real_apis?: number;
  markup_applied?: number;
  run_id?: string;
  balance_after?: number;
}

interface InsufficientFundsError {
  saldo_atual: number;
  saldo_necessario: number;
  moeda: string;
}

interface WalletContextType {
  balance: WalletBalance | null;
  loading: boolean;
  refreshBalance: () => Promise<void>;
  fetchTransactions: (limit?: number, offset?: number, typeFilter?: string) => Promise<{ data: WalletTransaction[]; total: number }>;
  insufficientFunds: InsufficientFundsError | null;
  setInsufficientFunds: (e: InsufficientFundsError | null) => void;
  getAuthHeaders: () => Promise<Record<string, string>>;
}

const WalletContext = createContext<WalletContextType | null>(null);

export const useWallet = () => {
  const ctx = useContext(WalletContext);
  if (!ctx) throw new Error("useWallet must be used within WalletProvider");
  return ctx;
};

export const WalletProvider = ({ children }: { children: ReactNode }) => {
  const [balance, setBalance] = useState<WalletBalance | null>(null);
  const [loading, setLoading] = useState(true);
  const [insufficientFunds, setInsufficientFunds] = useState<InsufficientFundsError | null>(null);
  const lastFetchTimeRef = useRef(0);
  const consecutiveErrorsRef = useRef(0);

  const getAuthHeaders = useCallback(async (): Promise<Record<string, string>> => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) return {};
    return {
      "Content-Type": "application/json",
      Authorization: `Bearer ${session.access_token}`,
    };
  }, []);

  const refreshBalance = useCallback(async () => {
    // Stop completely after 5+ consecutive errors
    if (consecutiveErrorsRef.current >= 5) {
      setLoading(false);
      return;
    }

    // Minimum 10s between any calls to avoid duplicates
    const now = Date.now();
    if (now - lastFetchTimeRef.current < 10000) return;

    // Exponential backoff: 30s × 2^errors, max 5 min
    const minInterval = Math.min(30000 * Math.pow(2, consecutiveErrorsRef.current), 300000);
    if (now - lastFetchTimeRef.current < minInterval && lastFetchTimeRef.current > 0) return;

    try {
      const headers = await getAuthHeaders();
      if (!headers.Authorization) return;
      lastFetchTimeRef.current = now;
      const res = await fetch(`${BACKEND_URL}/wallet/balance`, { headers });
      if (res.ok) {
        const data = await res.json();
        setBalance(data);
        consecutiveErrorsRef.current = 0;
      } else {
        consecutiveErrorsRef.current += 1;
        if (consecutiveErrorsRef.current >= 5) {
          toast.error("Não foi possível contactar o servidor. Recarregue a página para tentar novamente.");
        }
        console.warn(`Wallet balance fetch failed: ${res.status} (backoff: ${minInterval / 1000}s)`);
      }
    } catch {
      consecutiveErrorsRef.current += 1;
      if (consecutiveErrorsRef.current >= 5) {
        toast.error("Não foi possível contactar o servidor. Recarregue a página para tentar novamente.");
      }
    } finally {
      setLoading(false);
    }
  }, [getAuthHeaders]);

  const fetchTransactions = useCallback(async (limit = 50, offset = 0, typeFilter = "") => {
    const headers = await getAuthHeaders();
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (typeFilter) params.set("type_filter", typeFilter);
    const res = await fetch(`${BACKEND_URL}/wallet/transactions?${params}`, { headers });
    if (!res.ok) throw new Error("Erro ao carregar transações");
    const data = await res.json();
    return { data: Array.isArray(data) ? data : data.transactions || [], total: data.total || (Array.isArray(data) ? data.length : 0) };
  }, [getAuthHeaders]);

  useEffect(() => {
    const { data: { subscription } } = supabase.auth.onAuthStateChange((event) => {
      if (event === "SIGNED_IN") refreshBalance();
      if (event === "SIGNED_OUT") { setBalance(null); setLoading(false); }
    });
    refreshBalance();
    return () => subscription.unsubscribe();
  }, [refreshBalance]);

  return (
    <WalletContext.Provider value={{ balance, loading, refreshBalance, fetchTransactions, insufficientFunds, setInsufficientFunds, getAuthHeaders }}>
      {children}
    </WalletContext.Provider>
  );
};
