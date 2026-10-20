import type { PageFillResult } from "./page-fill";

export interface ModelPageScope { brandName: string; seriesName: string; typeLevel: string; }
export interface ModelSearchInput { announcementModel: string; engineModel: string; pageScope: ModelPageScope; }
export interface VehicleModelCandidate {
  modelId: string;
  modelName: string;
  brandId: string;
  brandName: string;
  seriesId: string;
  seriesName: string;
  typeLevel: string;
  sameNameCount: number;
  config: Record<string, unknown>;
  warnings: string[];
}
export interface ModelSearchResult {
  ok: boolean;
  message: string;
  code?: string;
  searchId?: string;
  candidates: VehicleModelCandidate[];
}
export interface ModelFillResult extends PageFillResult { warnings?: string[]; }
export interface SpecFillValue { field: string; label: string; value: string; optionValue?: string; }
