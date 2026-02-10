# -*- coding: utf-8 -*-
"""
WALLET MANAGER - Tribunal SaaS V2
============================================================
Sistema de saldo pré-pago para clientes.

Funcionalidades:
  - Consultar saldo (get_balance)
  - Verificar saldo suficiente com estimativa (check_sufficient_balance)
  - Debitar após análise com custo real (debit)
  - Creditar saldo — admin only (credit)
  - Histórico de transações (get_transactions)
  - Relatório de lucro — admin only (get_profit_report)

Tabelas Supabase:
  - wallet_balances: saldo actual por utilizador
  - wallet_transactions: histórico de todas as operações
  - wallet_config: configuração dinâmica (margem, etc.)

Markup:
  custo_cliente = custo_real_apis × MARKUP_MULTIPLIER
  MARKUP_MULTIPLIER = 1.47 (40% lucro + 7% margem de segurança)
  Pode ser alterado via wallet_config sem mudar código.
============================================================
"""

import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


# ============================================================
# CONSTANTES DEFAULT (podem ser overridden por wallet_config)
# ============================================================

DEFAULT_MARKUP_MULTIPLIER = 1.47  # 40% lucro + 7% segurança
DEFAULT_MIN_BALANCE_USD = 0.50    # saldo mínimo para iniciar análise
ADMIN_INITIAL_BALANCE = 9999.99   # saldo inicial para contas admin

# Heurística de estimativa de custo por tamanho do documento
COST_ESTIMATE_BY_SIZE = [
    # (max_chars, custo_estimado_apis_usd)
    (10_000, 1.00),
    (30_000, 2.00),
    (60_000, 3.50),
    (999_999_999, 5.00),
]


# ============================================================
# WALLET MANAGER
# ============================================================

class InsufficientBalanceError(Exception):
    """Saldo insuficiente para executar a análise."""

    def __init__(self, saldo_atual: float, saldo_necessario: float):
        self.saldo_atual = saldo_atual
        self.saldo_necessario = saldo_necessario
        super().__init__(
            f"Saldo insuficiente: ${saldo_atual:.2f} USD. "
            f"Necessário: ~${saldo_necessario:.2f} USD."
        )


class WalletManager:
    """
    Gestor de wallet pré-pago com integração Supabase.

    Uso típico:
        wm = WalletManager(supabase_admin)

        # Antes do pipeline
        wm.check_sufficient_balance(user_id, num_chars=50000)

        # Após pipeline (custo real conhecido)
        wm.debit(user_id, cost_real_usd=3.42, run_id="xxx")

        # Admin: creditar
        wm.credit(user_id, amount_usd=20.00, description="Pagamento MB")
    """

    def __init__(self, supabase_client):
        """
        Args:
            supabase_client: Cliente Supabase com service_role key
        """
        self._sb = supabase_client
        self._config_cache: Optional[Dict[str, str]] = None
        self._config_loaded_at: Optional[datetime] = None
        # Cache de admin: {user_id: bool}
        self._admin_cache: Dict[str, bool] = {}

    # ============================================================
    # CONFIGURAÇÃO DINÂMICA
    # ============================================================

    def _load_config(self) -> Dict[str, str]:
        """Carrega wallet_config do Supabase (cache 5 min)."""
        if (
            self._config_cache is not None
            and self._config_loaded_at
            and (datetime.now() - self._config_loaded_at).total_seconds() < 300
        ):
            return self._config_cache

        try:
            resp = self._sb.table("wallet_config").select("key, value").execute()
            config = {}
            for row in (resp.data or []):
                config[row["key"]] = row["value"]
            self._config_cache = config
            self._config_loaded_at = datetime.now()
            return config
        except Exception as e:
            logger.warning(f"[WALLET] Erro ao carregar wallet_config: {e}")
            return self._config_cache or {}

    def get_markup_multiplier(self) -> float:
        """Retorna multiplicador de markup (default 1.47)."""
        config = self._load_config()
        try:
            return float(config.get("markup_multiplier", str(DEFAULT_MARKUP_MULTIPLIER)))
        except (ValueError, TypeError):
            return DEFAULT_MARKUP_MULTIPLIER

    def get_min_balance(self) -> float:
        """Retorna saldo mínimo para iniciar análise (default $0.50)."""
        config = self._load_config()
        try:
            return float(config.get("min_balance_usd", str(DEFAULT_MIN_BALANCE_USD)))
        except (ValueError, TypeError):
            return DEFAULT_MIN_BALANCE_USD

    # ============================================================
    # DETECÇÃO DE ADMIN
    # ============================================================

    @staticmethod
    def _get_admin_emails() -> List[str]:
        """Retorna lista de emails admin do .env (ADMIN_EMAILS)."""
        raw = os.environ.get("ADMIN_EMAILS", "")
        return [e.strip().lower() for e in raw.split(",") if e.strip()]

    def is_admin(self, user_id: str) -> bool:
        """
        Verifica se user_id corresponde a uma conta admin.

        Consulta email do user_id via Supabase auth.users (service_role)
        e compara com ADMIN_EMAILS do .env.
        Resultado é cacheado para evitar queries repetidas.

        Args:
            user_id: UUID do utilizador

        Returns:
            True se é admin, False caso contrário
        """
        # Cache hit
        if user_id in self._admin_cache:
            return self._admin_cache[user_id]

        admin_emails = self._get_admin_emails()
        if not admin_emails:
            self._admin_cache[user_id] = False
            return False

        try:
            # Consultar email do user via auth.users (requer service_role)
            resp = self._sb.auth.admin.get_user_by_id(user_id)
            user_email = ""
            if resp and hasattr(resp, 'user') and resp.user:
                user_email = (resp.user.email or "").lower().strip()
            elif isinstance(resp, dict):
                user_email = (resp.get("email", "") or "").lower().strip()

            is_adm = user_email in admin_emails

            if is_adm:
                logger.info(
                    f"[WALLET-ADMIN] Conta admin detectada: {user_email} "
                    f"(user={user_id[:8]}...)"
                )

            self._admin_cache[user_id] = is_adm
            return is_adm

        except Exception as e:
            logger.warning(
                f"[WALLET] Erro ao verificar admin para {user_id[:8]}...: {e}. "
                f"Assumindo NÃO admin."
            )
            self._admin_cache[user_id] = False
            return False

    # ============================================================
    # ESTIMATIVA DE CUSTO
    # ============================================================

    def estimate_cost(self, num_chars: int) -> Dict[str, float]:
        """
        Estima custo de uma análise baseado no tamanho do documento.

        Args:
            num_chars: Número de caracteres do documento

        Returns:
            {"custo_estimado_apis": float, "custo_estimado_cliente": float}
        """
        markup = self.get_markup_multiplier()

        custo_apis = COST_ESTIMATE_BY_SIZE[-1][1]  # default: maior
        for max_chars, custo in COST_ESTIMATE_BY_SIZE:
            if num_chars <= max_chars:
                custo_apis = custo
                break

        return {
            "custo_estimado_apis": round(custo_apis, 2),
            "custo_estimado_cliente": round(custo_apis * markup, 2),
        }

    # ============================================================
    # CONSULTAR SALDO
    # ============================================================

    def get_balance(self, user_id: str) -> float:
        """
        Retorna saldo actual do utilizador em USD.
        Se não tiver wallet, cria uma com saldo 0 (ou 9999.99 para admin).

        Args:
            user_id: UUID do utilizador

        Returns:
            Saldo em USD (float)
        """
        try:
            resp = (
                self._sb.table("wallet_balances")
                .select("balance_usd")
                .eq("user_id", user_id)
                .execute()
            )

            if not resp.data:
                # Admin recebe saldo generoso, clientes recebem 0
                admin = self.is_admin(user_id)
                initial_balance = ADMIN_INITIAL_BALANCE if admin else 0.0
                self._sb.table("wallet_balances").insert({
                    "user_id": user_id,
                    "balance_usd": initial_balance,
                }).execute()
                tag = "[WALLET-ADMIN]" if admin else "[WALLET]"
                logger.info(f"{tag} Nova wallet criada para {user_id[:8]}... com ${initial_balance:.2f}")
                return initial_balance

            return float(resp.data[0]["balance_usd"])

        except Exception as e:
            logger.error(f"[WALLET] Erro ao consultar saldo: {e}")
            raise

    # ============================================================
    # VERIFICAR SALDO SUFICIENTE
    # ============================================================

    def check_sufficient_balance(
        self,
        user_id: str,
        num_chars: int = 0,
        skip_check: bool = False,
    ) -> Dict[str, Any]:
        """
        Verifica se o utilizador tem saldo suficiente para uma análise.

        Admin NUNCA é bloqueado por saldo insuficiente.

        Args:
            user_id: UUID do utilizador
            num_chars: Número de caracteres do documento (para estimativa)
            skip_check: Se True, ignora verificação (dev/test)

        Returns:
            {"saldo_atual": float, "custo_estimado": float, "suficiente": bool,
             "is_admin": bool}

        Raises:
            InsufficientBalanceError: Se saldo insuficiente (nunca para admin)
        """
        if skip_check:
            logger.info("[WALLET] SKIP_WALLET_CHECK ativo — ignorando verificação")
            return {
                "saldo_atual": 999.99,
                "custo_estimado": 0.0,
                "suficiente": True,
                "is_admin": False,
            }

        # Admin: SEMPRE permitido, sem bloqueio
        if self.is_admin(user_id):
            saldo = self.get_balance(user_id)
            logger.info(
                f"[WALLET-ADMIN] Análise sem verificação de saldo para admin "
                f"(user={user_id[:8]}... saldo=${saldo:.2f})"
            )
            return {
                "saldo_atual": saldo,
                "custo_estimado": 0.0,
                "suficiente": True,
                "is_admin": True,
            }

        saldo = self.get_balance(user_id)
        estimativa = self.estimate_cost(num_chars) if num_chars > 0 else {"custo_estimado_cliente": 0.0}
        custo_estimado = estimativa.get("custo_estimado_cliente", 0.0)

        # Saldo mínimo absoluto
        min_balance = self.get_min_balance()
        necessario = max(min_balance, custo_estimado)

        suficiente = saldo >= necessario

        logger.info(
            f"[WALLET] Saldo check: user={user_id[:8]}... "
            f"saldo=${saldo:.2f} necessário=~${necessario:.2f} "
            f"({'OK' if suficiente else 'INSUFICIENTE'})"
        )

        if not suficiente:
            raise InsufficientBalanceError(
                saldo_atual=saldo,
                saldo_necessario=necessario,
            )

        return {
            "saldo_atual": saldo,
            "custo_estimado": custo_estimado,
            "suficiente": True,
            "is_admin": False,
        }

    # ============================================================
    # DEBITAR (após análise)
    # ============================================================

    def debit(
        self,
        user_id: str,
        cost_real_usd: float,
        run_id: str,
        description: str = "",
    ) -> Dict[str, Any]:
        """
        Debita o custo real da análise da wallet do utilizador.
        Aplica markup (×1.47 por default). Admin paga custo real sem markup.

        Se o saldo actual for menor que o custo_cliente (análise já executada,
        não pode reverter), debita o que existir e loga WARNING.

        Args:
            user_id: UUID do utilizador
            cost_real_usd: Custo real das APIs em USD
            run_id: ID da execução do pipeline
            description: Descrição opcional

        Returns:
            {"custo_real": float, "custo_cliente": float, "saldo_antes": float,
             "saldo_depois": float, "markup": float, "debito_parcial": bool,
             "is_admin": bool}
        """
        admin = self.is_admin(user_id)

        # Admin: markup = 1.0 (custo real, sem margem de lucro)
        if admin:
            markup = 1.0
            logger.info(
                f"[WALLET-ADMIN] Análise sem margem para admin "
                f"(custo real=${cost_real_usd:.4f}, markup=1.0)"
            )
        else:
            markup = self.get_markup_multiplier()

        custo_cliente = round(cost_real_usd * markup, 4)

        saldo_antes = self.get_balance(user_id)
        debito_parcial = False

        # Se saldo < custo: debitar o que existe (análise já foi feita)
        valor_debitar = custo_cliente
        if saldo_antes < custo_cliente:
            if admin:
                # Admin: debitar o custo real mesmo que exceda saldo
                # (conta admin tem saldo alto, mas por segurança logamos)
                logger.warning(
                    f"[WALLET-ADMIN] Saldo admin baixo: "
                    f"${saldo_antes:.2f} < ${custo_cliente:.2f}. "
                    f"Debitando na mesma (é admin)."
                )
            else:
                logger.warning(
                    f"[WALLET] DÉBITO PARCIAL: user={user_id[:8]}... "
                    f"saldo=${saldo_antes:.2f} < custo_cliente=${custo_cliente:.2f}. "
                    f"Debitando ${saldo_antes:.2f} (análise já executada, não reversível)."
                )
                valor_debitar = saldo_antes
                debito_parcial = True

        saldo_depois = round(saldo_antes - valor_debitar, 4)

        # Prefixo da descrição para admin
        desc_prefix = "[ADMIN] " if admin else ""

        try:
            # Actualizar saldo
            self._sb.table("wallet_balances").update({
                "balance_usd": saldo_depois,
                "updated_at": datetime.now().isoformat(),
            }).eq("user_id", user_id).execute()

            # Registar transação
            self._sb.table("wallet_transactions").insert({
                "user_id": user_id,
                "type": "debit",
                "amount_usd": round(valor_debitar, 4),
                "cost_real_usd": round(cost_real_usd, 4),
                "markup_applied": markup,
                "run_id": run_id,
                "description": desc_prefix + (description or f"Análise {run_id}"),
            }).execute()

            lucro = round(valor_debitar - cost_real_usd, 4)

            tag = "[WALLET-ADMIN]" if admin else "[WALLET]"
            logger.info(
                f"{tag} DÉBITO: user={user_id[:8]}... "
                f"real=${cost_real_usd:.4f} × {markup} = ${custo_cliente:.4f} "
                f"(debitado=${valor_debitar:.4f}) "
                f"saldo: ${saldo_antes:.2f} → ${saldo_depois:.2f} "
                f"lucro=${lucro:.4f} "
                f"{'⚠ PARCIAL' if debito_parcial else '✓'}"
            )

            return {
                "custo_real": cost_real_usd,
                "custo_cliente": custo_cliente,
                "valor_debitado": valor_debitar,
                "saldo_antes": saldo_antes,
                "saldo_depois": saldo_depois,
                "markup": markup,
                "debito_parcial": debito_parcial,
                "lucro": lucro,
                "run_id": run_id,
                "is_admin": admin,
            }

        except Exception as e:
            logger.error(f"[WALLET] Erro ao debitar: {e}")
            raise

    # ============================================================
    # CREDITAR (admin)
    # ============================================================

    def credit(
        self,
        user_id: str,
        amount_usd: float,
        description: str = "",
        admin_id: str = "",
    ) -> Dict[str, Any]:
        """
        Credita saldo na wallet do utilizador (operação admin).

        Args:
            user_id: UUID do utilizador
            amount_usd: Valor a creditar em USD
            description: Descrição (ex: "Pagamento MB Way", "Promoção")
            admin_id: UUID do admin que executou a operação

        Returns:
            {"saldo_antes": float, "saldo_depois": float, "creditado": float}
        """
        if amount_usd <= 0:
            raise ValueError("Valor de crédito deve ser positivo.")

        saldo_antes = self.get_balance(user_id)
        saldo_depois = round(saldo_antes + amount_usd, 4)

        try:
            # Actualizar saldo
            self._sb.table("wallet_balances").update({
                "balance_usd": saldo_depois,
                "updated_at": datetime.now().isoformat(),
            }).eq("user_id", user_id).execute()

            # Registar transação
            self._sb.table("wallet_transactions").insert({
                "user_id": user_id,
                "type": "credit",
                "amount_usd": round(amount_usd, 4),
                "cost_real_usd": 0.0,
                "markup_applied": 1.0,
                "run_id": None,
                "description": description or f"Crédito manual{f' por {admin_id[:8]}' if admin_id else ''}",
            }).execute()

            logger.info(
                f"[WALLET] CRÉDITO: user={user_id[:8]}... "
                f"+${amount_usd:.2f} | saldo: ${saldo_antes:.2f} → ${saldo_depois:.2f}"
                f"{f' | admin={admin_id[:8]}...' if admin_id else ''}"
            )

            return {
                "saldo_antes": saldo_antes,
                "saldo_depois": saldo_depois,
                "creditado": amount_usd,
            }

        except Exception as e:
            logger.error(f"[WALLET] Erro ao creditar: {e}")
            raise

    # ============================================================
    # HISTÓRICO DE TRANSAÇÕES
    # ============================================================

    def get_transactions(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
        type_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Retorna histórico de transações do utilizador.

        Args:
            user_id: UUID do utilizador
            limit: Máximo de registos (default 50)
            offset: Offset para paginação
            type_filter: Filtrar por tipo ("debit" ou "credit")

        Returns:
            {"transactions": List[Dict], "total": int, "saldo_atual": float}
        """
        try:
            query = (
                self._sb.table("wallet_transactions")
                .select("*", count="exact")
                .eq("user_id", user_id)
                .order("created_at", desc=True)
                .range(offset, offset + limit - 1)
            )

            if type_filter and type_filter in ("debit", "credit"):
                query = query.eq("type", type_filter)

            resp = query.execute()

            saldo = self.get_balance(user_id)

            return {
                "transactions": resp.data or [],
                "total": resp.count or 0,
                "saldo_atual": saldo,
            }

        except Exception as e:
            logger.error(f"[WALLET] Erro ao consultar transações: {e}")
            raise

    # ============================================================
    # RELATÓRIO DE LUCRO (admin)
    # ============================================================

    def get_profit_report(
        self,
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        Relatório de lucro para admin.

        Args:
            days: Período em dias (default 30)

        Returns:
            {
                "periodo_dias": int,
                "total_debitos": float,
                "total_custo_real": float,
                "total_lucro": float,
                "margem_media_pct": float,
                "total_creditos": float,
                "num_analises": int,
                "num_utilizadores_ativos": int,
                "por_dia": List[Dict],
            }
        """
        try:
            since = (datetime.now() - timedelta(days=days)).isoformat()

            # Buscar todos os débitos no período
            debits_resp = (
                self._sb.table("wallet_transactions")
                .select("amount_usd, cost_real_usd, user_id, created_at")
                .eq("type", "debit")
                .gte("created_at", since)
                .order("created_at", desc=False)
                .execute()
            )

            # Buscar todos os créditos no período
            credits_resp = (
                self._sb.table("wallet_transactions")
                .select("amount_usd")
                .eq("type", "credit")
                .gte("created_at", since)
                .execute()
            )

            debits = debits_resp.data or []
            credits = credits_resp.data or []

            total_debitos = sum(float(d.get("amount_usd", 0)) for d in debits)
            total_custo_real = sum(float(d.get("cost_real_usd", 0)) for d in debits)
            total_creditos = sum(float(c.get("amount_usd", 0)) for c in credits)
            total_lucro = total_debitos - total_custo_real

            users_ativos = len(set(d.get("user_id", "") for d in debits))

            margem_media = (
                ((total_debitos / total_custo_real) - 1.0) * 100
                if total_custo_real > 0
                else 0.0
            )

            # Agrupar por dia
            por_dia: Dict[str, Dict[str, float]] = {}
            for d in debits:
                dia = d.get("created_at", "")[:10]  # "2026-02-08"
                if dia not in por_dia:
                    por_dia[dia] = {"debitos": 0.0, "custo_real": 0.0, "lucro": 0.0, "analises": 0}
                por_dia[dia]["debitos"] += float(d.get("amount_usd", 0))
                por_dia[dia]["custo_real"] += float(d.get("cost_real_usd", 0))
                por_dia[dia]["lucro"] = por_dia[dia]["debitos"] - por_dia[dia]["custo_real"]
                por_dia[dia]["analises"] += 1

            por_dia_list = [
                {"data": k, **{kk: round(vv, 4) for kk, vv in v.items()}}
                for k, v in sorted(por_dia.items())
            ]

            report = {
                "periodo_dias": days,
                "total_debitos": round(total_debitos, 4),
                "total_custo_real": round(total_custo_real, 4),
                "total_lucro": round(total_lucro, 4),
                "margem_media_pct": round(margem_media, 1),
                "total_creditos": round(total_creditos, 4),
                "num_analises": len(debits),
                "num_utilizadores_ativos": users_ativos,
                "por_dia": por_dia_list,
            }

            logger.info(
                f"[WALLET] Relatório lucro ({days}d): "
                f"debitos=${total_debitos:.2f} custo=${total_custo_real:.2f} "
                f"lucro=${total_lucro:.2f} ({margem_media:.1f}%) "
                f"analises={len(debits)} users={users_ativos}"
            )

            return report

        except Exception as e:
            logger.error(f"[WALLET] Erro ao gerar relatório de lucro: {e}")
            raise
