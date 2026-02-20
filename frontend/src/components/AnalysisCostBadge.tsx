interface AnalysisCostBadgeProps {
  cost?: number;
  balanceAfter?: number;
}

export const AnalysisCostBadge = ({ cost, balanceAfter }: AnalysisCostBadgeProps) => {
  if (cost == null) return null;

  return (
    <div className="inline-flex items-center gap-3 px-3 py-1.5 rounded-full bg-muted/50 border border-border text-xs text-muted-foreground">
      <span>Custo: <span className="font-semibold text-foreground">${cost.toFixed(2)}</span></span>
      {balanceAfter != null && (
        <span>Saldo restante: <span className="font-semibold text-foreground">${balanceAfter.toFixed(2)}</span></span>
      )}
    </div>
  );
};
