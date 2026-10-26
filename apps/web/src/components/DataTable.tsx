import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useI18n } from "../i18n/I18n";

export type Column<T> = {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  /** The value to sort by: makes the column's header sortable. */
  sort?: (row: T) => string | number | null | undefined;
  align?: "start" | "end" | "center";
  /** The footer cell, such as a total. */
  total?: ReactNode;
  className?: string;
  width?: string;
};

type Sort = { key: string; dir: "asc" | "desc" };

const collator = new Intl.Collator(["ar", "en"], { numeric: true, sensitivity: "base" });

function compare(a: string | number | null | undefined, b: string | number | null | undefined): number {
  if (a == null || a === "") return b == null || b === "" ? 0 : 1;      // empty values last, both ways
  if (b == null || b === "") return -1;
  return typeof a === "number" && typeof b === "number" ? a - b : collator.compare(String(a), String(b));
}

/** A list of records: sortable headers (announced with aria-sort), a sticky header, an optional totals row, and rows
 *  that open the record. Sorting is on the rows given (the page decides what is loaded). */
export function DataTable<T>({ rows, columns, rowKey, label, onRowClick, initialSort, rowClassName, footer }: {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  label: string;
  onRowClick?: (row: T) => void;
  initialSort?: Sort;
  rowClassName?: (row: T) => string;
  footer?: ReactNode;
}) {
  const { t } = useI18n();
  const [sort, setSort] = useState<Sort | null>(initialSort ?? null);
  const sorted = useMemo(() => {
    const col = sort && columns.find((c) => c.key === sort.key);
    if (!col?.sort) return rows;
    const by = col.sort;
    const out = [...rows].sort((a, b) => compare(by(a), by(b)));
    return sort!.dir === "desc" ? out.reverse() : out;
  }, [rows, columns, sort]);
  const hasTotals = columns.some((c) => c.total !== undefined);
  const toggle = (key: string) =>
    setSort((s) => (s?.key !== key ? { key, dir: "asc" } : s.dir === "asc" ? { key, dir: "desc" } : null));

  return (
    <div className="table-wrap data-table">
      <table aria-label={label}>
        <thead>
          <tr>
            {columns.map((c) => {
              const dir = sort?.key === c.key ? sort.dir : null;
              return (
                <th key={c.key} className={`${c.align ?? ""} ${c.className ?? ""}`} style={c.width ? { width: c.width } : undefined}
                  aria-sort={dir ? (dir === "asc" ? "ascending" : "descending") : c.sort ? "none" : undefined}>
                  {c.sort ? (
                    <button type="button" className={`th-sort ${dir ? "on" : ""}`} onClick={() => toggle(c.key)}
                      title={t("table.sort")}>
                      <span>{c.header}</span>
                      {dir === "asc" ? <ArrowUp size={13} aria-hidden /> : dir === "desc" ? <ArrowDown size={13} aria-hidden />
                        : <ArrowUpDown size={13} aria-hidden className="th-sort-idle" />}
                    </button>
                  ) : c.header}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => (
            <tr key={rowKey(r)} className={`${onRowClick ? "clickable" : ""} ${rowClassName?.(r) ?? ""}`}
              onClick={onRowClick ? (e) => {
                // Buttons, links and fields inside a row keep their own job.
                if ((e.target as HTMLElement).closest("a, button, input, select, textarea, label")) return;
                onRowClick(r);
              } : undefined}>
              {columns.map((c) => (
                <td key={c.key} className={`${c.align ?? ""} ${c.className ?? ""}`} data-label={typeof c.header === "string" ? c.header : undefined}>
                  {c.cell(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {hasTotals && (
          <tfoot>
            <tr>
              {columns.map((c) => (
                <td key={c.key} className={`${c.align ?? ""} ${c.className ?? ""} ${c.total === undefined ? "no-total" : ""}`}
                  data-label={typeof c.header === "string" ? c.header : undefined}>
                  {c.total ?? null}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
      {footer}
    </div>
  );
}
