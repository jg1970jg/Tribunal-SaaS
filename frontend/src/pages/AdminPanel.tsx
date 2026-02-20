import { useState, useEffect, useCallback } from "react";
import { supabase } from "@/integrations/supabase/client";
import { useNavigate } from "react-router-dom";
import { useWallet } from "@/contexts/WalletContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Scale, ArrowLeft, DollarSign, TrendingUp, Users, Send } from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { WalletIndicator } from "@/components/WalletIndicator";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

const BACKEND_URL = "https://tribunal-saas.onrender.com";

interface ProfitReport {
  total_cobrado_clientes?: number;
  total_gasto_apis?: number;
  lucro_total?: number;
  por_dia?: Array<{ date: string; revenue: number; cost: number; profit: number }>;
}

const AdminPanel = () => {
  const navigate = useNavigate();
  const { toast } = useToast();
  const { getAuthHeaders } = useWallet();
  const [report, setReport] = useState<ProfitReport | null>(null);
  const [reportLoading, setReportLoading] = useState(true);
  const [authorized, setAuthorized] = useState(false);

  // Credit form
  const [creditUserId, setCreditUserId] = useState("");
  const [creditAmount, setCreditAmount] = useState("");
  const [creditDesc, setCreditDesc] = useState("");
  const [crediting, setCrediting] = useState(false);

  useEffect(() => {
    const checkAdmin = async () => {
      const { data: { session } } = await supabase.auth.getSession();
      if (!session) { navigate("/auth"); return; }
      const { data } = await supabase
        .from("user_roles")
        .select("role")
        .eq("user_id", session.user.id)
        .eq("role", "admin")
        .maybeSingle();
      if (!data) { navigate("/dashboard"); return; }
      setAuthorized(true);
    };
    checkAdmin();
  }, [navigate]);

  const loadReport = useCallback(async () => {
    setReportLoading(true);
    try {
      const headers = await getAuthHeaders();
      const res = await fetch(`${BACKEND_URL}/admin/profit-report?days=30`, { headers });
      if (res.ok) setReport(await res.json());
    } catch { /* silent */ }
    setReportLoading(false);
  }, [getAuthHeaders]);

  useEffect(() => { if (authorized) loadReport(); }, [authorized, loadReport]);

  const handleCredit = async () => {
    if (!creditUserId.trim() || !creditAmount) return;
    setCrediting(true);
    try {
      const headers = await getAuthHeaders();
      const res = await fetch(`${BACKEND_URL}/wallet/credit`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          user_id: creditUserId.trim(),
          amount_usd: parseFloat(creditAmount),
          description: creditDesc || "Carregamento manual",
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      toast({ title: "Crédito adicionado", description: `$${parseFloat(creditAmount).toFixed(2)} creditados.` });
      setCreditUserId("");
      setCreditAmount("");
      setCreditDesc("");
    } catch (e: any) {
      toast({ title: "Erro", description: e.message, variant: "destructive" });
    }
    setCrediting(false);
  };

  const chartData = report?.por_dia?.map((d) => ({
    date: new Date(d.date).toLocaleDateString("pt-PT", { day: "numeric", month: "short" }),
    Receita: d.revenue,
    Custo: d.cost,
    Lucro: d.profit,
  })) || [];

  if (!authorized) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="w-8 h-8 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b border-border bg-card/80 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg gradient-navy flex items-center justify-center">
              <Scale className="w-5 h-5 text-primary-foreground" />
            </div>
            <span className="text-lg font-semibold text-foreground tracking-tight">LexForum</span>
          </div>
          <WalletIndicator />
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Button variant="ghost" onClick={() => navigate("/dashboard")} className="mb-6 text-muted-foreground">
          <ArrowLeft className="w-4 h-4 mr-2" />
          Voltar ao Dashboard
        </Button>

        <div className="animate-fade-in">
          <h1 className="text-2xl font-semibold text-foreground mb-6">Painel de Administração</h1>

          {/* Profit Summary */}
          {reportLoading ? (
            <div className="text-center py-8 text-muted-foreground">A carregar relatório...</div>
          ) : report ? (
            <>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
                <div className="bg-card rounded-xl border border-border p-5">
                  <div className="flex items-center gap-2 mb-2">
                    <DollarSign className="w-5 h-5 text-accent" />
                    <p className="text-sm text-muted-foreground">Total Cobrado</p>
                  </div>
                  <p className="text-2xl font-bold text-foreground">${(report.total_cobrado_clientes ?? 0).toFixed(2)}</p>
                </div>
                <div className="bg-card rounded-xl border border-border p-5">
                  <div className="flex items-center gap-2 mb-2">
                    <Users className="w-5 h-5 text-warning" />
                    <p className="text-sm text-muted-foreground">Gasto em APIs</p>
                  </div>
                  <p className="text-2xl font-bold text-foreground">${(report.total_gasto_apis ?? 0).toFixed(2)}</p>
                </div>
                <div className="bg-card rounded-xl border border-border p-5">
                  <div className="flex items-center gap-2 mb-2">
                    <TrendingUp className="w-5 h-5 text-success" />
                    <p className="text-sm text-muted-foreground">Lucro Total</p>
                  </div>
                  <p className="text-2xl font-bold text-success">${(report.lucro_total ?? 0).toFixed(2)}</p>
                </div>
              </div>

              {/* Chart */}
              {chartData.length > 0 && (
                <div className="bg-card rounded-xl border border-border p-6 mb-6">
                  <h2 className="text-lg font-semibold text-foreground mb-4">Lucro por Dia (últimos 30 dias)</h2>
                  <div className="h-[300px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={chartData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                        <XAxis dataKey="date" tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} />
                        <YAxis tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} tickFormatter={(v) => `$${v}`} />
                        <Tooltip
                          contentStyle={{
                            backgroundColor: "hsl(var(--card))",
                            border: "1px solid hsl(var(--border))",
                            borderRadius: "8px",
                            fontSize: "12px",
                          }}
                          formatter={(value: number) => `$${value.toFixed(2)}`}
                        />
                        <Bar dataKey="Receita" fill="hsl(var(--accent))" radius={[4, 4, 0, 0]} />
                        <Bar dataKey="Custo" fill="hsl(var(--warning))" radius={[4, 4, 0, 0]} />
                        <Bar dataKey="Lucro" fill="hsl(var(--success))" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              )}
            </>
          ) : (
            <div className="text-center py-8 text-muted-foreground">Não foi possível carregar o relatório.</div>
          )}

          {/* Credit Form */}
          <div className="bg-card rounded-xl border border-border p-6">
            <h2 className="text-lg font-semibold text-foreground mb-4">Creditar Saldo a Utilizador</h2>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="space-y-2">
                <Label>ID do Utilizador</Label>
                <Input
                  placeholder="UUID do utilizador"
                  value={creditUserId}
                  onChange={(e) => setCreditUserId(e.target.value)}
                  className="h-11"
                />
              </div>
              <div className="space-y-2">
                <Label>Valor (USD)</Label>
                <Input
                  type="number"
                  min={0.01}
                  step={0.01}
                  placeholder="20.00"
                  value={creditAmount}
                  onChange={(e) => setCreditAmount(e.target.value)}
                  className="h-11"
                />
              </div>
              <div className="space-y-2">
                <Label>Descrição</Label>
                <Input
                  placeholder="Carregamento"
                  value={creditDesc}
                  onChange={(e) => setCreditDesc(e.target.value)}
                  className="h-11"
                />
              </div>
            </div>
            <div className="flex justify-end mt-4">
              <Button onClick={handleCredit} disabled={crediting || !creditUserId || !creditAmount}>
                <Send className="w-4 h-4 mr-2" />
                {crediting ? "A creditar..." : "Creditar"}
              </Button>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
};

export default AdminPanel;
