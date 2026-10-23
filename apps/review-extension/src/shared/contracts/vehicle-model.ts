import type { PageFillResult } from "./page-fill";

export interface ModelSelection {
  id: string;
  label: string;
}
export interface ModelPageSelection {
  brand: ModelSelection;
  series: ModelSelection;
  model: ModelSelection;
  vehicleType: string;
}
export interface ModelPageScope {
  brandId: string;
  brandName: string;
  seriesId: string;
  seriesName: string;
  typeLevel: string;
}
export interface ModelScopeResult {
  ok: boolean;
  pageScope?: ModelPageScope;
  code?: string;
  message?: string;
}
export interface ModelSearchInput {
  announcementModel: string;
  engineModel: string;
  pageScope: ModelPageScope;
}
export interface VehicleModelCandidate {
  modelId: string;
  modelName: string;
}
export interface ModelSearchResult {
  ok: boolean;
  message: string;
  code?: string;
  searchId?: string;
  candidates: VehicleModelCandidate[];
}
export type ModelFillResult = PageFillResult;
export interface ModelFillValue {
  field: string;
  label: string;
  value: string;
  optionValue?: string;
}
