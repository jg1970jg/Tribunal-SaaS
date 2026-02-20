import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { supabase } from '@/integrations/supabase/client';
import { TierCard } from './TierCard';
import { WalletIndicator } from './WalletIndicator';
import { ChevronRight, FileText, CheckCircle, Scale, Gavel, ArrowLeft } from 'lucide-react';
import { Button } from './ui/button';

interface TierSelection {
  extraction: 'bronze' | 'silver' | 'gold';
  audit: 'bronze' | 'silver' | 'gold';
  judgment: 'bronze' | 'silver' | 'gold';
  decision: 'bronze' | 'silver' | 'gold';
}

interface CostEstimate {
  total_real: number;
  total_client: number;
  total_blocked: number;
  by_phase: {
    extraction: number;
    audit: number;
    judgment: number;
    decision: number;
  };
}

export const ConfigurationPage: React.FC = () => {
  const navigate = useNavigate();
  const [currentPhase, setCurrentPhase] = useState<'extraction' | 'audit' | 'judgment' | 'decision'>('extraction');
  const [selection, setSelection] = useState<TierSelection>({
    extraction: 'silver',
    audit: 'silver',
    judgment: 'silver',
    decision: 'silver',
  });
  const [costEstimate, setCostEstimate] = useState<CostEstimate | null>(null);
  const [documentTokens] = useState(30000); // Será dinâmico após upload

  const phases = [
    { key: 'extraction', label: 'Extração', icon: FileText, description: 'Análise inicial do documento' },
    { key: 'audit', label: 'Auditoria', icon: CheckCircle, description: 'Verificação profunda de problemas' },
    { key: 'judgment', label: 'Relatoria', icon: Scale, description: 'Avaliação e emissão de parecer' },
    { key: 'decision', label: 'Conselheiro-Mor', icon: Gavel, description: 'Parecer final' },
  ];

  const tiers = [
    {
      tier: 'bronze' as const,
      label: 'Standard',
      icon: '🥉',
      description: 'Análise automática eficiente',
      colorGradient: 'from-amber-600 to-amber-800',
      colorBorder: 'border-amber-500',
      colorBg: 'bg-gradient-to-br from-amber-50 to-orange-50',
      features: [
        'Análise automática eficiente',
        'Identifica problemas principais',
        'Relatório estruturado',
        'Múltiplas IAs a trabalhar',
      ],
      idealCases: {
        title: 'Ideal para:',
        cases: [
          'Documentos do dia-a-dia',
          'Revisão de conformidade',
          'Casos diretos e claros',
        ],
      },
      timeEstimate: '15-20 min',
      credits: 1068,
    },
    {
      tier: 'silver' as const,
      label: 'Premium',
      icon: '🥈',
      description: 'Análise profunda com IA avançada',
      colorGradient: 'from-slate-400 to-slate-700',
      colorBorder: 'border-slate-400',
      colorBg: 'bg-gradient-to-br from-slate-50 to-gray-100',
      badge: '⭐ RECOMENDADO',
      features: [
        'IA avançada (Claude Opus + GPT)',
        'Encontra o que Standard não vê',
        'Relatório focado e assertivo',
        'Análise mais aprofundada',
      ],
      idealCases: {
        title: 'Ideal para:',
        cases: [
          'Documentos importantes',
          'Casos com várias partes',
          'Situações que requerem atenção especial',
        ],
      },
      timeEstimate: '15-20 min',
      credits: 1360,
    },
    {
      tier: 'gold' as const,
      label: 'Elite',
      icon: '🥇',
      description: 'IA de última geração',
      colorGradient: 'from-yellow-400 to-yellow-700',
      colorBorder: 'border-yellow-400',
      colorBg: 'bg-gradient-to-br from-yellow-50 to-amber-100',
      badge: '💎 MÁXIMA QUALIDADE',
      features: [
        'IAs de topo (GPT-PRO, Claude Opus, Gemini Pro)',
        'Análise muito detalhada e minuciosa',
        'Deteta nuances jurídicas subtis',
        'Relatório completo e fundamentado',
      ],
      idealCases: {
        title: 'Ideal para:',
        cases: [
          'Casos de elevada importância',
          'Quando muito está em jogo',
          'Situações mais exigentes',
        ],
      },
      timeEstimate: '20-25 min',
      credits: 1560,
    },
  ];

  // Buscar estimativa de custo quando seleção muda
  useEffect(() => {
    const fetchCostEstimate = async () => {
      try {
        const { data: { session } } = await supabase.auth.getSession();
        const response = await fetch('https://tribunal-saas.onrender.com/tiers/estimate', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(session?.access_token ? { 'Authorization': `Bearer ${session.access_token}` } : {}),
          },
          body: JSON.stringify({
            selection,
            document_tokens: documentTokens,
          }),
        });
        const data = await response.json();
        setCostEstimate(data);
      } catch (error) {
        console.error('Erro ao calcular custo:', error);
      }
    };

    fetchCostEstimate();
  }, [selection, documentTokens]);

  const handleTierSelect = (tier: 'bronze' | 'silver' | 'gold') => {
    setSelection(prev => ({
      ...prev,
      [currentPhase]: tier,
    }));

    // Avançar para próxima fase automaticamente
    const currentIndex = phases.findIndex(p => p.key === currentPhase);
    if (currentIndex < phases.length - 1) {
      setCurrentPhase(phases[currentIndex + 1].key as any);
    }
  };

  const PhaseIcon = phases.find(p => p.key === currentPhase)?.icon || FileText;

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
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

      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Button variant="ghost" onClick={() => navigate("/dashboard")} className="mb-6 text-muted-foreground">
          <ArrowLeft className="w-4 h-4 mr-2" />
          Voltar
        </Button>

        <div className="mb-6">
          <h1 className="text-2xl font-semibold text-foreground">Configurar Análise</h1>
          <p className="text-muted-foreground mt-1">
            Escolha o nível de qualidade para cada fase da análise
          </p>
        </div>

      <div className="pb-8">
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-8">
          {/* Sidebar - Fases */}
          <div className="lg:col-span-1">
            <div className="bg-card rounded-lg border border-border shadow-sm p-4 sticky top-20">
              <h3 className="font-semibold text-foreground mb-4">Fases</h3>
              <div className="space-y-2">
                {phases.map((phase, index) => {
                  const Icon = phase.icon;
                  const isSelected = currentPhase === phase.key;
                  const isCompleted = phases.findIndex(p => p.key === currentPhase) > index;
                  
                  return (
                    <button
                      key={phase.key}
                      onClick={() => setCurrentPhase(phase.key as any)}
                      className={`
                        w-full flex items-center gap-3 p-3 rounded-lg transition-all
                        ${isSelected ? 'bg-primary/10 border-2 border-primary' : 'bg-muted/50 border-2 border-transparent hover:bg-muted'}
                      `}
                    >
                      <div className={`
                        flex-shrink-0 w-8 h-8 rounded-full flex items-center justify-center
                        ${isCompleted ? 'bg-success text-success-foreground' : isSelected ? 'bg-primary text-primary-foreground' : 'bg-muted-foreground/30 text-muted-foreground'}
                      `}>
                        {isCompleted ? <CheckCircle className="w-5 h-5" /> : <Icon className="w-5 h-5" />}
                      </div>
                      <div className="flex-1 text-left">
                        <div className="font-medium text-sm text-foreground">
                          {phase.label}
                        </div>
                        <div className="text-xs text-muted-foreground capitalize">
                          {selection[phase.key as keyof TierSelection]}
                        </div>
                      </div>
                      {isSelected && <ChevronRight className="w-5 h-5 text-primary" />}
                    </button>
                  );
                })}
              </div>

              {/* Resumo de Custo */}
              {costEstimate && (
                <div className="mt-6 pt-6 border-t border-border">
                  <h4 className="font-semibold text-foreground mb-3">Estimativa</h4>
                  <div className="space-y-2 text-sm">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Custo estimado:</span>
                      <span className="font-medium text-foreground">${costEstimate.total_client.toFixed(2)}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Margem (+25%):</span>
                      <span className="font-medium text-foreground">
                        ${(costEstimate.total_blocked - costEstimate.total_client).toFixed(2)}
                      </span>
                    </div>
                    <div className="flex justify-between pt-2 border-t border-border">
                      <span className="font-semibold text-foreground">Total a bloquear:</span>
                      <span className="font-bold text-primary">
                        ${costEstimate.total_blocked.toFixed(2)}
                      </span>
                    </div>
                    <div className="text-xs text-muted-foreground mt-2">
                      ≈ {Math.ceil(costEstimate.total_blocked / 0.005)} créditos
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>

          {/* Main Content - Tier Cards */}
          <div className="lg:col-span-3">
            <div className="mb-6">
              <div className="flex items-center gap-3 mb-2">
                <PhaseIcon className="w-6 h-6 text-primary" />
                <h2 className="text-2xl font-bold text-foreground">
                  {phases.find(p => p.key === currentPhase)?.label}
                </h2>
              </div>
              <p className="text-muted-foreground">
                {phases.find(p => p.key === currentPhase)?.description}
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {tiers.map((tierData) => (
                <TierCard
                  key={tierData.tier}
                  {...tierData}
                  isSelected={selection[currentPhase] === tierData.tier}
                  onSelect={() => handleTierSelect(tierData.tier)}
                />
              ))}
            </div>

            {/* Botões de Navegação */}
            <div className="mt-8 flex justify-between">
              <button
                onClick={() => {
                  const currentIndex = phases.findIndex(p => p.key === currentPhase);
                  if (currentIndex > 0) {
                    setCurrentPhase(phases[currentIndex - 1].key as any);
                  }
                }}
                disabled={currentPhase === 'extraction'}
                className="px-6 py-2 text-muted-foreground bg-card border border-border rounded-lg hover:bg-muted disabled:opacity-50 disabled:cursor-not-allowed"
              >
                ← Anterior
              </button>

              {currentPhase === 'decision' ? (
                <button className="px-8 py-3 bg-primary text-primary-foreground font-semibold rounded-lg hover:bg-primary/90 shadow-lg">
                  Analisar Documento →
                </button>
              ) : (
                <button
                  onClick={() => {
                    const currentIndex = phases.findIndex(p => p.key === currentPhase);
                    if (currentIndex < phases.length - 1) {
                      setCurrentPhase(phases[currentIndex + 1].key as any);
                    }
                  }}
                  className="px-6 py-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90"
                >
                  Próxima →
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
      </main>
    </div>
  );
};
