import type { PageFillResult } from "./page-fill";

export interface ModelSelection {id: string; label: string;}
export interface ModelPageSelection {brand: ModelSelection; series: ModelSelection; model: ModelSelection; vehicleType: string;}
export interface ModelPageScope { brandId: string; brandName: string; seriesId: string; seriesName: string; typeLevel: string; }
export interface ModelScopeResult {ok: boolean; pageScope?: ModelPageScope; code?: string; message?: string;}
export interface ModelSearchInput { announcementModel: string; engineModel: string; pageScope: ModelPageScope; }
export interface VehicleModelCandidate {
  modelId: string;
  modelName: string;
  config: {
    driveForm: string | null; fuelType: string | null; emissionStandard: string | null;
    maxHp: string | null; engineBrand: string | null; engineType: string | null;
  };
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
