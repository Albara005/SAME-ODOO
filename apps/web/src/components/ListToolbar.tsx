import { Search, X } from "lucide-react";
import type { ReactNode } from "react";
import { useI18n } from "../i18n/I18n";

export type Filter<K extends string> = { key: K; label: string; count?: number };

/** The bar above a list: search, quick filters (one at a time) and how many records are shown. */
export function ListToolbar<K extends string>({ search, onSearch, placeholder, filters, filter, onFilter, filterLabel, shown, extra }: {
  search?: string;
  onSearch?: (q: string) => void;
  placeholder?: string;
  filters?: Filter<K>[];
  filter?: K;
  onFilter?: (k: K) => void;
  filterLabel?: string;
  shown?: number;
  extra?: ReactNode;
}) {
  const { t } = useI18n();
  return (
    <div className="list-toolbar">
      {onSearch && (
        <div className="search-box">
          <Search size={16} aria-hidden className="search-box-icon" />
          <input
            type="search" className="input" value={search ?? ""} onChange={(e) => onSearch(e.target.value)}
            placeholder={placeholder ?? t("common.search")} aria-label={placeholder ?? t("common.search")}
          />
          {search && (
            <button type="button" className="search-box-clear" aria-label={t("common.clear")} onClick={() => onSearch("")}>
              <X size={14} aria-hidden />
            </button>
          )}
        </div>
      )}
      {filters && onFilter && (
        <div className="chips" role="group" aria-label={filterLabel ?? t("common.status")}>
          {filters.map((f) => (
            <button key={f.key} type="button" className={`chip ${f.key === filter ? "on" : ""}`} aria-pressed={f.key === filter}
              onClick={() => onFilter(f.key)}>
              {f.label}
              {f.count !== undefined && <span className="chip-count num">{f.count}</span>}
            </button>
          ))}
        </div>
      )}
      <span className="grow" />
      {extra}
      {shown !== undefined && <span className="list-count muted small" role="status">{t("common.shown", { n: shown })}</span>}
    </div>
  );
}
