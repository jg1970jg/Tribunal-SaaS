# -*- coding: utf-8 -*-
"""
WALLET MANAGER - Sistema de Bloqueio de Créditos
===================================================
Implementa o sistema completo de:
  - Bloqueio de créditos ANTES do processamento
  - Settlement (ajuste) APÓS processamento
  - Devolução de diferença ao cliente
  - Margem de segurança de 25%

NOTA: Usa tabela `profiles` (NÃO `users`) para credits_balance/credits_blocked.
"""

import os
import logging
from typing import Any, Dict, Optional, Tuple
from datetime import datetime, timedelta
from supabase import Client

logger = logging.getLogger(__name__)

# Constantes
SAFETY_MARGIN = 1.25   # 25% margem de segurança
USD_PER_CREDIT = 0.005  # 1 crédito = $0.005


class WalletError(Exception):
    """Erro genérico de wallet."""
    pass


class InsufficientCreditsError(WalletError):
    """Saldo insuficiente para bloquear créditos."""
    def __init__(self, required: float, available: float):
        self.required = required
        self.available = available
        # Compatibilidade com engine.py
        self.saldo_atual = available
        self.saldo_necessario = required
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

    def get_balance(self, user_id: str, user_email: str = "") -> Dict[str, float]:
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
            result = self.sb.table("profiles").select(
                "credits_balance, credits_blocked"
            ).eq("id", user_id).single().execute()

            if not result.data:
                raise WalletError(f"Utilizador {user_id} não encontrado na tabela profiles")

            total = result.data.get("credits_balance") or 0.0
            blocked = result.data.get("credits_blocked") or 0.0

            return {
                "total": float(total),
                "blocked": float(blocked),
                "available": float(total - blocked),
            }
        except Exception as e:
            if "profiles" in str(e).lower() or "not found" in str(e).lower():
                logger.error(f"Erro ao consultar saldo (tabela profiles): {e}")
            else:
                logger.error(f"Erro ao consultar saldo: {e}")
            raise WalletError(f"Erro ao consultar saldo: {e}")

    def get_markup_multiplier(self) -> float:
        """Retorna o multiplicador de markup (margem de lucro)."""
        return 2.0  # 100% de margem

    def check_sufficient_balance(self, user_id: str, num_chars: int = 0) -> Dict[str, Any]:
        """
        Verifica se o utilizador tem saldo suficiente.
        Compatibilidade com engine.py antigo.
        """
        balance = self.get_balance(user_id)
        custo_estimado = 0.50  # Estimativa mínima

        return {
            "saldo_atual": balance["available"],
            "custo_estimado": custo_estimado,
            "suficiente": balance["available"] >= custo_estimado,
        }

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

            # Bloquear directamente via tabelas (sem SQL function)
            # 1. Atualizar credits_blocked no profile
            new_blocked = balance["blocked"] + blocked_usd
            self.sb.table("profiles").update({
                "credits_blocked": new_blocked,
            }).eq("id", user_id).execute()

            # 2. Registar transação (type: debit para bloqueio)
            tx_result = self.sb.table("wallet_transactions").insert({
                "user_id": user_id,
                "type": "debit",
                "amount_usd": blocked_usd,
                "balance_after_usd": balance["total"],
                "cost_real_usd": estimated_cost_usd,
                "run_id": analysis_id,
                "description": f"[BLOCK] {reason}",
            }).execute()

            transaction_id = tx_result.data[0]["id"] if tx_result.data else None

            # 3. Registar na tabela blocked_credits
            self.sb.table("blocked_credits").insert({
                "user_id": user_id,
                "analysis_id": analysis_id,
                "amount": blocked_usd,
                "transaction_id": transaction_id,
                "status": "blocked",
            }).execute()

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
        """
        try:
            # 1. Obter bloqueio original
            block_resp = self.sb.table("blocked_credits").select(
                "id, user_id, amount"
            ).eq("analysis_id", analysis_id).eq("status", "blocked").execute()

            if not block_resp.data:
                logger.warning(f"Nenhum bloqueio encontrado para analysis={analysis_id}")
                return {"status": "no_block", "real_cost": real_cost_usd}

            block = block_resp.data[0]
            user_id = block["user_id"]
            blocked_amount = float(block["amount"])
            markup = self.get_markup_multiplier()
            custo_cliente = real_cost_usd * markup
            refunded = max(0, blocked_amount - custo_cliente)

            # 2. Debitar custo real do saldo
            balance = self.get_balance(user_id)
            new_balance = max(0, balance["total"] - custo_cliente)
            new_blocked = max(0, balance["blocked"] - blocked_amount)

            self.sb.table("profiles").update({
                "credits_balance": new_balance,
                "credits_blocked": new_blocked,
            }).eq("id", user_id).execute()

            # 3. Marcar bloqueio como settled
            self.sb.table("blocked_credits").update({
                "status": "settled",
                "settled_at": datetime.now().isoformat(),
            }).eq("id", block["id"]).execute()

            # 4. Registar transação de debit
            self.sb.table("wallet_transactions").insert({
                "user_id": user_id,
                "type": "debit",
                "amount_usd": custo_cliente,
                "balance_after_usd": new_balance,
                "cost_real_usd": real_cost_usd,
                "markup_applied": markup,
                "run_id": analysis_id,
                "description": f"Liquidação análise {analysis_id}",
            }).execute()

            settlement = {
                "status": "success",
                "blocked": blocked_amount,
                "real_cost": real_cost_usd,
                "custo_cliente": custo_cliente,
                "refunded": refunded,
            }

            logger.info(
                f"Créditos liquidados: analysis={analysis_id}, "
                f"blocked=${blocked_amount:.4f}, "
                f"real=${real_cost_usd:.4f}, "
                f"refunded=${refunded:.4f}"
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
            # Obter bloqueio
            block_resp = self.sb.table("blocked_credits").select(
                "id, user_id, amount"
            ).eq("analysis_id", analysis_id).eq("status", "blocked").execute()

            if block_resp.data:
                block = block_resp.data[0]
                # Devolver créditos bloqueados
                balance = self.get_balance(block["user_id"])
                new_blocked = max(0, balance["blocked"] - float(block["amount"]))
                self.sb.table("profiles").update({
                    "credits_blocked": new_blocked,
                }).eq("id", block["user_id"]).execute()

                # Marcar como cancelado
                self.sb.table("blocked_credits").update({
                    "status": "cancelled",
                    "settled_at": datetime.now().isoformat(),
                }).eq("id", block["id"]).execute()

            logger.info(f"Bloqueio cancelado: analysis={analysis_id}")
        except Exception as e:
            logger.error(f"Erro ao cancelar bloqueio: {e}")
            # Não propagar erro (melhor ter bloqueio ativo que perder dinheiro)

    def debit(
        self,
        user_id: str,
        cost_real_usd: float,
        run_id: str,
    ) -> Dict[str, any]:
        """
        Débito direto (compatibilidade com engine.py antigo).
        Usa markup de 100% (custo × 2).

        Args:
            user_id: UUID do utilizador
            cost_real_usd: Custo real das APIs em USD
            run_id: ID da execução

        Returns:
            Dict com custo_real, custo_cliente, saldo_antes, saldo_depois, etc.
        """
        markup = self.get_markup_multiplier()
        custo_cliente = cost_real_usd * markup

        try:
            balance = self.get_balance(user_id)
            saldo_antes = balance["total"]

            new_balance = saldo_antes - custo_cliente

            # Atualizar saldo na tabela profiles
            self.sb.table("profiles").update({
                "credits_balance": max(0, new_balance)
            }).eq("id", user_id).execute()

            # Registar transação
            self.sb.table("wallet_transactions").insert({
                "user_id": user_id,
                "type": "debit",
                "amount_usd": custo_cliente,
                "balance_after_usd": max(0, new_balance),
                "cost_real_usd": cost_real_usd,
                "markup_applied": markup,
                "run_id": run_id,
                "description": f"Análise run_id={run_id}",
            }).execute()

            lucro = custo_cliente - cost_real_usd

            return {
                "custo_real": cost_real_usd,
                "custo_cliente": custo_cliente,
                "valor_debitado": custo_cliente,
                "saldo_antes": saldo_antes,
                "saldo_depois": max(0, new_balance),
                "markup": markup,
                "debito_parcial": new_balance < 0,
                "lucro": lucro,
                "run_id": run_id,
            }

        except Exception as e:
            logger.error(f"Erro ao debitar wallet: {e}")
            raise WalletError(f"Erro ao debitar wallet: {e}")

    def get_transactions(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        type_filter: Optional[str] = None,
    ) -> Dict[str, any]:
        """
        Retorna histórico de transações.
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
        """
        try:
            if amount_usd <= 0:
                raise ValueError("Valor deve ser positivo")

            balance = self.get_balance(user_id)
            current = balance["total"]
            new_balance = current + amount_usd

            # Atualizar saldo na tabela profiles
            self.sb.table("profiles").update({
                "credits_balance": new_balance
            }).eq("id", user_id).execute()

            # Criar transação
            tx_result = self.sb.table("wallet_transactions").insert({
                "user_id": user_id,
                "type": "credit",
                "amount_usd": amount_usd,
                "balance_after_usd": new_balance,
                "description": description,
                "admin_id": admin_id,
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
        """Gera relatório de lucro (apenas admin)."""
        try:
            from_date = (datetime.now() - timedelta(days=days)).isoformat()

            result = self.sb.table("wallet_transactions").select(
                "*"
            ).eq("type", "debit").gte("created_at", from_date).execute()

            transactions = result.data or []

            if not transactions:
                return {
                    "period_days": days,
                    "total_analyses": 0,
                    "total_revenue": 0.0,
                    "total_charged": 0.0,
                    "total_profit": 0.0,
                    "profit_margin": 0.0,
                    "avg_analysis_cost": 0.0,
                }

            total_charged = sum(float(t.get("amount") or 0) for t in transactions)
            # Custo real = charged / markup (2.0)
            total_real_cost = total_charged / 2.0
            total_profit = total_charged - total_real_cost
            profit_margin = (total_profit / total_charged * 100) if total_charged > 0 else 0

            return {
                "period_days": days,
                "total_analyses": len(transactions),
                "total_revenue": total_real_cost,
                "total_charged": total_charged,
                "total_profit": total_profit,
                "profit_margin": profit_margin,
                "avg_analysis_cost": total_real_cost / len(transactions) if transactions else 0,
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

    print("Conversão:")
    print(f"  $2.71 = {usd_to_credits(2.71)} créditos")
    print(f"  $3.52 = {usd_to_credits(3.52)} créditos")
    print(f"  $4.02 = {usd_to_credits(4.02)} créditos")
    print()

    print("Bloqueio com margem 25%:")
    estimated = 3.52
    blocked = estimated * SAFETY_MARGIN
    print(f"  Estimado: ${estimated:.2f}")
    print(f"  Bloqueado: ${blocked:.2f}")
    print(f"  Margem: ${blocked - estimated:.2f}")
