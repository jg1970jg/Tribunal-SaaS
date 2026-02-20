
-- Tabela de créditos
CREATE TABLE IF NOT EXISTS public.user_wallets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE NOT NULL UNIQUE,
  credits INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- RLS para user_wallets
ALTER TABLE public.user_wallets ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Ver meus creditos" ON public.user_wallets
  FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Atualizar meus creditos" ON public.user_wallets
  FOR UPDATE USING (auth.uid() = user_id);

-- Função para dar 3 créditos a novos utilizadores
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.user_wallets (user_id, credits)
  VALUES (new.id, 3);
  RETURN new;
END;
$$;

-- Trigger no registo de novos utilizadores
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW
  EXECUTE FUNCTION public.handle_new_user();
