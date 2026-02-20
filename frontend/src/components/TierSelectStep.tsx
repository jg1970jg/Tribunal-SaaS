import { useState } from "react";
import { Loader2, Sparkles, Shield, Crown } from "lucide-react";
import { Button } from "@/components/ui/button";

export type TierLevel = "bronze" | "silver" | "gold";

interface TierOption {
  tier: TierLevel;
  label: string;
  icon: React.ReactNode;
  description: string;
  borderColor: string;
  headerBg: string;
  ctaBg: string;
  badge?: string;
  badgeBg?: string;
  features: string[];
  shimmer?: boolean;
}

const TIERS: TierOption[] = [
  {
    tier: "bronze",
    label: "Standard",
    icon: <Shield className="w-7 h-7 text-amber-100" />,
    description: "Análise automática eficiente",
    borderColor: "border-amber-700/30 hover:border-amber-500",
    headerBg: "bg-gradient-to-br from-amber-600 via-amber-700 to-amber-900",
    ctaBg: "bg-gradient-to-r from-amber-600 to-amber-700 hover:from-amber-500 hover:to-amber-600",
    features: ["Análise automática", "Identifica problemas principais", "Relatório estruturado"],
  },
  {
    tier: "silver",
    label: "Premium",
    icon: <Sparkles className="w-7 h-7 text-blue-100" />,
    description: "Análise profunda com IA avançada",
    borderColor: "border-primary/30 hover:border-primary",
    headerBg: "bg-gradient-to-br from-slate-600 via-slate-700 to-slate-900",
    ctaBg: "bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70",
    badge: "⭐ RECOMENDADO",
    badgeBg: "bg-primary/90",
    features: ["IA avançada (Claude Opus + GPT)", "Encontra o que Standard não vê", "Análise mais aprofundada"],
    shimmer: true,
  },
  {
    tier: "gold",
    label: "Elite",
    icon: <Crown className="w-7 h-7 text-yellow-100" />,
    description: "IA de última geração",
    borderColor: "border-yellow-500/30 hover:border-yellow-400",
    headerBg: "bg-gradient-to-br from-yellow-500 via-yellow-600 to-amber-700",
    ctaBg: "bg-gradient-to-r from-yellow-500 to-amber-600 hover:from-yellow-400 hover:to-amber-500",
    badge: "💎 MÁXIMA QUALIDADE",
    badgeBg: "bg-yellow-500/90",
    features: ["IAs de topo (GPT-PRO, Claude Opus, Gemini Pro)", "Análise muito detalhada", "Deteta nuances subtis"],
  },
];

interface TierSelectStepProps {
  onSelect: (tier: TierLevel) => void;
  onBack: () => void;
  loading?: boolean;
}

export const TierSelectStep = ({ onSelect, onBack, loading }: TierSelectStepProps) => {
  const [hoveredTier, setHoveredTier] = useState<TierLevel | null>(null);

  if (loading) {
    return (
      <div className="flex flex-col items-center gap-3 py-8">
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">A preparar análise...</p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="text-center">
        <h3 className="text-lg font-semibold text-foreground">Escolha o nível de análise</h3>
        <p className="text-xs text-muted-foreground mt-1">O custo é calculado dinamicamente com base no documento</p>
      </div>

      <div className="grid grid-cols-3 gap-4">
        {TIERS.map((t, index) => {
          const isHovered = hoveredTier === t.tier;

          return (
            <button
              key={t.tier}
              onClick={() => onSelect(t.tier)}
              onMouseEnter={() => setHoveredTier(t.tier)}
              onMouseLeave={() => setHoveredTier(null)}
              className={`
                relative flex flex-col rounded-2xl border-2 transition-all duration-300 ease-out
                overflow-hidden text-left group
                ${t.borderColor}
                ${isHovered ? "shadow-xl -translate-y-1.5 scale-[1.03]" : "shadow-md hover:shadow-lg"}
              `}
              style={{
                animationDelay: `${index * 80}ms`,
                animation: "fade-in 0.4s ease-out backwards",
              }}
            >
              {/* Shimmer overlay for recommended */}
              {t.shimmer && (
                <div className="absolute inset-0 z-10 pointer-events-none overflow-hidden rounded-2xl">
                  <div className="absolute -inset-full bg-gradient-to-r from-transparent via-white/10 to-transparent animate-[shimmer_3s_ease-in-out_infinite] skew-x-12" />
                </div>
              )}

              {/* Badge */}
              {t.badge && (
                <div className={`absolute top-2.5 right-2.5 z-20 ${t.badgeBg} backdrop-blur-sm px-2 py-0.5 rounded-full`}>
                  <span className="text-[10px] font-bold text-white tracking-wide">{t.badge}</span>
                </div>
              )}

              {/* Header */}
              <div className={`${t.headerBg} px-4 py-5 relative`}>
                <div className={`transition-transform duration-300 ${isHovered ? "scale-110" : ""}`}>
                  {t.icon}
                </div>
                <div className="font-bold text-base text-white mt-2">{t.label}</div>
                <div className="text-white/75 text-xs leading-snug mt-0.5">{t.description}</div>
              </div>

              {/* Features */}
              <div className="px-4 py-4 flex-1 bg-card">
                <ul className="space-y-2">
                  {t.features.map((f, i) => (
                    <li
                      key={i}
                      className="text-xs text-muted-foreground flex items-start gap-1.5"
                    >
                      <span className="text-green-500 mt-0.5 shrink-0">✓</span>
                      <span>{f}</span>
                    </li>
                  ))}
                </ul>
              </div>

              {/* CTA */}
              <div className="px-4 pb-4 bg-card">
                <div
                  className={`
                    w-full py-2 rounded-lg ${t.ctaBg} text-white text-xs font-semibold text-center
                    transition-all duration-300
                    ${isHovered ? "shadow-lg" : ""}
                  `}
                >
                  Selecionar
                </div>
              </div>
            </button>
          );
        })}
      </div>

      <div className="flex justify-start pt-1">
        <Button variant="ghost" size="sm" onClick={onBack} className="text-muted-foreground">
          ← Voltar
        </Button>
      </div>
    </div>
  );
};
