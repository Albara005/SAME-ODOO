import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, MessageSquarePlus, NotebookPen, Send, Trash2, Wrench, X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { StarMark } from "../components/Art";
import { StatusBadge } from "../components/business";
import { Badge, Button, Checkbox, Dialog, Empty, ErrorNotice, IconButton, QueryView, useToast } from "../components/ui";
import type { MessageKey } from "../i18n/en";
import { useI18n } from "../i18n/I18n";
import { streamTurn } from "../lib/assistantStream";
import { Markdown } from "../lib/markdown";

type Action = Schemas["ActionOut"];

/** Assistant chat. Read tools run immediately; anything that writes waits for the user's approval. */
export function AssistantPage() {
  const { t, date } = useI18n();
  const { org, path } = useOrg();
  const { conversationId } = useParams();
  const navigate = useNavigate();
  const conversations = useQuery({
    queryKey: ["conversations", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/assistant/conversations", { params: { path: { org_id: org.id } } })),
  });
  const [notesOpen, setNotesOpen] = useState(false);
  // The message being typed lives here: the chat below starts afresh for each conversation, and the
  // first reply in a new one moves it to its own address while the next message may already be typed.
  const [draft, setDraft] = useState("");

  return (
    <div className="assistant">
      <aside className="assistant-list" aria-label={t("assistant.history")}>
        <Button variant="primary" icon={<MessageSquarePlus size={16} />} onClick={() => navigate(path("assistant"))}>
          {t("assistant.new")}
        </Button>
        <QueryView query={conversations}>
          {(list) =>
            list.length === 0 ? (
              <p className="muted small">{t("assistant.noHistory")}</p>
            ) : (
              <nav className="stack-sm" style={{ gap: 2 }}>
                {list.map((c) => (
                  <button
                    key={c.id}
                    type="button"
                    className={`conv-link ${c.id === conversationId ? "active" : ""}`}
                    aria-current={c.id === conversationId ? "page" : undefined}
                    onClick={() => navigate(path(`assistant/${c.id}`))}
                  >
                    <span className="conv-title">{c.title || t("assistant.untitled")}</span>
                    <span className="muted small num">{date(c.created_at)}</span>
                  </button>
                ))}
              </nav>
            )
          }
        </QueryView>
        <Button size="sm" variant="ghost" icon={<NotebookPen size={16} />} onClick={() => setNotesOpen(true)}>
          {t("memory.title")}
        </Button>
      </aside>
      <Chat key={conversationId ?? "new"} conversationId={conversationId} text={draft} setText={setDraft} />
      {notesOpen && <NotesDialog onClose={() => setNotesOpen(false)} />}
    </div>
  );
}

/** What the assistant remembers for this user: each note was approved by them, and only their assistant sees it. */
function NotesDialog({ onClose }: { onClose: () => void }) {
  const { t, date } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const notes = useQuery({
    queryKey: ["memories", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/assistant/memories", { params: { path: { org_id: org.id } } })),
  });
  const forget = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/api/v1/orgs/{org_id}/assistant/memories/{memory_id}", { params: { path: { org_id: org.id, memory_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["memories", org.id] }),
  });
  return (
    <Dialog open onClose={onClose} title={t("memory.title")}>
      <div className="stack">
        <p className="muted small">{t("memory.about")}</p>
        <QueryView query={notes}>
          {(list) => list.length === 0 ? <Empty>{t("memory.empty")}</Empty> : (
            <ul className="memory-list">
              {list.map((n) => (
                <li key={n.id}>
                  <span className="grow" dir="auto">{n.text}</span>
                  <span className="muted small num">{date(n.created_at)}</span>
                  <IconButton label={`${t("memory.forget")}: ${n.text}`} onClick={() => forget.mutate(n.id)}>
                    <Trash2 size={16} />
                  </IconButton>
                </li>
              ))}
            </ul>
          )}
        </QueryView>
      </div>
    </Dialog>
  );
}

function Chat({ conversationId, text, setText }: { conversationId?: string; text: string; setText: (text: string) => void }) {
  const { t } = useI18n();
  const { org, path } = useOrg();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [deep, setDeep] = useState(false);
  const [sending, setSending] = useState<string | null>(null); // the message on its way, shown at once
  const [live, setLive] = useState(""); // the reply as it is being written
  const [tools, setTools] = useState<string[]>([]); // tools used so far in this turn
  const endRef = useRef<HTMLDivElement>(null);

  const detail = useQuery({
    queryKey: ["conversation", org.id, conversationId],
    queryFn: () =>
      unwrap(api.GET("/api/v1/orgs/{org_id}/assistant/conversations/{conversation_id}", {
        params: { path: { org_id: org.id, conversation_id: conversationId! } },
      })),
    enabled: Boolean(conversationId),
  });

  const send = useMutation({
    mutationFn: async ({ content, deep }: { content: string; deep: boolean }) => {
      let id = conversationId;
      if (!id) {
        const conv = await unwrap(api.POST("/api/v1/orgs/{org_id}/assistant/conversations", {
          params: { path: { org_id: org.id } }, body: { title: content.slice(0, 80) },
        }));
        id = conv.id;
      }
      const turn = await streamTurn(org.id, id, { content, deep }, {
        onDelta: (piece) => setLive((so) => so + piece),
        onTool: (name) => setTools((so) => (so.includes(name) ? so : [...so, name])),
      });
      return { id, turn };
    },
    onSuccess: async ({ id }) => {
      await qc.invalidateQueries({ queryKey: ["conversation", org.id, id] });
      void qc.invalidateQueries({ queryKey: ["conversations", org.id] });
      void qc.invalidateQueries({ queryKey: ["memories", org.id] });
      if (id !== conversationId) navigate(path(`assistant/${id}`), { replace: true });
    },
    onSettled: () => {
      setSending(null);
      setLive("");
      setTools([]);
    },
  });

  const messages = detail.data?.messages ?? [];
  const actions = (detail.data?.actions ?? []).filter((a) => a.kind === "write");
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });   // not returned: newer browsers give back a promise
  }, [messages.length, actions.length, sending, live]);

  function submit(e?: FormEvent) {
    e?.preventDefault();
    const content = text.trim();
    if (!content || send.isPending) return;
    setSending(content);
    setText("");
    send.mutate({ content, deep });
  }
  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit();
  }

  const unavailable = send.error instanceof ApiError && (send.error.status === 403 || send.error.status === 402);
  return (
    <section className="chat" aria-label={t("nav.assistant")}>
      <div className="chat-thread" aria-live="polite">
        {conversationId && detail.isPending && <QueryView query={detail}>{() => null}</QueryView>}
        {detail.isError && <ErrorNotice error={detail.error} />}
        {!conversationId && !sending && <Welcome onPick={(q) => setText(q)} />}
        {messages.map((m, i) => (
          <Bubble key={i} role={m.role} text={m.text} />
        ))}
        {sending && <Bubble role="user" text={sending} />}
        {send.isPending && tools.length > 0 && (
          <div className="tool-trail small muted" role="status">
            <Wrench size={14} aria-hidden /> {t("assistant.usedTools", { tools: tools.map((n) => toolLabel(t, n)).join("، ") })}
          </div>
        )}
        {send.isPending && (live ? (
          <Bubble role="assistant" text={live} streaming />
        ) : (
          <div className="bubble bubble-assistant thinking" role="status">
            <StarMark size={18} /> {t("assistant.thinking")}
          </div>
        ))}
        {actions.length > 0 && (
          <div className="stack-sm">
            {actions.map((a) => (
              <ActionCard key={a.id} action={a} conversationId={conversationId!} />
            ))}
          </div>
        )}
        {send.isError &&
          (unavailable ? (
            <div className="notice notice-error" role="alert">
              {(send.error as ApiError).status === 402 ? t("assistant.overBudget") : t("assistant.off")}
            </div>
          ) : (
            <ErrorNotice error={send.error} />
          ))}
        <div ref={endRef} />
      </div>
      <form className="composer" onSubmit={submit}>
        <textarea
          className="textarea"
          aria-label={t("assistant.message")}
          placeholder={t("assistant.placeholder")}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          rows={2}
          maxLength={8000}
          disabled={unavailable}
        />
        <div className="row-between">
          <Checkbox label={t("assistant.deep")} checked={deep} onChange={(e) => setDeep(e.target.checked)} />
          <Button type="submit" variant="primary" icon={<Send size={16} />} loading={send.isPending} disabled={!text.trim() || unavailable}>
            {t("assistant.send")}
          </Button>
        </div>
        <p className="muted small">{t("assistant.safety")}</p>
      </form>
    </section>
  );
}

function Welcome({ onPick }: { onPick: (q: string) => void }) {
  const { t } = useI18n();
  const ideas: MessageKey[] = ["assistant.idea1", "assistant.idea2", "assistant.idea3"];
  return (
    <div className="chat-welcome">
      <StarMark size={44} />
      <h1>{t("assistant.welcome")}</h1>
      <p className="muted">{t("assistant.welcomeSub")}</p>
      <div className="row" style={{ justifyContent: "center" }}>
        {ideas.map((k) => (
          <button key={k} type="button" className="idea" onClick={() => onPick(t(k))}>
            {t(k)}
          </button>
        ))}
      </div>
    </div>
  );
}

/** A tool's name in the reader's language, when the app knows it. */
function toolLabel(t: (k: MessageKey) => string, name: string): string {
  const key = `tool.${name}` as MessageKey;
  return t(key) === key ? name : t(key);
}

function Bubble({ role, text, streaming }: { role: string; text: string; streaming?: boolean }) {
  return (
    <div className={`bubble ${role === "user" ? "bubble-user" : "bubble-assistant"} ${streaming ? "streaming" : ""}`}
      aria-busy={streaming || undefined}>
      {role !== "user" && <StarMark size={18} />}
      <div className="bubble-text" dir="auto">
        {role === "user" ? text : <Markdown text={text} />}
      </div>
    </div>
  );
}

const TOOL_LABEL: Record<string, MessageKey> = {
  create_customer: "assistant.tool.create_customer",
  create_draft_invoice: "assistant.tool.create_draft_invoice",
  create_quotation: "assistant.tool.create_quotation",
  register_payment: "assistant.tool.register_payment",
  remember: "assistant.tool.remember",
};

function ActionCard({ action: a, conversationId }: { action: Action; conversationId: string }) {
  const { t } = useI18n();
  const { org } = useOrg();
  const qc = useQueryClient();
  const toast = useToast();
  const decide = useMutation({
    mutationFn: (approve: boolean) =>
      unwrap(api.POST(approve ? "/api/v1/orgs/{org_id}/assistant/actions/{action_id}/approve" : "/api/v1/orgs/{org_id}/assistant/actions/{action_id}/reject", {
        params: { path: { org_id: org.id, action_id: a.id } },
      })),
    onSuccess: (done) => {
      toast(done.status === "approved" ? t("assistant.done") : done.status === "rejected" ? t("assistant.rejected") : t("assistant.failed"),
        done.status === "approval_failed" ? "error" : "info");
      void qc.invalidateQueries({ queryKey: ["conversation", org.id, conversationId] });
      void qc.invalidateQueries({ queryKey: ["customers", org.id] });
      void qc.invalidateQueries({ queryKey: ["invoices", org.id] });
      void qc.invalidateQueries({ queryKey: ["orders", org.id] });
      void qc.invalidateQueries({ queryKey: ["memories", org.id] });
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : t("common.error"), "error"),
  });
  const pending = a.status === "pending_approval";
  const label = TOOL_LABEL[a.name];
  const error = a.status === "approval_failed" && a.result && !Array.isArray(a.result) ? String((a.result as { error?: unknown }).error ?? "") : "";
  return (
    <article className={`action-card ${pending ? "pending" : ""}`}>
      <div className="row-between">
        <strong>{label ? t(label) : a.name}</strong>
        {pending ? <Badge tone="warning">{t("assistant.needsApproval")}</Badge> : <StatusBadge status={a.status} />}
      </div>
      <dl className="kv small">
        {Object.entries(a.arguments).map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <dt className="mono">{k}</dt>
            <dd dir="auto">{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
          </div>
        ))}
      </dl>
      {error && <p className="small" style={{ color: "var(--color-danger-text)" }}>{error}</p>}
      {pending && (
        <div className="row">
          <Button variant="primary" size="sm" icon={<Check size={16} />} loading={decide.isPending && decide.variables === true} onClick={() => decide.mutate(true)}>
            {t("assistant.approve")}
          </Button>
          <Button size="sm" icon={<X size={16} />} loading={decide.isPending && decide.variables === false} onClick={() => decide.mutate(false)}>
            {t("assistant.reject")}
          </Button>
        </div>
      )}
    </article>
  );
}
