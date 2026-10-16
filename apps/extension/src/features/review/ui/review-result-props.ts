import type { PageData, ReviewJobSnapshot, ReviewResponse } from "../../../shared/contracts/review";
import type { ExceptionFilter } from "../presentation/exception";
import type { WorkbenchRendererProps } from "./workbench-renderers";
export interface ReviewResultsProps extends WorkbenchRendererProps {
  review: ReviewResponse;
  job: ReviewJobSnapshot | null;
  pageData: PageData | null;
  exceptionFilter: ExceptionFilter;
  onExceptionFilterChange: (filter: ExceptionFilter) => void;
}
