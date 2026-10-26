import { createContext, useContext, useEffect } from "react";

/** The page's own title, shown after the app in the top bar's breadcrumbs (and in the browser tab). */
export const CrumbContext = createContext<(title: string | null) => void>(() => {});

export function usePageTitle(title: string | undefined) {
  const set = useContext(CrumbContext);
  useEffect(() => {
    if (!title) return;
    set(title);
    return () => set(null);
  }, [title, set]);
}
