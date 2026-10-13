import {useParams} from 'react-router-dom';
import type {ApiError, RiskAlertSummary} from './api';
import {CurrentRisksView} from './components/CurrentRisksView';
import {RiskDetailView} from './components/RiskDetailView';
import type {RiskItem} from './types';

interface RiskRouteViewProps {
  readonly riskItems: readonly RiskItem[];
  /** 当前提醒全量计数；null 表示汇总不可用，概览卡回落已加载页计数。 */
  readonly summary: RiskAlertSummary | null;
  readonly onAskAssistant: (query: string) => void;
  readonly onCloseDetail: () => void;
  readonly onExportReport: (risk: RiskItem) => void;
  readonly onSelectRisk: (risk: RiskItem) => void;
  readonly onRequestError: (error: ApiError) => void;
}

export const RiskRouteView = ({riskItems, summary, onAskAssistant, onCloseDetail, onExportReport, onSelectRisk, onRequestError}: RiskRouteViewProps) => {
  const {alertId} = useParams();
  if (alertId !== undefined) {
    return <RiskDetailView alertId={alertId} onAskAssistant={onAskAssistant} onClose={onCloseDetail} onExportReport={onExportReport} onRequestError={onRequestError} />;
  }

  return <CurrentRisksView riskItems={[...riskItems]} summary={summary} onSelectRisk={onSelectRisk} />;
};
