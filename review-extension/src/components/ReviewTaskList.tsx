import type { ReviewTask } from "../types/review";
import { isFieldFirstTaskVisible } from "../reviewSteps";

/** Generic renderer for backend routed tasks; it contains no business rules. */
export function ReviewTaskList({ tasks }: { tasks: readonly ReviewTask[] }) {
  const visibleTasks = tasks.filter(isFieldFirstTaskVisible);
  if (!visibleTasks.length) return null;
  return (
    <section className="result-card review-task-list">
      <div className="group-heading"><h2>审核任务</h2><small>{visibleTasks.length} 项</small></div>
      {visibleTasks.map((task) => (
        <article className={`check-row status-${task.result_status.toLowerCase()}`} key={task.step_id}>
          <span>{task.sequence}. {task.label}</span>
          <strong>{task.result_status === "MATCH" ? "通过" : "待处理"}</strong>
          <p className="muted">{task.reason}</p>
        </article>
      ))}
    </section>
  );
}

