import { css } from "@codemirror/lang-css";
import { html } from "@codemirror/lang-html";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { Compartment, EditorState, type Extension } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { useEffect, useRef } from "react";

function languageFor(path: string): Extension {
  const ext = path.split(".").pop()?.toLowerCase();
  if (ext === "html" || ext === "htm") return html();
  if (ext === "css") return css();
  if (ext === "js" || ext === "mjs") return javascript();
  if (ext === "json") return json();
  return [];
}

/** Colours come from the app's tokens, so the editor follows light and dark mode. */
const theme = EditorView.theme({
  "&": { height: "100%", fontSize: "13px", backgroundColor: "transparent", color: "var(--color-text)" },
  ".cm-scroller": { fontFamily: 'ui-monospace, "JetBrains Mono", Menlo, monospace', lineHeight: "1.6" },
  ".cm-gutters": { backgroundColor: "transparent", color: "var(--color-text-muted)", border: "none" },
  ".cm-activeLine": { backgroundColor: "color-mix(in srgb, var(--color-primary-subtle) 45%, transparent)" },
  ".cm-activeLineGutter": { backgroundColor: "transparent", color: "var(--color-text)" },
  ".cm-cursor": { borderLeftColor: "var(--color-text)" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": {
    backgroundColor: "color-mix(in srgb, var(--color-primary) 28%, transparent)",
  },
  ".cm-tooltip": { backgroundColor: "var(--color-surface)", border: "1px solid var(--color-border)" },
});

/**
 * Code editor with syntax highlighting, search, bracket matching and autocompletion.
 * `value` replaces the document when it changes from outside (e.g. switching files).
 */
export function CodeEditor({ path, value, onChange, onSave, readOnly, label }: {
  path: string; value: string; onChange: (v: string) => void; onSave?: () => void; readOnly?: boolean; label: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const language = useRef(new Compartment());
  const editable = useRef(new Compartment());
  const callbacks = useRef({ onChange, onSave });
  callbacks.current = { onChange, onSave };

  useEffect(() => {
    const v = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          theme,
          EditorView.lineWrapping,
          EditorView.contentAttributes.of({ "aria-label": label, dir: "ltr" }),
          language.current.of(languageFor(path)),
          editable.current.of(EditorState.readOnly.of(Boolean(readOnly))),
          keymap.of([{ key: "Mod-s", preventDefault: true, run: () => (callbacks.current.onSave?.(), true) }]),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) callbacks.current.onChange(u.state.doc.toString());
          }),
        ],
      }),
    });
    view.current = v;
    return () => v.destroy();
    // created once; later changes go through the effects below
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const v = view.current;
    if (!v) return;
    v.dispatch({ effects: language.current.reconfigure(languageFor(path)) });
  }, [path]);

  useEffect(() => {
    view.current?.dispatch({ effects: editable.current.reconfigure(EditorState.readOnly.of(Boolean(readOnly))) });
  }, [readOnly]);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) {
      v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
    }
  }, [value]);

  return <div ref={host} className="code-editor" dir="ltr" />;
}
