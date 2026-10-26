import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

export type MenuItem =
  | { kind: "link"; label: string; to: string; icon?: ReactNode }
  | { kind: "action"; label: string; onSelect: () => void; icon?: ReactNode; danger?: boolean }
  | { kind: "separator" }
  | { kind: "label"; label: string };

/** A button that opens a list of links and actions (WAI-ARIA menu button): arrows move, Escape closes and returns focus. */
export function MenuButton({ label, button, items, align = "end", className = "" }: {
  label: string; button: ReactNode; items: MenuItem[]; align?: "start" | "end"; className?: string;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  const focusables = () => Array.from(menu.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? []);
  useEffect(() => {
    if (!open) return;
    focusables()[0]?.focus();
    const away = (e: MouseEvent) => {
      if (!menu.current?.contains(e.target as Node) && !trigger.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };
  const onKey = (e: React.KeyboardEvent) => {
    const list = focusables();
    const at = list.indexOf(document.activeElement as HTMLElement);
    if (e.key === "ArrowDown") { e.preventDefault(); list[(at + 1) % list.length]?.focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); list[(at - 1 + list.length) % list.length]?.focus(); }
    else if (e.key === "Home") { e.preventDefault(); list[0]?.focus(); }
    else if (e.key === "End") { e.preventDefault(); list[list.length - 1]?.focus(); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
    else if (e.key === "Tab") close(false);
  };

  return (
    <div className={`menu-wrap ${className}`}>
      <button
        ref={trigger} type="button" className="btn btn-ghost icon-btn menu-trigger" aria-label={label} title={label}
        aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(!open)}
        onKeyDown={(e) => { if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); } }}
      >
        {button}
      </button>
      {open && (
        <div ref={menu} id={id} role="menu" aria-label={label} className={`menu menu-${align}`} onKeyDown={onKey}>
          {items.map((item, i) => {
            if (item.kind === "separator") return <div key={i} role="separator" className="menu-sep" />;
            if (item.kind === "label") return <div key={i} className="menu-label" role="presentation">{item.label}</div>;
            if (item.kind === "link") {
              return (
                <Link key={i} role="menuitem" tabIndex={-1} to={item.to} className="menu-item" onClick={() => close(false)}>
                  {item.icon}<span>{item.label}</span>
                </Link>
              );
            }
            return (
              <button key={i} type="button" role="menuitem" tabIndex={-1} className={`menu-item ${item.danger ? "danger" : ""}`}
                onClick={() => { close(false); item.onSelect(); }}>
                {item.icon}<span>{item.label}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
