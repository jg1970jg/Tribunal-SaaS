
-- Drop existing RESTRICTIVE policies
DROP POLICY "Users can view own documents" ON public.documents;
DROP POLICY "Users can insert own documents" ON public.documents;
DROP POLICY "Users can update own documents" ON public.documents;
DROP POLICY "Users can delete own documents" ON public.documents;

-- Recreate as PERMISSIVE
CREATE POLICY "Users can view own documents" ON public.documents FOR SELECT TO authenticated USING (auth.uid() = user_id);
CREATE POLICY "Users can insert own documents" ON public.documents FOR INSERT TO authenticated WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can update own documents" ON public.documents FOR UPDATE TO authenticated USING (auth.uid() = user_id);
CREATE POLICY "Users can delete own documents" ON public.documents FOR DELETE TO authenticated USING (auth.uid() = user_id);
