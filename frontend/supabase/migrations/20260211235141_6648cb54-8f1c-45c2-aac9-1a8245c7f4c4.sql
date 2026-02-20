
-- Deny all direct INSERT on user_wallets from clients.
-- Wallet creation is handled by the handle_new_user() SECURITY DEFINER trigger,
-- which bypasses RLS, so this policy won't affect it.
CREATE POLICY "No direct insert on user_wallets"
ON public.user_wallets
FOR INSERT
TO authenticated
WITH CHECK (false);

-- Also block anon inserts
CREATE POLICY "No anon insert on user_wallets"
ON public.user_wallets
FOR INSERT
TO anon
WITH CHECK (false);
