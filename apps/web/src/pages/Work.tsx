import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellOff, Check, CheckCheck, X } from "lucide-react";
import { useState } from "react";
import { api, ApiError, unwrap, type Schemas } from "../api/client";
import { useOrg } from "../auth/Org";
import { StatusBadge, Tabs } from "../components/business";
import { Badge, Button, Card, Checkbox, Empty, PageHeader, QueryView, useToast } from "../components/ui";
import { useI18n } from "../i18n/I18n";
import { today } from "../lib/format";

/** Every page here changes the counts on the home page and in the menu. */
function useRefreshCounts() {
  const { org } = useOrg();
  const qc = useQueryClient();
  return () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: ["tasks", org.id] }),
      qc.invalidateQueries({ queryKey: ["notifications", org.id] }),
      qc.invalidateQueries({ queryKey: ["approvals", org.id] }),
    ]);
}

// ---------------------------------------------------------------- tasks

export function TasksPage() {
  const { t, date } = useI18n();
  const { org, can } = useOrg();
  const refresh = useRefreshCounts();
  const toast = useToast();
  const [status, setStatus] = useState<"open" | "done">("open");
  const [everyone, setEveryone] = useState(false);
  const tasks = useQuery({
    queryKey: ["tasks", org.id, status, everyone],
    queryFn: () =>
      unwrap(api.GET("/api/v1/orgs/{org_id}/tasks", { params: { path: { org_id: org.id }, query: { status_filter: status, all_tasks: everyone } } })),
  });
  // Ticked at once; the task moves to the other tab when the list refreshes.
  const [flipped, setFlipped] = useState<ReadonlySet<string>>(new Set());
  const flip = (id: string, on: boolean) =>
    setFlipped((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  const toggle = useMutation({
    mutationFn: (task: Schemas["TaskOut"]) =>
      unwrap(api.PATCH("/api/v1/orgs/{org_id}/tasks/{task_id}", {
        params: { path: { org_id: org.id, task_id: task.id } }, body: { status: task.status === "open" ? "done" : "open" },
      })),
    onMutate: (task) => flip(task.id, true),
    onError: (e) => toast(e instanceof ApiError ? e.message : t("common.error"), "error"),
    onSettled: async (_data, _error, task) => {
      await refresh(); // the refreshed list carries the real state, so the local tick can go
      flip(task.id, false);
    },
  });
  const now = today();
  return (
    <div className="stack">
      <PageHeader title={t("nav.tasks")} subtitle={t("tasks.subtitle")} />
      <div className="row-between">
        <Tabs<"open" | "done">
          value={status}
          onChange={setStatus}
          tabs={[{ key: "open", label: t("tasks.open") }, { key: "done", label: t("tasks.done") }]}
        />
        {can("workflows.manage") && <Checkbox label={t("tasks.everyone")} checked={everyone} onChange={(e) => setEveryone(e.target.checked)} />}
      </div>
      <QueryView query={tasks}>
        {(list) =>
          list.length === 0 ? (
            <Empty>{status === "open" ? t("tasks.allDone") : t("common.none")}</Empty>
          ) : (
            <ul className="list">
              {list.map((task) => {
                const overdue = task.status === "open" && task.due_date !== null && task.due_date < now;
                return (
                  <li key={task.id} className="list-item">
                    <input
                      type="checkbox"
                      className="task-check"
                      aria-label={`${t("tasks.markDone")}: ${task.title}`}
                      checked={(task.status === "done") !== flipped.has(task.id)}
                      disabled={flipped.has(task.id)}
                      onChange={() => toggle.mutate(task)}
                    />
                    <div className="grow stack-sm" style={{ gap: 2 }}>
                      <span className={task.status === "done" ? "muted" : ""} style={task.status === "done" ? { textDecoration: "line-through" } : undefined}>
                        {task.title}
                      </span>
                      {task.description && <span className="muted small">{task.description}</span>}
                    </div>
                    {task.source.startsWith("workflow") && <Badge tone="primary">{t("tasks.fromWorkflow")}</Badge>}
                    {task.due_date && (
                      <Badge tone={overdue ? "danger" : "neutral"}>
                        {overdue ? t("tasks.overdue") : t("tasks.due")} {date(task.due_date)}
                      </Badge>
                    )}
                  </li>
                );
              })}
            </ul>
          )
        }
      </QueryView>
    </div>
  );
}

// ---------------------------------------------------------------- notifications

export function NotificationsPage() {
  const { t, date } = useI18n();
  const { org } = useOrg();
  const refresh = useRefreshCounts();
  const notes = useQuery({
    queryKey: ["notifications", org.id, "all"],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/notifications", { params: { path: { org_id: org.id } } })),
  });
  const read = useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/api/v1/orgs/{org_id}/notifications/{notification_id}/read", { params: { path: { org_id: org.id, notification_id: id } } })),
    onSuccess: refresh,
  });
  const readAll = useMutation({
    mutationFn: async () => {
      for (const n of notes.data?.filter((x) => !x.read) ?? []) {
        await unwrap(api.POST("/api/v1/orgs/{org_id}/notifications/{notification_id}/read", { params: { path: { org_id: org.id, notification_id: n.id } } }));
      }
    },
    onSettled: refresh,
  });
  const unread = notes.data?.filter((n) => !n.read).length ?? 0;
  return (
    <div className="stack">
      <PageHeader
        title={t("nav.notifications")}
        actions={unread > 0 && (
          <Button icon={<CheckCheck size={16} />} loading={readAll.isPending} onClick={() => readAll.mutate()}>
            {t("notifications.readAll")}
          </Button>
        )}
      />
      <QueryView query={notes}>
        {(list) =>
          list.length === 0 ? (
            <Empty>
              <BellOff size={28} aria-hidden /> <br />
              {t("notifications.none")}
            </Empty>
          ) : (
            <ul className="list">
              {list.map((n) => (
                <li key={n.id} className={`list-item ${n.read ? "" : "unread"}`}>
                  <span className="dot" aria-hidden />
                  <div className="grow stack-sm" style={{ gap: 2 }}>
                    <strong dir="auto">{n.title}</strong>
                    {n.body && <span className="muted small" dir="auto">{n.body}</span>}
                  </div>
                  <span className="muted small num">{date(n.created_at)}</span>
                  {!n.read && (
                    <Button size="sm" variant="ghost" onClick={() => read.mutate(n.id)}>
                      {t("notifications.markRead")}
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )
        }
      </QueryView>
    </div>
  );
}

// ---------------------------------------------------------------- approvals

export function ApprovalsPage() {
  const { t, date } = useI18n();
  const { org } = useOrg();
  const refresh = useRefreshCounts();
  const toast = useToast();
  const approvals = useQuery({
    queryKey: ["approvals", org.id],
    queryFn: () => unwrap(api.GET("/api/v1/orgs/{org_id}/approvals", { params: { path: { org_id: org.id } } })),
  });
  const decide = useMutation({
    mutationFn: ({ id, approve }: { id: string; approve: boolean }) =>
      unwrap(api.POST(approve ? "/api/v1/orgs/{org_id}/workflow-runs/{run_id}/approve" : "/api/v1/orgs/{org_id}/workflow-runs/{run_id}/reject", {
        params: { path: { org_id: org.id, run_id: id } },
      })),
    onSuccess: (run) => {
      toast(run.status === "failed" || run.status === "rejected" ? t("approvals.rejectedToast") : t("approvals.approvedToast"));
      refresh();
    },
    onError: (e) => toast(e instanceof ApiError ? e.message : t("common.error"), "error"),
  });
  return (
    <div className="stack">
      <PageHeader title={t("nav.approvals")} subtitle={t("approvals.subtitle")} />
      <QueryView query={approvals}>
        {(list) =>
          list.length === 0 ? (
            <Empty>{t("approvals.none")}</Empty>
          ) : (
            <div className="stack-sm">
              {list.map((run) => {
                const step = run.steps.find((s) => s.status === "waiting_approval");
                const message = typeof step?.result.message === "string" ? step.result.message : "";
                return (
                  <Card key={run.id} tight>
                    <div className="row-between">
                      <div className="stack-sm" style={{ gap: 2 }}>
                        <span className="row">
                          <strong>{run.workflow_name}</strong>
                          <StatusBadge status={run.status} />
                        </span>
                        {message && <span dir="auto">{message}</span>}
                        <span className="muted small">
                          {t("approvals.needs")} <code>{String(step?.result.permission ?? "")}</code> · <span className="num">{date(run.created_at)}</span>
                        </span>
                      </div>
                      <div className="row">
                        <Button variant="primary" icon={<Check size={16} />} loading={decide.isPending && decide.variables?.id === run.id && decide.variables.approve}
                          onClick={() => decide.mutate({ id: run.id, approve: true })}>
                          {t("assistant.approve")}
                        </Button>
                        <Button icon={<X size={16} />} loading={decide.isPending && decide.variables?.id === run.id && !decide.variables.approve}
                          onClick={() => decide.mutate({ id: run.id, approve: false })}>
                          {t("assistant.reject")}
                        </Button>
                      </div>
                    </div>
                  </Card>
                );
              })}
            </div>
          )
        }
      </QueryView>
    </div>
  );
}
