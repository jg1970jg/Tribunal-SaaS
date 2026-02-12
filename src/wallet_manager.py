# -*- coding: utf-8 -*-
"""
WALLET MANAGER - Sistema de Bloqueio de Créditos
===================================================
Implementa o sistema completo de:
- Bloqueio de créditos ANTES do processamento
- Settlement (ajuste) APÓS processamento
- Devolução de diferença ao cliente
- Margem de segurança de 25%
"""

import os
import logging
from typing import Dict, Optional, Tuple
from datetime import datetime
from supabase import Client

logger = logging.getLogger(__name__)

# Constantes
SAFETY_MARGIN = 1.25  # 25% margem de segurança
USD_PER_CREDIT = 0.005  # 1 crédito = $0.005


class WalletError(Exception):
    """Erro genérico de wallet."""
    pass


class InsufficientCreditsError(WalletError):
    """Saldo insuficiente para bloquear créditos."""
    def __init__(self, required: float, available: float):
        self.required = required
        self.available = available
        super().__init__(
            f"Saldo insuficiente. Necessário: ${required:.2f}, Disponível: ${available:.2f}"
        )


class WalletManager:
    """
    Gerencia todas as operações de wallet:
    - Bloqueio de créditos
    - Settlement (ajuste)
    - Consulta de saldo
    - Histórico de transações
    """
    
    def __init__(self, supabase_client: Client):
        """
        Args:
            supabase_client: Cliente Supabase (service_role)
        """
        self.sb = supabase_client
    
    def get_balance(self, user_id: str) -> Dict[str, float]:
        """
        Retorna saldo do utilizador.
        
        Returns:
            Dict com {
                'total': saldo total,
                'blocked': créditos bloqueados,
                'available': saldo disponível (total - blocked)
            }
        """
        try:
            result = self.sb.table("users").select(
                "credits_balance, credits_blocked"
            ).eq("id", user_id).single().execute()
            
            if not result.data:
                raise WalletError(f"Utilizador {user_id} não encontrado")
            
            total = result.data.get("credits_balance") or 0.0
            blocked = result.data.get("credits_blocked") or 0.0
            
            return {
                "total": float(total),
                "blocked": float(blocked),
                "available": float(total - blocked),
            }
        
        except Exception as e:
            logger.error(f"Erro ao consultar saldo: {e}")
            raise WalletError(f"Erro ao consultar saldo: {e}")
    
    def block_credits(
        self,
        user_id: str,
        analysis_id: str,
        estimated_cost_usd: float,
        reason: str = "Bloqueio para análise",
    ) -> Dict[str, any]:
        """
        Bloqueia créditos ANTES de processar análise.
        
        Args:
            user_id: UUID do utilizador
            analysis_id: UUID da análise
            estimated_cost_usd: Custo estimado em USD
            reason: Descrição do bloqueio
        
        Returns:
            Dict com {
                'transaction_id': UUID da transação,
                'blocked_usd': valor bloqueado,
                'balance_after': saldo após bloqueio
            }
        
        Raises:
            InsufficientCreditsError: Se saldo insuficiente
        """
        try:
            # Calcular bloqueio com margem de segurança
            blocked_usd = estimated_cost_usd * SAFETY_MARGIN
            
            # Verificar saldo disponível
            balance = self.get_balance(user_id)
            
            if balance["available"] < blocked_usd:
                raise InsufficientCreditsError(
                    required=blocked_usd,
                    available=balance["available"]
                )
            
            # Chamar função SQL para bloquear
            result = self.sb.rpc(
                "block_credits",
                {
                    "p_user_id": user_id,
                    "p_analysis_id": analysis_id,
                    "p_amount": blocked_usd,
                    "p_reason": reason,
                }
            ).execute()
            
            transaction_id = result.data
            
            logger.info(
                f"Créditos bloqueados: user={user_id}, "
                f"analysis={analysis_id}, blocked=${blocked_usd:.4f}, "
                f"tx={transaction_id}"
            )
            
            return {
                "transaction_id": transaction_id,
                "blocked_usd": blocked_usd,
                "balance_after": balance["available"] - blocked_usd,
            }
        
        except InsufficientCreditsError:
            raise
        except Exception as e:
            logger.error(f"Erro ao bloquear créditos: {e}")
            raise WalletError(f"Erro ao bloquear créditos: {e}")
    
    def settle_credits(
        self,
        analysis_id: str,
        real_cost_usd: float,
    ) -> Dict[str, any]:
        """
        Liquida créditos APÓS processamento.
        
        1. Débita o custo real
        2. Devolve a diferença (se houver)
        3. Marca bloqueio como liquidado
        
        Args:
            analysis_id: UUID da análise
            real_cost_usd: Custo real em USD
        
        Returns:
            Dict com {
                'status': 'success' ou 'margin_breach',
                'blocked': valor bloqueado,
                'real_cost': custo real,
                'refunded': valor devolvido (ou 0 se breach),
                'extra_charged': valor extra cobrado (se breach)
            }
        
        Raises:
            WalletError: Se análise não tem bloqueio
        """
        try:
            # Chamar função SQL para liquidar
            result = self.sb.rpc(
                "settle_credits",
                {
                    "p_analysis_id": analysis_id,
                    "p_real_cost": real_cost_usd,
                }
            ).execute()
            
            settlement = result.data
            
            if settlement["status"] == "margin_breach":
                logger.error(
                    f"⚠️ MARGIN BREACH! analysis={analysis_id}, "
                    f"blocked=${settlement['blocked']:.4f}, "
                    f"real=${settlement['real_cost']:.4f}, "
                    f"extra=${settlement.get('extra_charged', 0):.4f}"
                )
            else:
                logger.info(
                    f"Créditos liquidados: analysis={analysis_id}, "
                    f"blocked=${settlement['blocked']:.4f}, "
                    f"real=${settlement['real_cost']:.4f}, "
                    f"refunded=${settlement['refunded']:.4f}"
                )
            
            return settlement
        
        except Exception as e:
            logger.error(f"Erro ao liquidar créditos: {e}")
            raise WalletError(f"Erro ao liquidar créditos: {e}")
    
    def cancel_block(self, analysis_id: str) -> None:
        """
        Cancela bloqueio se análise falhar.
        
        Args:
            analysis_id: UUID da análise
        """
        try:
            self.sb.rpc(
                "cancel_credit_block",
                {"p_analysis_id": analysis_id}
            ).execute()
            
            logger.info(f"Bloqueio cancelado: analysis={analysis_id}")
        
        except Exception as e:
            logger.error(f"Erro ao cancelar bloqueio: {e}")
            # Não propagar erro (melhor ter bloqueio ativo que perder dinheiro)
    
    def get_transactions(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        type_filter: Optional[str] = None,
    ) -> Dict[str, any]:
        """
        Retorna histórico de transações.
        
        Args:
            user_id: UUID do utilizador
            limit: Máximo de registos (default 50, max 100)
            offset: Paginação
            type_filter: Filtrar por tipo (block, debit, refund, purchase, admin_credit)
        
        Returns:
            Dict com {
                'transactions': lista de transações,
                'total': total de transações,
                'limit': limite aplicado,
                'offset': offset aplicado
            }
        """
        try:
            query = self.sb.table("wallet_transactions").select(
                "*", count="exact"
            ).eq("user_id", user_id)
            
            if type_filter:
                query = query.eq("type", type_filter)
            
            query = query.order("created_at", desc=True).limit(limit).offset(offset)
            
            result = query.execute()
            
            return {
                "transactions": result.data,
                "total": result.count,
                "limit": limit,
                "offset": offset,
            }
        
        except Exception as e:
            logger.error(f"Erro ao consultar transações: {e}")
            raise WalletError(f"Erro ao consultar transações: {e}")
    
    def credit(
        self,
        user_id: str,
        amount_usd: float,
        description: str = "Crédito admin",
        admin_id: Optional[str] = None,
    ) -> Dict[str, any]:
        """
        Credita saldo (apenas admin).
        
        Args:
            user_id: UUID do utilizador
            amount_usd: Valor em USD
            description: Descrição do crédito
            admin_id: UUID do admin (opcional)
        
        Returns:
            Dict com {
                'transaction_id': UUID,
                'amount_usd': valor creditado,
                'balance_after': saldo após crédito
            }
        """
        try:
            if amount_usd <= 0:
                raise ValueError("Valor deve ser positivo")
            
            # Obter saldo atual
            balance = self.get_balance(user_id)
            current = balance["total"]
            new_balance = current + amount_usd
            
            # Atualizar saldo
            self.sb.table("users").update({
                "credits_balance": new_balance
            }).eq("id", user_id).execute()
            
            # Criar transação
            tx_result = self.sb.table("wallet_transactions").insert({
                "user_id": user_id,
                "type": "admin_credit",
                "amount": amount_usd,
                "balance_before": current,
                "balance_after": new_balance,
                "reason": description,
                "status": "completed",
                "completed_at": datetime.now().isoformat(),
            }).execute()
            
            transaction_id = tx_result.data[0]["id"]
            
            logger.info(
                f"Crédito admin: user={user_id}, amount=${amount_usd:.2f}, "
                f"admin={admin_id}, tx={transaction_id}"
            )
            
            return {
                "transaction_id": transaction_id,
                "amount_usd": amount_usd,
                "balance_after": new_balance,
            }
        
        except Exception as e:
            logger.error(f"Erro ao creditar saldo: {e}")
            raise WalletError(f"Erro ao creditar saldo: {e}")
    
    def get_profit_report(self, days: int = 30) -> Dict[str, any]:
        """
        Gera relatório de lucro (apenas admin).
        
        Args:
            days: Período em dias
        
        Returns:
            Dict com {
                'period_days': dias do período,
                'total_analyses': total de análises,
                'total_revenue': receita total (custo real),
                'total_charged': total cobrado aos clientes,
                'total_profit': lucro total,
                'profit_margin': margem de lucro %,
                'avg_analysis_cost': custo médio por análise
            }
        """
        try:
            # Consultar análises dos últimos N dias
            from_date = (datetime.now() - timedelta(days=days)).isoformat()
            
            result = self.sb.table("analyses").select(
                "credits_real_cost, credits_blocked"
            ).gte("created_at", from_date).execute()
            
            analyses = result.data
            
            if not analyses:
                return {
                    "period_days": days,
                    "total_analyses": 0,
                    "total_revenue": 0.0,
                    "total_charged": 0.0,
                    "total_profit": 0.0,
                    "profit_margin": 0.0,
                    "avg_analysis_cost": 0.0,
                }
            
            total_real_cost = sum(
                float(a.get("credits_real_cost") or 0) for a in analyses
            )
            total_charged = sum(
                float(a.get("credits_blocked") or 0) / SAFETY_MARGIN for a in analyses
            )
            
            total_profit = total_charged - total_real_cost
            profit_margin = (total_profit / total_charged * 100) if total_charged > 0 else 0
            
            return {
                "period_days": days,
                "total_analyses": len(analyses),
                "total_revenue": total_real_cost,
                "total_charged": total_charged,
                "total_profit": total_profit,
                "profit_margin": profit_margin,
                "avg_analysis_cost": total_real_cost / len(analyses) if analyses else 0,
            }
        
        except Exception as e:
            logger.error(f"Erro ao gerar relatório: {e}")
            raise WalletError(f"Erro ao gerar relatório: {e}")


# ============================================================
# SINGLETON - Instância global
# ============================================================

_wallet_manager: Optional[WalletManager] = None


def get_wallet_manager() -> WalletManager:
    """
    Retorna instância singleton do WalletManager.
    
    Requer: SUPABASE_SERVICE_ROLE_KEY no ambiente
    """
    global _wallet_manager
    
    if _wallet_manager is None:
        from auth_service import get_supabase_admin
        
        sb = get_supabase_admin()
        _wallet_manager = WalletManager(sb)
    
    return _wallet_manager


# ============================================================
# UTILITÁRIOS
# ============================================================

def usd_to_credits(usd: float) -> int:
    """Converte USD para créditos (arredondado para cima)."""
    import math
    return math.ceil(usd / USD_PER_CREDIT)


def credits_to_usd(credits: int) -> float:
    """Converte créditos para USD."""
    return credits * USD_PER_CREDIT


if __name__ == "__main__":
    # Testes
    print("=== WALLET MANAGER ===\n")
    
    # Teste de conversão
    print("Conversão:")
    print(f"  $2.71 = {usd_to_credits(2.71)} créditos")
    print(f"  $3.52 = {usd_to_credits(3.52)} créditos")
    print(f"  $4.02 = {usd_to_credits(4.02)} créditos")
    print()
    
    # Teste de bloqueio
    print("Bloqueio com margem 25%:")
    estimated = 3.52
    blocked = estimated * SAFETY_MARGIN
    print(f"  Estimado: ${estimated:.2f}")
    print(f"  Bloqueado: ${blocked:.2f}")
    print(f"  Margem: ${blocked - estimated:.2f}")
