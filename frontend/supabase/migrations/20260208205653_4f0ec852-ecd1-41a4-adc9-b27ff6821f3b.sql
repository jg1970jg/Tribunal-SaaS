
-- Renomear coluna credits para balance
ALTER TABLE public.user_wallets RENAME COLUMN credits TO balance;

-- Atualizar a função para usar 'balance'
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.user_wallets (user_id, balance)
  VALUES (new.id, 3);
  RETURN new;
END;
$$;
