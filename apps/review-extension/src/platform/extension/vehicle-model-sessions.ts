import { validatedModelInput } from "../../shared/vehicle-model.ts";
import { vehicleModelMessage } from "./vehicle-model-client.ts";
import type { ModelSearchInput, VehicleModelCandidate } from "../../shared/contracts/vehicle-model.ts";
import { ModelPageError, supportedModelPage } from "./vehicle-model-page.ts";
import type { VerifyModelPage } from "./vehicle-model-page.ts";
import { createRandomId } from "../../shared/random-id.ts";

interface Context {
  sourceTabId: number; expectedPageUrl: string; expectedPageInstanceId: string;
  expectedPageFingerprint: string; expectedCollectionId: string;
}
interface Session { id: string; context: Context; input: ModelSearchInput; candidates: VehicleModelCandidate[] | null; }
interface Store {
  get: (key: string) => Promise<Record<string, unknown>>;
  set: (values: Record<string, unknown>) => Promise<void>;
}
interface Message extends Partial<Context> {type?: string; input?: ModelSearchInput; searchId?: string; modelId?: string;}
function contextFrom(message: Message): Context {
  if (!Number.isInteger(message.sourceTabId) || (message.sourceTabId ?? -1) < 0
    || !supportedModelPage(message.expectedPageUrl || "")
    || ![message.expectedPageInstanceId, message.expectedPageFingerprint, message.expectedCollectionId]
      .every(value => typeof value === "string" && value.trim())) throw new Error("审核页面身份不完整，请重新读取页面");
  return {sourceTabId: message.sourceTabId!, expectedPageUrl: message.expectedPageUrl!,
    expectedPageInstanceId: message.expectedPageInstanceId!, expectedPageFingerprint: message.expectedPageFingerprint!,
    expectedCollectionId: message.expectedCollectionId!};
}

/** 后台持有可信候选，session storage 在 MV3 worker 休眠后仍可供回填验证。 */
export function createModelSessionHandler(extensionId: string, store: Store,
  query: (input: ModelSearchInput) => Promise<{ok: boolean; message?: string; candidates?: VehicleModelCandidate[]}>,
  verifyPage: VerifyModelPage,
  makeId = createRandomId) {
  // 串行存储操作，避免迟到请求覆盖较新的查询会话。
  let storageQueue: Promise<unknown> = Promise.resolve();
  const stored = <T>(action: () => Promise<T>) => {
    const result = storageQueue.then(action).catch(() => { throw new Error("车型查询会话保存或读取失败，请重新搜索；仍失败请重新加载扩展"); });
    storageQueue = result.catch(() => {});
    return result;
  };
  const keyFor = (tab: number) => `reviewVehicleModel:${tab}`;
  return async (message: Message, sender: chrome.runtime.MessageSender) => {
    const failure = (text: string) => ({ok: false, message: text, candidates: []});
    try {
      if (sender.id !== extensionId) return failure("车型查询发送方无效");
      if (message.type === "SEARCH_REVIEW_VEHICLE_MODELS") {
        const url = new URL(sender.url || "");
        if (sender.tab || url.protocol !== "chrome-extension:" || url.hostname !== extensionId || url.pathname !== "/index.html") return failure("请从审核侧栏查询车型");
        const context = contextFrom(message), input = validatedModelInput(message.input!);
        const key = keyFor(context.sourceTabId);
        const session: Session = {id: makeId(), context, input, candidates: null};
        await stored(() => store.set({[key]: session}));
        const result = await query(input);
        return await stored(async () => {
          const current = (await store.get(key))[key] as Session | undefined;
          if (current?.id !== session.id) return failure("车型查询已过期，请使用最新搜索结果");
          if (!result.ok) return failure(result.message || "车型查询失败，请重试");
          if (!Array.isArray(result.candidates)) return failure("车型查询响应不完整，请重试");
          session.candidates = result.candidates;
          await store.set({[key]: session});
          return {ok: true, searchId: session.id, candidates: session.candidates};
        });
      }
      if (message.type === "RESOLVE_REVIEW_MODEL_SEARCH") {
        const tabId = await verifyPage(sender, message.expectedPageUrl || "");
        const context = contextFrom({...message, sourceTabId: tabId});
        return await stored(async () => {
          const session = (await store.get(keyFor(context.sourceTabId)))[keyFor(context.sourceTabId)] as Session | undefined;
          if (!session || session.id !== message.searchId) return {...failure("已有更新的搜索或查询会话已清除，请重新搜索并选择"), code: "MODEL_SEARCH_CHANGED"};
          if (session.context.sourceTabId !== context.sourceTabId
            || session.context.expectedPageUrl !== context.expectedPageUrl
            || session.context.expectedPageInstanceId !== context.expectedPageInstanceId
            || session.context.expectedPageFingerprint !== context.expectedPageFingerprint
            || session.context.expectedCollectionId !== context.expectedCollectionId) {
            return {...failure("审核记录或页面采集已变化，请重新读取页面后搜索"), code: "PAGE_IDENTITY_CHANGED"};
          }
          const candidate = session.candidates?.find(item => item.modelId === message.modelId);
          if (!candidate) return failure("所选车型不属于当前查询，请重新选择");
          return {ok: true, input: session.input, candidate};
        });
      }
      return failure("不支持的车型操作");
    } catch (error) {
      return {...failure(vehicleModelMessage(error)), ...(error instanceof ModelPageError ? {code: error.code} : {})};
    }
  };
}
