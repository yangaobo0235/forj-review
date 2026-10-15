import type { BusinessSelection } from "../types/review";
import type { PageAdapter } from "./pageAdapter.ts";

/**
 * 一致性审核页面的适配器。
 *
 * **它不再自己判定页面。** 一致性审核与过户审核共用地址
 * （`/consistency-qingdao`、`/consistency-changchun`），要靠列表页的状态筛选
 * 进到各自的页面，URL 完全一样——只看地址必然认错，而认错不会报错，只会拿
 * 某个业务的规则去审另一个业务的单子。
 *
 * 因此识别交给后端下发的页面识别清单（`browser/page-catalog.ts`）。这里只保留
 * 适配器身份，避免在前端重复维护页面规则。
 */
export class ConsistencyPageAdapter implements PageAdapter {
  public readonly id = "consistency";

  public detect(): BusinessSelection | null {
    return null;
  }
}
