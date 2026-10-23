import { validatedModelInput } from "../../shared/vehicle-model.ts";
import { vehicleModelMessage } from "./vehicle-model-client.ts";
import { ModelPageError } from "./vehicle-model-page.ts";
import type { VerifyModelPage } from "./vehicle-model-page.ts";
import type {
  ModelSearchInput,
  VehicleModelCandidate,
} from "../../shared/contracts/vehicle-model.ts";

interface OptionTarget {
  id: string;
  label: string;
  prepare: boolean;
  resolveLabel?: boolean;
}
interface Message {
  type: string;
  expectedPageUrl?: string;
  input?: ModelSearchInput;
  token?: string;
  target?: OptionTarget;
}

/** 配置复核与精确选项定位共用同一份来源校验，避免修完第一关又被下一关拦截。 */
export function createModelBridgeHandler(
  verifyPage: VerifyModelPage,
  query: (
    input: ModelSearchInput,
  ) => Promise<{ ok: boolean; message?: string; candidates?: VehicleModelCandidate[] }>,
  inspect: (
    tabId: number,
    sender: chrome.runtime.MessageSender,
    token: string,
    pageUrl: string,
    target?: OptionTarget,
  ) => Promise<unknown>,
) {
  return async (message: Message, sender: chrome.runtime.MessageSender) => {
    try {
      const tabId = await verifyPage(sender, message.expectedPageUrl || "");
      if (message.type === "QUERY_VEHICLE_MODEL_CATALOG")
        return await query(validatedModelInput(message.input!));
      if (message.type !== "INSPECT_REVIEW_SELECT") throw new Error("不支持的车型操作");
      if (
        typeof message.token !== "string" ||
        !/^[a-zA-Z0-9-]{1,100}$/.test(message.token) ||
        (message.target &&
          (!/^\d+$/.test(message.target.id) ||
            typeof message.target.label !== "string" ||
            message.target.label.length > 1000 ||
            typeof message.target.prepare !== "boolean" ||
            (message.target.resolveLabel !== undefined &&
              typeof message.target.resolveLabel !== "boolean")))
      ) {
        throw new Error("车型控件定位参数无效，请重新读取页面");
      }
      return await inspect(tabId, sender, message.token, message.expectedPageUrl!, message.target);
    } catch (error) {
      return {
        ok: false,
        message: vehicleModelMessage(error),
        ...(error instanceof ModelPageError ? { code: error.code } : {}),
      };
    }
  };
}
