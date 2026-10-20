import type { PageData, PageActionIntent } from "../../shared/contracts/review";

export type PageActionExecutor = (intent: PageActionIntent, page: PageData) => Promise<string>;

/**
 * 前端页面动作目录：按后端下发的稳定 `action_id` 找到执行器。
 *
 * 注意这里只做「有没有注册执行器」的判定，不做页面身份校验——身份、白名单、
 * 控件唯一性、原值校验和回读回滚都在写回器（`page-field-writer.ts`）里，
 * 每个执行器自己负责调用。
 */
export class PageActionRegistry {
  private readonly executors = new Map<string, PageActionExecutor>();

  public register(actionId: string, executor: PageActionExecutor): void {
    if (this.executors.has(actionId)) throw new Error(`页面动作已注册：${actionId}`);
    this.executors.set(actionId, executor);
  }

  public async executeAll(actions: readonly PageActionIntent[], page: PageData): Promise<string[]> {
    const messages: string[] = [];
    const executed = new Set<string>();
    for (const action of actions) {
      const executor = this.executors.get(action.action_id);
      if (!executor) {
        // 后端新增了动作、前端还没注册执行器时，以前是静默跳过：页面上什么都
        // 没发生，也没有任何提示。「新增业务前端零改动」的破口就在这里，
        // 所以至少要变成审核员看得见的一句话。
        messages.push(`页面动作「${action.action_id}」尚未在前端注册，已跳过`);
        continue;
      }
      if (action.requires_authorization !== false) {
        messages.push(`页面动作「${action.action_id}」需要操作人员确认，未自动执行`);
        continue;
      }
      if (executed.has(action.action_id)) continue;
      executed.add(action.action_id);
      try {
        messages.push(await executor(action, page));
      } catch {
        messages.push(`页面动作「${action.action_id}」未能执行，请在原页面人工操作`);
      }
    }
    return messages;
  }
}

