import React from 'react';
import { Check } from 'lucide-react';

interface TierCardProps {
  tier: 'bronze' | 'silver' | 'gold';
  label: string;
  icon: string;
  description: string;
  colorGradient: string;
  colorBorder: string;
  colorBg: string;
  badge?: string;
  features: string[];
  idealCases: {
    title: string;
    cases: string[];
  };
  timeEstimate: string;
  credits: number;
  isSelected?: boolean;
  onSelect?: () => void;
}

export const TierCard: React.FC<TierCardProps> = ({
  tier,
  label,
  icon,
  description,
  colorGradient,
  colorBorder,
  colorBg,
  badge,
  features,
  idealCases,
  timeEstimate,
  credits,
  isSelected = false,
  onSelect,
}) => {
  return (
    <div
      onClick={onSelect}
      className={`
        relative overflow-hidden rounded-2xl border-2 transition-all duration-300 cursor-pointer
        ${isSelected ? `${colorBorder} shadow-2xl scale-105` : 'border-gray-200 hover:shadow-xl hover:scale-102'}
        ${colorBg}
      `}
    >
      {/* Header com gradiente */}
      <div className={`relative bg-gradient-to-r ${colorGradient} p-6 text-white`}>
        {/* Shimmer effect */}
        <div className="absolute inset-0 bg-gradient-to-r from-transparent via-white/20 to-transparent animate-shimmer" />
        
        {/* Badge (se existir) */}
        {badge && (
          <div className="absolute top-3 right-3 bg-white/20 backdrop-blur-sm px-3 py-1 rounded-full text-xs font-bold">
            {badge}
          </div>
        )}
        
        <div className="relative z-10">
          <div className="text-5xl mb-3">{icon}</div>
          <h3 className="text-2xl font-bold mb-2">{label}</h3>
          <p className="text-white/90 text-sm">{description}</p>
        </div>
      </div>

      {/* Corpo do card */}
      <div className="p-6 space-y-6">
        {/* Features */}
        <div>
          <h4 className="font-semibold text-gray-900 mb-3 text-sm uppercase tracking-wide">
            O que inclui:
          </h4>
          <ul className="space-y-2">
            {features.map((feature, index) => (
              <li key={index} className="flex items-start gap-2 text-sm text-gray-700">
                <Check className="w-5 h-5 text-green-600 flex-shrink-0 mt-0.5" />
                <span>{feature}</span>
              </li>
            ))}
          </ul>
        </div>

        {/* Ideal para */}
        <div className="bg-gray-50 rounded-lg p-4">
          <h4 className="font-semibold text-gray-900 mb-2 text-sm">
            {idealCases.title}
          </h4>
          <ul className="space-y-1">
            {idealCases.cases.map((useCase, index) => (
              <li key={index} className="text-sm text-gray-600 flex items-start gap-2">
                <span className="text-gray-400 mt-1">•</span>
                <span>{useCase}</span>
              </li>
            ))}
          </ul>
        </div>

        {/* Métricas */}
        <div className={`${timeEstimate ? 'grid grid-cols-2' : 'grid grid-cols-1'} gap-4 pt-4 border-t border-gray-200`}>
          {timeEstimate && (
            <div>
              <div className="text-xs text-gray-500 uppercase tracking-wide mb-1">
                Tempo
              </div>
              <div className="font-semibold text-gray-900">{timeEstimate}</div>
            </div>
          )}
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-wide mb-1">
              Custo
            </div>
            <div className="font-semibold text-gray-900">
              {credits.toLocaleString('pt-PT')} créditos
            </div>
          </div>
        </div>

        {/* Botão de seleção */}
        <button
          className={`
            w-full py-3 px-4 rounded-lg font-semibold transition-all duration-200
            ${isSelected 
              ? `bg-gradient-to-r ${colorGradient} text-white shadow-lg` 
              : 'bg-gray-100 text-gray-700 hover:bg-gray-200'
            }
          `}
        >
          {isSelected ? '✓ Selecionado' : 'Selecionar'}
        </button>
      </div>

      {/* Bounce animation when selected */}
      {isSelected && (
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute inset-0 border-4 border-white/50 rounded-2xl animate-ping" />
        </div>
      )}
    </div>
  );
};

// Exemplo de uso:
export const TierCardExample = () => {
  const [selectedTier, setSelectedTier] = React.useState<'bronze' | 'silver' | 'gold'>('silver');

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
          'Análise com múltiplas IAs',
        ],
      },
      timeEstimate: '',
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
          'Quando quer certeza na análise',
        ],
      },
      timeEstimate: '',
      credits: 1360,
    },
    {
      tier: 'gold' as const,
      label: 'Elite',
      icon: '🥇',
      description: 'IAs de topo combinadas',
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
          'Quando quer a máxima confiança',
        ],
      },
      timeEstimate: '',
      credits: 1560,
    },
  ];

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-7xl mx-auto">
        <div className="text-center mb-12">
          <h1 className="text-4xl font-bold text-gray-900 mb-4">
            Escolha o Nível de Análise
          </h1>
          <p className="text-xl text-gray-600">
            Selecione a qualidade de IA para cada fase da análise
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
          {tiers.map((tierData) => (
            <TierCard
              key={tierData.tier}
              {...tierData}
              isSelected={selectedTier === tierData.tier}
              onSelect={() => setSelectedTier(tierData.tier)}
            />
          ))}
        </div>
      </div>
    </div>
  );
};

// Adicionar ao CSS global (ou criar ficheiro separado):
// @keyframes shimmer {
//   0% { transform: translateX(-100%); }
//   100% { transform: translateX(100%); }
// }
// .animate-shimmer {
//   animation: shimmer 3s infinite;
// }
